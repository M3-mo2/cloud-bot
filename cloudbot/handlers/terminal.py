"""Terminal handlers (Phase 1.5): my terminals, the view, input, resize, signals.

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


# -- my terminals ------------------------------------------------------------
@router.callback_query(F.data.startswith("term:list"))
async def on_list(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    sessions = await services.sessions.list_open_for_workspace(ctx.tg_id, ws_id)
    await show_screen(cb, term_screens.my_terminals(sessions, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- open --------------------------------------------------------------------
@router.callback_query(F.data.startswith("term:open"))
async def on_open(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    try:
        session = await services.sessions.open(ctx.tg_id, ws_id)
    except EngineRefused as exc:
        if exc.engine_code != "too_many_sessions":
            raise
        # The documented cap screen, never a raw error.
        sessions = await services.sessions.list_open_for_workspace(ctx.tg_id, ws_id)
        await show_screen(cb, term_screens.my_terminals(sessions, ws_id, ctx.locale), ctx=ctx, sender=sender)
        return
    await _show_view(cb, ctx, services, sender, ws_id, session.id, state=state)


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
    screen = term_screens.terminal_view(
        session, ws_id, ctx.locale, output=output, truncated=truncated, meta=meta, typing=typing
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

    key = (ctx.tg_id, str(ws_id), ses_id)
    meta = await services.sessions.meta()
    first = True
    chat_id = None
    message_id = None
    last_next: int | None = None
    idle_ticks = 0
    for _ in range(FOLLOW_MAX_ITERATIONS):
        if generation is not None and not _follow_is_current(key, generation):
            log.info("terminal.follow_superseded", ws_id=str(ws_id), ses_id=ses_id)
            return
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


# -- resize ------------------------------------------------------------------
@router.callback_query(F.data.startswith("term:resize"))
async def on_resize_menu(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """Open the resize picker.

    The terminal view emits ``term:resize`` for its **↔️ Resize** button, but no
    handler matched it: the button was live and dead at once (a producer/consumer
    mismatch of the same family as the ``IndexError``). The every-screen contract
    test catches it.
    """
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
    await show_screen(cb, term_screens.resize_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("term:rsz_custom"))
async def on_resize_custom(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = parsed.ws_id
    ses_id = parsed.ses_id
    await state.set_state(TerminalInput.awaiting_resize)
    await state.update_data(ws_id=ws_id, ses_id=ses_id)
    await show_screen(cb, term_screens.resize_custom_prompt(_wid(ws_id), ses_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("term:rsz"))
async def on_resize(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    spec = parsed.args[2]  # "COLSxROWS"
    cols, rows = _parse_size(spec)
    await services.sessions.resize(ctx.tg_id, ws_id, ses_id, cols=cols, rows=rows)
    session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
    await show_screen(cb, term_screens.resize_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.message(TerminalInput.awaiting_resize)
async def on_resize_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    ses_id = data["ses_id"]
    cols, rows = _parse_size(message.text or "")
    await services.sessions.resize(ctx.tg_id, ws_id, ses_id, cols=cols, rows=rows)
    await _show_view(message, ctx, services, sender, ws_id, ses_id)


# -- close -------------------------------------------------------------------
@router.callback_query(F.data.startswith("term:close"))
async def on_close(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    await state.clear()
    try:
        session = await services.sessions.close(ctx.tg_id, ws_id, ses_id)
    except EngineRefused as exc:
        if exc.engine_code == "not_found":
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        elif exc.engine_code == "session_ended":
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        else:
            raise
    await show_screen(cb, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- forget (drop the bot-side handle) ---------------------------------------
@router.callback_query(F.data.startswith("term:forget"))
async def on_forget(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Remove an ended terminal from *my terminals* without touching the engine."""
    parsed = await _parse_callback(cb, ctx, sender)
    if parsed is None:
        return
    ws_id = _wid(parsed.ws_id)
    ses_id = parsed.ses_id
    await state.clear()
    await services.sessions.forget(ctx.tg_id, ws_id, ses_id)
    sessions = await services.sessions.list_open_for_workspace(ctx.tg_id, ws_id)
    await show_screen(cb, term_screens.my_terminals(sessions, ws_id, ctx.locale), ctx=ctx, sender=sender)


def _parse_size(spec: str) -> tuple[int, int]:
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
