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
) -> None:
    """Edit the originating message in place, or send when required.

    Sends (rather than edits) when: the screen needs a reply keyboard or
    ``force_reply`` (edits cannot carry them), the result should be kept
    (``force_send``), or the event is a plain message.
    """
    if isinstance(event, CallbackQuery) and event.message is not None and not (
        screen.send_new or force_send
    ):
        await sender.answer_callback(event)
        await sender.edit(
            event.message.chat.id, event.message.message_id, screen.doc, markup=screen.markup
        )
        return
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, CallbackQuery):
        await sender.answer_callback(event)
    await sender.send(chat_id, screen.doc, markup=screen.markup)
