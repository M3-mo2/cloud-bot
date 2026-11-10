"""Terminal handlers (Phase 1.5): open-or-reuse, the view, input, signals.

Thin aiogram routers: parse → call the ``sessions`` service → render one screen
→ send. The live view follows the engine's offset protocol through the service
helper; it is bounded and paced, and it never builds a FIFO or a run-based PTY.

**Sticky input.** Entering the terminal's command mode is sticky: after the user
taps *Send a command* once, plain text messages keep going to that session until
they explicitly stop (the ``term:exit`` control) or a known slash-command leaves
the mode. A known command is cleared by ``TerminalStickyMiddleware`` before the
routers run, so a sticky state can never swallow ``/start``, ``/help``,
``/settings`` or ``/admin``.

**R7 — cut short on idle, supersede on a new command.** A follow stops once the
session goes quiet (no new output for ``FOLLOW_IDLE_INTERVALS`` polls) instead of
pacing over an idle PTY for the whole budget. A *new* command while a follow is
still running **supersedes** the in-flight follow: the older follower notices at
its next tick that it is no longer current and stops before its next engine read,
so two commands never fight over one message and the engine is not polled by both.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import EngineRefused, InvalidInput, NotOwned
from cloudbot.domain.value_objects import SessionStatus
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import terminal_callbacks as _term
from cloudbot.services.bundle import Services
from cloudbot.services.sessions import CTRL_D
from cloudbot.states.terminal_input import TerminalInput

log = structlog.get_logger("cloudbot.terminal")

router = Router(name="terminal")

#: Live-follow tuning: bounded and gentle on the engine (same shape as run logs).
FOLLOW_MAX_ITERATIONS = 30
FOLLOW_POLL_INTERVAL = 1.0

#: R7 — the follow is cut short once the session is **idle**: this many consecutive
#: polls with no new output mean the command the user just sent has produced all it
#: is going to for now, so the follower stops instead of burning the whole iteration
#: budget (measured live: a full 30 × 1 s pass per command). A newer plain message
#: is a fresh command and claims a new generation, superseding any follower still in
#: flight, so a long-running command's view is never displaced — it is replaced,
#: which is exactly what typing the next command means.
FOLLOW_IDLE_INTERVALS = 2

#: The generation of the active follow per session. A newer command for the same
#: session raises this number; the older follower stops at its next tick (R7).
_follow_generation: dict[tuple[int, str, str], int] = {}


def _begin_follow(key: tuple[int, str, str]) -> int:
    generation = _follow_generation.get(key, 0) + 1
    _follow_generation[key] = generation
    return generation


def _follow_is_current(key: tuple[int, str, str], generation: int) -> bool:
    return _follow_generation.get(key) == generation


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


def _is_command(text: str) -> bool:
    """A slash-command is never swallowed as terminal input (D5/R4)."""
    return text.startswith("/")


async def _parse_callback(cb: CallbackQuery, ctx: UserContext, sender: RichSender):
    """Parse a ``term:`` callback through the total codec; render the stale screen
    and return ``None`` when it is stale or malformed (never index a lost key)."""
    parsed = _term.parse(cb.data)
    if not parsed.valid:
        await show_screen(cb, term_screens.stale_screen(ctx.locale), ctx=ctx, sender=sender)
        return None
    return parsed


async def _is_typing_here(state: FSMContext | None, ws_id, ses_id: str) -> bool:
    """True when the sticky command state points at this exact session (R2)."""
    if state is None:
        return False
    if await state.get_state() != TerminalInput.awaiting_command.state:
        return False
    data = await state.get_data()
    return str(data.get("ws_id")) == str(ws_id) and data.get("ses_id") == ses_id


# -- open-or-reuse (the workspace home's 🖥 button) ---------------------------
@router.callback_query(F.data.startswith("term:open"))
async def on_open(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext) -> None:
    """One tap from the workspace home lands on a working prompt.

    **Open-or-reuse**: a live session of this user in this workspace is shown;
    otherwise a new one is created and shown. There is no intermediate screen —
    the archive of dead sessions that used to sit here is deleted, not hidden.
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    size = _open_size(parsed)
    try:
        session = await services.sessions.open_or_reuse(ctx.tg_id, ws_id, **size)
    except EngineRefused as exc:
        if exc.engine_code != "too_many_sessions":
            raise
        await _show_cap(cb, ctx, services, sender, ws_id)
        return
    await _show_view(cb, ctx, services, sender, ws_id, session.id, state=state)


