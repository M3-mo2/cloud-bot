"""Terminal handlers (Phase 1.5): my terminals, the view, input, resize, signals.

Thin aiogram routers: parse → call the ``sessions`` service → render one screen
→ send. The live view follows the engine's offset protocol through the service
helper; it is bounded and paced, and it never builds a FIFO or a run-based PTY.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.services.bundle import Services
from cloudbot.services.sessions import CTRL_D
from cloudbot.states.terminal_input import TerminalInput

log = structlog.get_logger("cloudbot.terminal")

router = Router(name="terminal")

#: Live-follow tuning: bounded and gentle on the engine (same shape as run logs).
FOLLOW_MAX_ITERATIONS = 30
FOLLOW_POLL_INTERVAL = 1.0


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


def _is_command(text: str) -> bool:
    """A slash-command is never swallowed as terminal input (D5)."""
    return text.startswith("/")


# -- my terminals ------------------------------------------------------------
@router.callback_query(F.data.startswith("term:list"))
async def on_list(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    sessions = await services.sessions.list_open_for_workspace(ctx.tg_id, ws_id)
    await show_screen(cb, term_screens.my_terminals(sessions, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- open --------------------------------------------------------------------
@router.callback_query(F.data.startswith("term:open"))
async def on_open(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    try:
        session = await services.sessions.open(ctx.tg_id, ws_id)
    except EngineRefused as exc:
        if exc.engine_code != "too_many_sessions":
            raise
        # The documented cap screen, never a raw error.
        sessions = await services.sessions.list_open_for_workspace(ctx.tg_id, ws_id)
        await show_screen(cb, term_screens.my_terminals(sessions, ws_id, ctx.locale), ctx=ctx, sender=sender)
        return
    await _show_view(cb, ctx, services, sender, ws_id, session.id)


# -- view / refresh ----------------------------------------------------------
@router.callback_query(F.data.startswith("term:refresh"))
@router.callback_query(F.data.startswith("term:view"))
async def on_view(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    ses_id = decoded.key[1]
    await _show_view(cb, ctx, services, sender, ws_id, ses_id)


async def _show_view(cb, ctx, services, sender, ws_id, ses_id) -> None:
    session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
    meta = await services.sessions.meta()
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
        session, ws_id, ctx.locale, output=output, truncated=truncated, meta=meta
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


# -- send a command ----------------------------------------------------------
@router.callback_query(F.data.startswith("term:cmd"))
async def on_cmd(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    ses_id = decoded.key[1]
    await state.set_state(TerminalInput.awaiting_command)
    await state.update_data(ws_id=ws_id, ses_id=ses_id)
    await show_screen(cb, term_screens.command_prompt(_wid(ws_id), ses_id, ctx.locale), ctx=ctx, sender=sender)


@router.message(TerminalInput.awaiting_command)
async def on_command_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    text = message.text or ""
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    ses_id = data["ses_id"]
    if _is_command(text):
        # A slash-command is never terminal input. Known commands are handled by
        # an earlier router; an unknown one lands here and simply re-renders the
        # terminal so the user stays focused with the buttons available.
        log.info("terminal.command_not_input", command=text.split()[0])
        await _show_view(message, ctx, services, sender, ws_id, ses_id)
        return
    try:
        await services.sessions.send_line(ctx.tg_id, ws_id, ses_id, text)
    except EngineRefused as exc:
        if exc.engine_code != "session_ended":
            raise
        session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        await show_screen(message, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=True)
        return
    await _follow(message, ctx, services, sender, ws_id, ses_id, force_send=True)


async def _read_all(services: Services, tg_id: int, ws_id, ses_id) -> tuple[str, bool]:
    try:
        read = await services.sessions.read_output(tg_id, ws_id, ses_id, 0)
        return read.data, read.truncated
    except EngineRefused as exc:
        if exc.engine_code != "session_ended":
            raise
        return "", False


async def _stream(
    event, ctx, services: Services, sender: RichSender, ws_id, ses_id, *, force_send: bool = False
) -> None:
    """Follow the offset cursor, updating one message in place until terminal.

    Bounded by ``FOLLOW_MAX_ITERATIONS`` and paced by ``FOLLOW_POLL_INTERVAL`` so
    the engine is never hammered (the same shape as the run-log follower).
    """
    import asyncio

    meta = await services.sessions.meta()
    first = True
    chat_id = None
    message_id = None
    for _ in range(FOLLOW_MAX_ITERATIONS):
        session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
        output, truncated = await _read_all(services, ctx.tg_id, ws_id, ses_id)
        screen = term_screens.terminal_view(
            session, ws_id, ctx.locale, output=output, truncated=truncated, meta=meta
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


async def _follow(event, ctx, services, sender, ws_id, ses_id, *, force_send: bool = False) -> None:
    """Stream output until a terminal status, bounded and paced."""
    await _stream(event, ctx, services, sender, ws_id, ses_id, force_send=force_send)


# -- signals -----------------------------------------------------------------
@router.callback_query(F.data.startswith("term:signal"))
async def on_signal(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    ses_id = decoded.key[1]
    signal = decoded.key[2]
    try:
        await services.sessions.signal(ctx.tg_id, ws_id, ses_id, signal)
    except EngineRefused as exc:
        if exc.engine_code == "session_ended":
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
            await show_screen(cb, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        raise
    await _show_view(cb, ctx, services, sender, ws_id, ses_id)


@router.callback_query(F.data.startswith("term:ctrl_d"))
async def on_ctrl_d(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    ses_id = decoded.key[1]
    try:
        await services.sessions.send_input(ctx.tg_id, ws_id, ses_id, CTRL_D)
    except EngineRefused as exc:
        if exc.engine_code == "session_ended":
            session = await services.sessions.get(ctx.tg_id, ws_id, ses_id)
            await show_screen(cb, term_screens.ended_screen(session, ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        raise
    await _show_view(cb, ctx, services, sender, ws_id, ses_id)


# -- resize ------------------------------------------------------------------
@router.callback_query(F.data.startswith("term:rsz_custom"))
async def on_resize_custom(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    ses_id = decoded.key[1]
    await state.set_state(TerminalInput.awaiting_resize)
    await state.update_data(ws_id=ws_id, ses_id=ses_id)
    await show_screen(cb, term_screens.resize_custom_prompt(_wid(ws_id), ses_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("term:rsz"))
async def on_resize(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    ses_id = decoded.key[1]
    spec = decoded.key[2]  # "COLSxROWS"
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
async def on_close(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    ses_id = decoded.key[1]
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
