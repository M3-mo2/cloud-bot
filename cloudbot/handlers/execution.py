"""Execution handlers: run mode, exec, background jobs, live logs, history."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.keyboards.callbacks import cb as _cb, decode_required
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import run as run_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents
from cloudbot.states.input import Input

router = Router(name="execution")

#: The command behind the most recent result, per (user, workspace), so a
#: "try again" button repeats **exactly** what failed. Bounded and process-local:
#: after a restart the button is gone with the screen, and a tap on a stale one
#: falls back to the prompt (a correctable screen, never a guessed command).
_LAST_EXEC: "dict[tuple[int, str], str]" = {}
_MAX_LAST_EXEC = 64

#: The last exec result itself, so "back" can rebuild its result screen without
#: re-running anything. Bounded and process-local, like ``_LAST_EXEC``.
_LAST_EXEC_RESULT: "dict[tuple[int, str], object]" = {}


def remember_exec(tg_id: int, ws_id, command: str) -> None:
    if not command:
        return
    if len(_LAST_EXEC) >= _MAX_LAST_EXEC:
        _LAST_EXEC.pop(next(iter(_LAST_EXEC)), None)
    _LAST_EXEC[(tg_id, str(ws_id))] = command


def last_exec(tg_id: int, ws_id) -> str | None:
    return _LAST_EXEC.get((tg_id, str(ws_id)))


def remember_exec_result(tg_id: int, ws_id, result) -> None:
    """Keep the last exec result so the run-result screen can be rebuilt."""
    key = (tg_id, str(ws_id))
    if key not in _LAST_EXEC_RESULT and len(_LAST_EXEC_RESULT) >= _MAX_LAST_EXEC:
        _LAST_EXEC_RESULT.pop(next(iter(_LAST_EXEC_RESULT)), None)
    _LAST_EXEC_RESULT[key] = result


def last_exec_result(tg_id: int, ws_id):
    return _LAST_EXEC_RESULT.get((tg_id, str(ws_id)))


#: Where the user's last screen was, per (user, workspace): the destination the
#: install result's back button returns to.
#:
#: **Process memory on purpose.** This describes where this user's last screen was
#: — presentation state, not durable state. Losing it on a restart must degrade to
#: "no back button" (a correctable screen with ``pkg:list`` as its exit), never to
#: a guessed or wrong destination. It is bounded so a long-lived process cannot
#: grow it without limit.
_ORIGINS: "dict[tuple[int, str], tuple[str, str | None]]" = {}
_MAX_ORIGINS = 64
ORIGIN_KINDS = ("run", "runlog", "terminal", "packages")


def remember_origin(tg_id: int, ws_id, kind: str, ref: str | None = None) -> None:
    """Record the screen the user is on, for the install result's back button.

    ``kind`` is one of :data:`ORIGIN_KINDS`; ``ref`` is the run or session id for
    ``runlog``/``terminal``. Recorded at render time (by the surface itself), never
    re-derived from a callback: by tap time the button's own callback is all the
    handler has, and it must not have to guess which run or session it came from.
    """
    if kind not in ORIGIN_KINDS:
        raise ValueError(f"unknown origin kind: {kind!r}")
    key = (tg_id, str(ws_id))
    if key not in _ORIGINS and len(_ORIGINS) >= _MAX_ORIGINS:
        _ORIGINS.pop(next(iter(_ORIGINS)), None)
    _ORIGINS[key] = (kind, None if ref is None else str(ref))


def last_origin(tg_id: int, ws_id) -> tuple[str, str | None] | None:
    return _ORIGINS.get((tg_id, str(ws_id)))


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


# -- run mode chooser --------------------------------------------------------
@router.callback_query(F.data.startswith("run:choose"))
async def on_choose(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 1)
    ws_id = _wid(decoded.key[0])
    ws = await services.workspaces.get(ctx.tg_id, ws_id)
    page = await services.files.list(ctx.tg_id, ws_id, "/")
    await show_screen(cb, run_screens.run_mode(ws, ctx.locale, has_files=bool(page.entries)), ctx=ctx, sender=sender)


# -- exec --------------------------------------------------------------------
@router.callback_query(F.data.startswith("exec:prompt"))
async def on_exec_prompt(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    await state.set_state(Input.waiting_exec_command)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, run_screens.exec_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


async def _run_exec_and_show(
    event,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    events: RequestEvents,
    ws_id,
    command: str,
) -> None:
    """Run one command and render its result, with the missing-package offer.

    The offer is built from the traceback's own module names (never invented),
    and the retry button re-runs this same ``command`` on one tap. Nothing here
    installs or re-runs on its own: a tap on an offer button is the only trigger.
    """
    from cloudbot.presentation.screens import packages as pkg_screens

    result = await services.execution.exec(ctx.tg_id, ws_id, command)
    if result.exit_code != 0:
        events.run_failed(tg_id=ctx.tg_id, ws_id=str(ws_id), name=command)
    # Where the install offer's back button must return: this result screen.
    remember_origin(ctx.tg_id, ws_id, "run")
    remember_exec_result(ctx.tg_id, ws_id, result)
    offer = pkg_screens.missing_section(result.stderr or "", ws_id, ctx.locale)
    again = _cb("exec:again", ws_id) if result.exit_code != 0 else None
    await show_screen(
        event,
        run_screens.exec_result(
            result, ws_id, ctx.locale, missing_offer=offer, again_action=again
        ),
        ctx=ctx,
        sender=sender,
        force_send=isinstance(event, Message),
    )

@router.message(Input.waiting_exec_command)
async def on_exec_command(
    message: Message,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    data = await state.get_data()
    ws_id = _wid(data["ws_id"])
    command = (message.text or "").strip()
    await state.clear()
    # Remember what was run so the result screen's "try again" repeats exactly
    # this command, on the workspace it ran on.
    remember_exec(ctx.tg_id, ws_id, command)
    await _run_exec_and_show(message, ctx, services, sender, events, ws_id, command)

@router.callback_query(F.data.startswith("exec:again"))
async def on_exec_again(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    """Re-run the exact command that failed — only when the user taps it."""
    decoded = decode_required(cb.data, 1)
    ws_id = _wid(decoded.key[0])
    command = last_exec(ctx.tg_id, ws_id)
    if not command:
        # Nothing to repeat (a restart, or a button from another workspace):
        # ask for the command again instead of guessing one.
        await state.set_state(Input.waiting_exec_command)
        await state.update_data(ws_id=str(ws_id))
        await show_screen(
            cb, run_screens.exec_prompt(ws_id, ctx.locale), ctx=ctx, sender=sender
        )
        return
    await _run_exec_and_show(cb, ctx, services, sender, events, ws_id, command)


# -- background job ----------------------------------------------------------
@router.callback_query(F.data.startswith("run:prompt"))
async def on_job_prompt(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    await state.set_state(Input.waiting_job_command)
    await state.update_data(ws_id=ws_id, file_mode=False)
    await show_screen(cb, run_screens.job_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:pick_file"))
async def on_pick_file(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 1)
    ws_id = _wid(decoded.key[0])
    page = await services.files.list(ctx.tg_id, ws_id, "/")
    files = [e for e in page.entries if e.type == "file"]
    await show_screen(cb, run_screens.file_picker(files, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:file"))
async def on_run_file(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode_required(cb.data, 2)
    ws_id = decoded.key[0]
    path = decoded.key[1]
    await state.set_state(Input.waiting_job_args)
    await state.update_data(ws_id=ws_id, file_mode=True, file_path=path)
    await show_screen(cb, run_screens.job_prompt(_wid(ws_id), ctx.locale, file_path=path), ctx=ctx, sender=sender)


@router.message(Input.waiting_job_command)
async def on_job_command(
    message: Message,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    data = await state.get_data()
    ws_id = _wid(data["ws_id"])
    command = (message.text or "").strip()
    await state.clear()
    run = await services.execution.run_command(ctx.tg_id, ws_id, command.split())
    events.run_started(tg_id=ctx.tg_id, ws_id=str(ws_id), name=command)
    await show_screen(message, run_screens.run_started(run, ctx.locale), ctx=ctx, sender=sender, force_send=True)


@router.message(Input.waiting_job_args)
async def on_job_args(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    ws_id = _wid(data["ws_id"])
    path = data["file_path"]
    args = (message.text or "").split()
    await state.clear()
    run = await services.execution.run_file(ctx.tg_id, ws_id, path, args=args or None)
    await show_screen(message, run_screens.run_started(run, ctx.locale), ctx=ctx, sender=sender, force_send=True)


# -- live logs (draft streaming + offset protocol) ---------------------------
@router.callback_query(F.data.startswith("run:refresh"))
@router.callback_query(F.data.startswith("run:logs"))
async def on_logs(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 2)
    ws_id = _wid(decoded.key[0])
    run_id = decoded.key[1]
    await _stream_logs(cb, ctx, services, sender, ws_id, run_id, update_only="run:refresh" in decoded.action)


async def _stream_logs(cb, ctx, services, sender, ws_id, run_id, *, update_only: bool) -> None:
    # One bounded read for the in-place screen (the draft loop is driven by the
    # tail service; here we render the current state and persist it).
    state = await services.execution.read_all_logs(ctx.tg_id, ws_id, run_id)
    run = await services.execution.get_run(ctx.tg_id, ws_id, run_id)
    # A file run's live logs is a screen the install offer can appear on; "back"
    # must return to this exact run's logs.
    remember_origin(ctx.tg_id, ws_id, "runlog", str(run.id))
    screen = run_screens.live_logs(
        run, ctx.locale, stdout=state.stdout, stderr=state.stderr, truncated=state.truncated
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:stop_confirm"))
async def on_stop_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 2)
    ws_id = _wid(decoded.key[0])
    run = await services.execution.get_run(ctx.tg_id, ws_id, decoded.key[1])
    await show_screen(cb, run_screens.stop_confirm(run, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:stop"))
async def on_stop(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 2)
    ws_id = _wid(decoded.key[0])
    run_id = decoded.key[1]
    await services.execution.stop_run(ctx.tg_id, ws_id, run_id)
    state = await services.execution.read_all_logs(ctx.tg_id, ws_id, run_id)
    run = await services.execution.get_run(ctx.tg_id, ws_id, run_id)
    await show_screen(
        cb,
        run_screens.live_logs(run, ctx.locale, stdout=state.stdout, stderr=state.stderr),
        ctx=ctx,
        sender=sender,
    )


@router.callback_query(F.data.startswith("run:delete"))
async def on_delete_run(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 2)
    ws_id = _wid(decoded.key[0])
    await services.execution.delete_run(ctx.tg_id, ws_id, decoded.key[1])
    page = await services.execution.list_runs(ctx.tg_id, ws_id)
    await show_screen(cb, history_screens.run_history(page, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- history -----------------------------------------------------------------
def _history_page(key: tuple) -> tuple[str, str | None, int]:
    """``(ws_id, status_filter, page)`` from a history callback's key parts.

    The filter is carried inline so a page turn keeps it, and the page is the
    last part; both are optional (buttons drawn before paging existed carry
    neither and still render the first page of all runs).
    """
    ws_id = key[0]
    status: str | None = None
    page = 0
    known = {value for _, value in history_screens.FILTERS if value}
    if len(key) >= 2:
        if key[1] == "all":
            page = _page_of(key[2]) if len(key) >= 3 else 0
        elif key[1] in known:
            status = key[1]
            if len(key) >= 3:
                page = _page_of(key[2])
        else:
            page = _page_of(key[1])
    return ws_id, status, page

def _page_of(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0

async def _show_history(
    cb, ctx: UserContext, services: Services, sender: RichSender, ws_id, status: str | None, page: int
) -> None:
    from cloudbot.presentation.pagination import PAGE_SIZE

    run_page = await services.execution.list_runs(
        ctx.tg_id,
        _wid(ws_id),
        status=status,
        offset=page * PAGE_SIZE,
    )
    await show_screen(
        cb,
        history_screens.run_history(
            run_page, _wid(ws_id), ctx.locale, active_filter=status, page_index=page
        ),
        ctx=ctx,
        sender=sender,
    )

@router.callback_query(F.data.startswith("run:history"))
async def on_history(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id, status, page = _history_page(decode_required(cb.data, 1).key)
    await _show_history(cb, ctx, services, sender, ws_id, status, page)

@router.callback_query(F.data.startswith("run:filter"))
async def on_filter(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id, status, page = _history_page(decode_required(cb.data, 2).key)
    await _show_history(cb, ctx, services, sender, ws_id, status, page)

@router.callback_query(F.data.startswith("run:purge_confirm"))
async def on_purge_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode_required(cb.data, 1).key[0])
    from cloudbot.core.errors import EngineRefused

    try:
        await services.execution.purge(ctx.tg_id, ws_id)
    except EngineRefused as exc:
        if exc.engine_code == "conflict":
            await show_screen(cb, history_screens.purge_conflict(ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        raise
    page = await services.execution.list_runs(ctx.tg_id, ws_id)
    await show_screen(cb, history_screens.run_history(page, ws_id, ctx.locale), ctx=ctx, sender=sender)