@router.callback_query(F.data.startswith("term:new"))
async def on_new(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext) -> None:
    """Open a **fresh** session, never reusing a live one.

    The only producer is a dead session's own view (``➕ New terminal``): a session
    that ended can never run again, so the user needs a new one there. ``term:open``
    is the workspace button and is open-or-reuse instead.
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    await state.clear()
    try:
        session = await services.sessions.open(ctx.tg_id, ws_id)
    except EngineRefused as exc:
        if exc.engine_code != "too_many_sessions":
            raise
        await _show_cap(cb, ctx, services, sender, ws_id)
        return
    await _show_view(cb, ctx, services, sender, ws_id, session.id, state=state)


async def _show_cap(cb, ctx, services, sender, ws_id) -> None:
    """The documented cap screen: name what to close and give the stop control.

    It lists only sessions that are **live** — a stop button has to be reachable
    to free a slot — never the archive of ended ones this task deleted.
    """
    sessions = await services.sessions.list_for_workspace(ctx.tg_id, ws_id)
    live = [s for s in sessions if s.status is SessionStatus.RUNNING]
    screen = term_screens.cap_screen(live, ws_id, ctx.locale)
    await show_screen(cb, screen, ctx=ctx, sender=sender)


def _open_size(parsed) -> dict[str, int]:
    """The optional explicit geometry carried by ``term:open``.

    Absent (the default form every screen emits) → the service's own default is
    used unchanged. Present → ``--cols/--rows`` from the documented power-user
    route. Invalid values raise ``InvalidInput`` (a correctable screen).
    """
    if len(parsed.args) < 3:
        return {}
    cols, rows = _parse_size(f"{parsed.args[1]}x{parsed.args[2]}")
    return {"cols": cols, "rows": rows}


# -- view / refresh ----------------------------------------------------------
@router.callback_query(F.data.startswith("term:refresh"))
@router.callback_query(F.data.startswith("term:view"))
async def on_view(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    await _show_view(cb, ctx, services, sender, ws_id, ses_id, state=state)


async def _show_view(cb, ctx, services, sender, ws_id, ses_id, *, state: FSMContext | None = None) -> None:
    session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
    meta = await services.sessions.meta()
    if session.status.is_terminal and state is not None:
        # The terminal ended under a sticky state: clear it so the mode does not
        # outlive the session it pointed at (R5).
        await state.clear()
    typing = await _is_typing_here(state, ws_id, ses_id)
    output = ""
    truncated = False
    if session.status.value != "unknown":
        try:
            read = await services.sessions.read_output(ctx.tg_id, ws_id, ses_id, 0)
            output, truncated = read.data, read.truncated
        except EngineRefused as exc:
            if exc.engine_code != "session_ended":
                raise
    # The live terminal is a screen the install offer can appear on; "back" from
    # the install result must return to this exact session's view.
    from cloudbot.handlers.execution import remember_origin
    from cloudbot.handlers.package_offer import package_names

    remember_origin(ctx.tg_id, ws_id, "terminal", str(session.id))
    # Answer the offer with facts: the workspace's current package list, so a
    # package that is already installed is never offered again from old output.
    installed = await package_names(services, ctx.tg_id, ws_id)
    screen = term_screens.terminal_view(
        session,
        ws_id,
        ctx.locale,
        output=output,
        truncated=truncated,
        meta=meta,
        typing=typing,
        installed=installed,
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


# -- send a command (sticky) -------------------------------------------------
@router.callback_query(F.data.startswith("term:cmd"))
async def on_cmd(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
) -> None:
    """Enter sticky command mode and show the live view carrying the exit.

    The screen is the terminal view itself (in place), not a separate prompt, so
    the sticky state is always paired with the terminal buttons and the one-tap
    **stop typing** control (R2/R3). Plain text messages then keep going to this
    session until the user stops or a known slash-command takes over (R1/R4).
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = parsed.ws_id
    ses_id = parsed.ses_id
    await state.set_state(TerminalInput.awaiting_command)
    await state.update_data(ws_id=str(ws_id), ses_id=str(ses_id))
    await _show_view(cb, ctx, services, sender, _wid(ws_id), ses_id, state=state)


