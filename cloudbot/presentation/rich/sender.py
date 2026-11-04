"""``RichSender`` — the single outbound gateway to Telegram.

The only module that calls ``Bot.send_rich_message`` / ``send_rich_message_draft``
/ ``edit_message_text``. Nothing else in the codebase calls a ``Bot.send_*`` or
``Bot.edit_*`` method (enforced by an architecture test).
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram.types import InputRichMessage, Message, ReplyParameters

from cloudbot.presentation.rich.document import RichDoc

log = structlog.get_logger("cloudbot.sender")


class RichSender:
    def __init__(self, bot: Any) -> None:
        self._bot = bot
        self._last_html: dict[tuple[int, int], str] = {}

    async def send(
        self,
        chat_id: int,
        doc: RichDoc,
        *,
        markup: Any | None = None,
        reply_to: int | None = None,
    ) -> Message:
        return await self._bot.send_rich_message(
            chat_id=chat_id,
            rich_message=doc.build(),
            reply_markup=markup,
            # aiogram's rich API takes reply_parameters (a ReplyParameters object); there is no
            # reply_to_message_id here — that name belongs to send_message. Measured against
            # aiogram 3.31.0's own signature, and bound by tests/contract/test_sender_signatures.py.
            reply_parameters=ReplyParameters(message_id=reply_to) if reply_to is not None else None,
        )

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        doc: RichDoc,
        *,
        markup: Any | None = None,
    ) -> Message:
        html = doc.html
        if self._last_html.get((chat_id, message_id)) == html and markup is None:
            # Telegram rejects "message is not modified"; short-circuit.
            return await self._get_message_stub(chat_id, message_id)
        self._last_html[(chat_id, message_id)] = html
        return await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            rich_message=doc.build(),
            reply_markup=markup,
        )

    async def draft(
        self,
        chat_id: int,
        draft_id: int,
        doc: RichDoc,
        *,
        can_stop: bool = True,
        keep_on_stop: bool = False,
    ) -> bool:
        return await self._bot.send_rich_message_draft(
            chat_id=chat_id,
            draft_id=draft_id,
            rich_message=doc.build(),
            can_stop=can_stop,
            keep_on_stop=keep_on_stop,
        )

    async def answer_callback(
        self, cb: Any, *, text: str | None = None, alert: bool = False
    ) -> None:
        await cb.answer(text=text or None, show_alert=alert)

    @staticmethod
    def build_message(html: str, *, is_rtl: bool) -> InputRichMessage:
        return InputRichMessage(html=html, is_rtl=is_rtl)

    async def _get_message_stub(self, chat_id: int, message_id: int) -> Message:
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)
