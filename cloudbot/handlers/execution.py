"""Execution handlers: run mode, exec, background jobs, live logs, history."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import run as run_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents
from cloudbot.states.input import Input

router = Router(name="execution")


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


# -- run mode chooser --------------------------------------------------------
@router.callback_query(F.data.startswith("run:choose"))
async def on_choose(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    ws = await services.workspaces.get(ctx.tg_id, ws_id)
    page = await services.files.list(ctx.tg_id, ws_id, "/")
    await show_screen(cb, run_screens.run_mode(ws, ctx.locale, has_files=bool(page.entries)), ctx=ctx, sender=sender)


# -- exec --------------------------------------------------------------------
@router.callback_query(F.data.startswith("exec:prompt"))
async def on_exec_prompt(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(Input.waiting_exec_command)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, run_screens.exec_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


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
    result = await services.execution.exec(ctx.tg_id, ws_id, command)
    if result.exit_code != 0:
        events.run_failed(tg_id=ctx.tg_id, ws_id=str(ws_id), name=command)
    await show_screen(message, run_screens.exec_result(result, ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=True)


# -- background job ----------------------------------------------------------
@router.callback_query(F.data.startswith("run:prompt"))
async def on_job_prompt(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(Input.waiting_job_command)
    await state.update_data(ws_id=ws_id, file_mode=False)
    await show_screen(cb, run_screens.job_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:pick_file"))
async def on_pick_file(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    page = await services.files.list(ctx.tg_id, ws_id, "/")
    files = [e for e in page.entries if e.type == "file"]
    await show_screen(cb, run_screens.file_picker(files, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:file"))
async def on_run_file(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
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
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    run_id = decoded.key[1]
    await _stream_logs(cb, ctx, services, sender, ws_id, run_id, update_only="run:refresh" in decoded.action)


async def _stream_logs(cb, ctx, services, sender, ws_id, run_id, *, update_only: bool) -> None:
    # One bounded read for the in-place screen (the draft loop is driven by the
    # tail service; here we render the current state and persist it).
    state = await services.execution.read_all_logs(ctx.tg_id, ws_id, run_id)
    run = await services.execution.get_run(ctx.tg_id, ws_id, run_id)
    screen = run_screens.live_logs(
        run, ctx.locale, stdout=state.stdout, stderr=state.stderr, truncated=state.truncated
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:stop_confirm"))
async def on_stop_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    run = await services.execution.get_run(ctx.tg_id, ws_id, decoded.key[1])
    await show_screen(cb, run_screens.stop_confirm(run, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:stop"))
async def on_stop(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
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
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    await services.execution.delete_run(ctx.tg_id, ws_id, decoded.key[1])
    page = await services.execution.list_runs(ctx.tg_id, ws_id)
    await show_screen(cb, history_screens.run_history(page, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- history -----------------------------------------------------------------
@router.callback_query(F.data.startswith("run:history"))
async def on_history(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    page = await services.execution.list_runs(ctx.tg_id, ws_id)
    await show_screen(cb, history_screens.run_history(page, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("run:filter"))
async def on_filter(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    status = decoded.key[1]
    page = await services.execution.list_runs(ctx.tg_id, ws_id, status=None if status == "all" else status)
    await show_screen(
        cb, history_screens.run_history(page, ws_id, ctx.locale, active_filter=None if status == "all" else status),
        ctx=ctx, sender=sender,
    )


@router.callback_query(F.data.startswith("run:purge_confirm"))
async def on_purge_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
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