# -- stop typing (the one-tap exit; R3) --------------------------------------
@router.callback_query(F.data.startswith("term:exit"))
async def on_exit(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
) -> None:
    """Clear the sticky state and return to the ordinary terminal view."""
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = parsed.ws_id
    ses_id = parsed.ses_id
    await state.clear()
    await _show_view(cb, ctx, services, sender, _wid(ws_id), ses_id, state=state)


@router.message(TerminalInput.awaiting_command)
async def on_command_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    text = message.text or ""
    data = await state.get_data()
    ws_id = _wid(data["ws_id"])
    ses_id = data["ses_id"]
    if _is_command(text):
        # A slash-command is never terminal input. *Known* commands are handled by
        # an earlier router (``TerminalStickyMiddleware`` cleared the state first
        # so the command does its own thing, R4); an unknown one lands here and
        # simply re-renders the terminal so the user stays focused with the
        # buttons available — the sticky state is kept.
        log.info("terminal.command_not_input", command=text.split()[0])
        await state.set_state(TerminalInput.awaiting_command)
        await state.update_data(ws_id=str(ws_id), ses_id=ses_id)
        await _show_view(message, ctx, services, sender, ws_id, ses_id, state=state)
        return
    try:
        await services.sessions.send_line(ctx.tg_id, ws_id, ses_id, text)
    except NotOwned:
        # The state must never be a way to reach someone else's session (R5):
        # drop it and let the error guard render the safe "not found" board.
        await state.clear()
        raise
    except EngineRefused as exc:
        # An ended — or engine-forgotten — session renders the ended screen and
        # clears the state rather than raising (R5).
        if exc.engine_code not in ("session_ended", "not_found"):
            raise
        await state.clear()
        session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        await show_screen(
            message,
            term_screens.ended_screen(session, ws_id, ctx.locale),
            ctx=ctx,
            sender=sender,
            force_send=True,
        )
        return
    # Sticky (R1): keep the same state so the very next plain message is another
    # command — no re-tap per command.
    await state.set_state(TerminalInput.awaiting_command)
    await state.update_data(ws_id=str(ws_id), ses_id=ses_id)
    await _follow(message, ctx, services, sender, ws_id, ses_id, force_send=True, typing=True)


async def _read_all(services: Services, tg_id: int, ws_id, ses_id) -> tuple[str, bool, int]:
    try:
        read = await services.sessions.read_output(tg_id, ws_id, ses_id, 0)
        return read.data, read.truncated, read.next
    except EngineRefused as exc:
        if exc.engine_code != "session_ended":
            raise
        return "", False, 0


