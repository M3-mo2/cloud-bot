"""Handler helper: send or edit a screen consistently (edit-first, send-second)."""

from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message

from cloudbot.middleware.context import UserContext
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens.screen import Screen


async def show_screen(
    event: Any,
    screen: Screen,
    *,
    ctx: UserContext,
    sender: RichSender,
    force_send: bool = False,
    edit_id: int | None = None,
) -> Message:
    """Edit the originating message in place, or send when required.

    Sends (rather than edits) when: the screen needs a reply keyboard or
    ``force_reply`` (edits cannot carry them), the result should be kept
    (``force_send``), or the event is a plain message. ``edit_id`` forces the
    render to land on an explicit message (used by the publish flow so the
    progress message becomes the result — no orphan). Returns the message that
    now carries the screen, so a caller can update it in place later.

    While an admin is in entered mode the persistent banner is added here — one
    seam, so every screen they reach is marked, including reused user screens.
    """
    if ctx.in_entered_mode:
        from cloudbot.presentation.screens.admin._helpers import prepend_banner

        prepend_banner(screen, ctx)
    is_callback = isinstance(event, CallbackQuery)
    chat_id = event.message.chat.id if is_callback else event.chat.id
    if edit_id is not None:
        if is_callback:
            await sender.answer_callback(event)
        return await sender.edit(chat_id, edit_id, screen.doc, markup=screen.markup)
    if is_callback and event.message is not None and not (screen.send_new or force_send):
        await sender.answer_callback(event)
        return await sender.edit(
            event.message.chat.id, event.message.message_id, screen.doc, markup=screen.markup
        )
    if is_callback:
        await sender.answer_callback(event)
    return await sender.send(chat_id, screen.doc, markup=screen.markup)