async def _stream(
    event,
    ctx,
    services: Services,
    sender: RichSender,
    ws_id,
    ses_id,
    *,
    force_send: bool = False,
    typing: bool = False,
    generation: int | None = None,
) -> None:
    """Follow the offset cursor, updating one message in place until terminal.

    Bounded by ``FOLLOW_MAX_ITERATIONS`` and paced by ``FOLLOW_POLL_INTERVAL`` so
    the engine is never hammered (the same shape as the run-log follower).

    **R7 — cut short on idle, supersede on a new command.** Each iteration checks
    two things first. (1) If this follow is no longer the current one for the
    session, a newer command has claimed the session's generation: stop *before*
    the next engine read, so two followers never poll at once. (2) If the session
    has produced no new output for ``FOLLOW_IDLE_INTERVALS`` consecutive polls, the
    command is quiescent: stop, rather than spend the whole iteration budget pacing
    over an idle PTY (measured live at 30 s per command). Both are checked before
    the read, so the engine is polled at most once per tick and never unnecessarily.
    """
    import asyncio

    from cloudbot.handlers.package_offer import package_names

    key = (ctx.tg_id, str(ws_id), ses_id)
    meta = await services.sessions.meta()
    # The workspace's package list is fetched lazily, after the first generation
    # check: a follower that a newer command already superseded must not spend an
    # engine read on a render it is about to abandon. One fetch serves the whole
    # follow — an install cannot run mid-stream, so the list cannot change.
    installed: frozenset[str] | None = None
    installed_fetched = False
    first = True
    chat_id = None
    message_id = None
    last_next: int | None = None
    idle_ticks = 0
    for _ in range(FOLLOW_MAX_ITERATIONS):
        if generation is not None and not _follow_is_current(key, generation):
            log.info("terminal.follow_superseded", ws_id=str(ws_id), ses_id=ses_id)
            return
        if not installed_fetched:
            installed = await package_names(services, ctx.tg_id, ws_id)
            installed_fetched = True
        session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        output, truncated, next_offset = await _read_all(services, ctx.tg_id, ws_id, ses_id)
        if not session.status.is_terminal:
            if last_next is not None and next_offset == last_next:
                idle_ticks += 1
                if idle_ticks >= FOLLOW_IDLE_INTERVALS:
                    log.info("terminal.follow_idle_stop", ws_id=str(ws_id), ses_id=ses_id)
                    return
            else:
                idle_ticks = 0
            last_next = next_offset
        screen = term_screens.terminal_view(
            session,
            ws_id,
            ctx.locale,
            output=output,
            truncated=truncated,
            meta=meta,
            typing=typing,
            installed=installed,
        )
        if first:
            if isinstance(event, CallbackQuery) and event.message is not None and not force_send:
                chat_id = event.message.chat.id
                message_id = event.message.message_id
                await sender.answer_callback(event)
                await sender.edit(chat_id, message_id, screen.doc, markup=screen.markup)
            else:
                chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
                if isinstance(event, CallbackQuery):
                    await sender.answer_callback(event)
                sent = await sender.send(chat_id, screen.doc, markup=screen.markup)
                message_id = sent.message_id
            first = False
        elif chat_id is not None and message_id is not None:
            await sender.edit(chat_id, message_id, screen.doc, markup=screen.markup)
        if session.status.is_terminal:
            break
        await asyncio.sleep(FOLLOW_POLL_INTERVAL)


async def _follow(
    event, ctx, services, sender, ws_id, ses_id, *, force_send: bool = False, typing: bool = False
) -> None:
    """Stream output until a terminal status, bounded and paced.

    Starting a follow claims this session's generation, superseding any follower
    still in flight for the same session (R7).
    """
    key = (ctx.tg_id, str(ws_id), ses_id)
    generation = _begin_follow(key)
    await _stream(
        event,
        ctx,
        services,
        sender,
        ws_id,
        ses_id,
        force_send=force_send,
        typing=typing,
        generation=generation,
    )


# -- signals -----------------------------------------------------------------
@router.callback_query(F.data.startswith("term:signal"))
async def on_signal(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    signal = parsed.args[2]
    try:
        await services.sessions.signal(ctx.tg_id, ws_id, ses_id, signal)
    except EngineRefused as exc:
        if exc.engine_code == "session_ended":
            await state.clear()
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
            await show_screen(cb, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        raise
    await _show_view(cb, ctx, services, sender, ws_id, ses_id, state=state)


@router.callback_query(F.data.startswith("term:ctrl_d"))
async def on_ctrl_d(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    try:
        await services.sessions.send_input(ctx.tg_id, ws_id, ses_id, CTRL_D)
    except EngineRefused as exc:
        if exc.engine_code == "session_ended":
            await state.clear()
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
            await show_screen(cb, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        raise
    await _show_view(cb, ctx, services, sender, ws_id, ses_id, state=state)


# -- retired resize controls (term26) ----------------------------------------
# The size control is gone: the chat is the viewport and the app decides how many
# columns it can draw. A real PTY already accepts its native tool (``stty cols N
# rows M``), and the documented power-user route is ``--cols/--rows`` on
# ``term:open``. Old messages still carry these buttons, so they must still be
# answered — with the live view, never a dead button and never a swallowed text
# message.
@router.callback_query(F.data.startswith("term:rsz_custom"))
@router.callback_query(F.data.startswith("term:rsz"))
@router.callback_query(F.data.startswith("term:resize"))
async def on_retired_resize(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
) -> None:
    """Answer a pre-term26 resize button by re-rendering the terminal view.

    The state is cleared **before** rendering: a user who was mid-resize when this
    shipped must not have their next message swallowed as a size. Nothing is
    resized (there is no size UI any more).
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    await state.clear()
    ws_id = _wid(parsed.ws_id)
    await _show_view(cb, ctx, services, sender, ws_id, parsed.ses_id, state=state)


# -- stop (the one destructive verb on a live terminal) ----------------------
@router.callback_query(F.data.startswith("term:close"))
async def on_close(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Kill the session and return to the **workspace home**, never to a list.

    Stopping a terminal is leaving it: the user goes back to the workspace they
    were in, where the terminal button is one tap away again. Rendering a dead
    session's own view (or an archive) here would be a screen nobody asked for.

    The bot-side handle is forgotten immediately: the engine already keeps the
    ended record, and this is the user saying they are done with it. Forgetting is
    hygiene, not deletion of evidence — the engine owns the process.
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    await state.clear()
    try:
        await services.sessions.close(ctx.tg_id, ws_id, ses_id)
    except EngineRefused as exc:
        # Already ended (or the engine forgot it): the outcome the user asked for
        # is the truth, so this is not an error screen.
        if exc.engine_code not in ("not_found", "session_ended"):
            raise
    await services.sessions.forget(ctx.tg_id, ws_id, ses_id)
    await _show_workspace_home(cb, ctx, services, sender, ws_id)


async def _show_workspace_home(cb, ctx, services, sender, ws_id) -> None:
    from cloudbot.handlers.workspaces import _plan_state
    from cloudbot.presentation.screens import workspaces as ws_screens

    ws = await services.workspaces.get(ctx.tg_id, ws_id)
    screen = ws_screens.workspace_detail(
        ws,
        ctx.locale,
        active_run=ws.summary.runs_active > 0,
        mode=ctx.mode,
        plan_state=await _plan_state(services, ctx.tg_id, ws, ctx.locale),
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


# -- forget (drop the bot-side handle) ---------------------------------------
@router.callback_query(F.data.startswith("term:forget"))
async def on_forget(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Drop an ended terminal's bot-side handle without touching the engine.

    There is no list to return to any more, and the dead session's own view is
    already on screen: forgetting it would re-render that view for a handle that
    no longer exists. The honest destination is the workspace home.
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    await state.clear()
    try:
        await services.sessions.forget(ctx.tg_id, ws_id, ses_id)
    except NotOwned:
        pass
    await _show_workspace_home(cb, ctx, services, sender, ws_id)


def _parse_size(spec: str) -> tuple[int, int]:
    """Parse ``COLSxROWS`` (kept for the documented ``--cols/--rows`` route).

    The resize *screen* is retired (term26), but the same syntax remains the
    power-user way to state a geometry when opening a session.
    """
    text = (spec or "").strip().lower().replace("×", "x")
    for sep in ("x", ",", " "):
        if sep in text:
            head, _, tail = text.partition(sep)
            try:
                cols, rows = int(head), int(tail)
            except ValueError:
                raise InvalidInput("size", "invalid")
            return cols, rows
    raise InvalidInput("size", "invalid")
