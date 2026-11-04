"""``RichSender`` — the single outbound gateway to Telegram.

The only module that calls ``Bot.send_rich_message`` / ``send_rich_message_draft``
/ ``edit_message_text``. Nothing else in the codebase calls a ``Bot.send_*`` or
``Bot.edit_*`` method (enforced by an architecture test).
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputRichMessage, Message, ReplyParameters

from cloudbot.presentation.rich.document import RichDoc

log = structlog.get_logger("cloudbot.sender")


def _markup_signature(markup: Any | None) -> str:
    """A stable signature of the reply markup (empty string when there is none).

    The edit-suppression signature must cover the **full payload** — the rich body
    *and* the keyboard — so a changed keyboard with unchanged text is still sent
    (TASK 07, Bug B). ``InlineKeyboardMarkup`` serialises deterministically.
    """
    if markup is None:
        return ""
    dump = getattr(markup, "model_dump_json", None)
    if dump is not None:
        return dump(exclude_none=True)
    return str(markup)


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    """True when Telegram refused the edit only because the payload is unchanged.

    That is not a failure: the intended state is already on screen. Anything else
    (a real Bad Request) must still propagate.
    """
    message = (getattr(exc, "message", "") or str(exc)).lower()
    return "message is not modified" in message


class RichSender:
    def __init__(self, bot: Any) -> None:
        self._bot = bot
        self._last_payload: dict[tuple[int, int], tuple[str, bool, str]] = {}

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
        built = doc.build()
        signature = (built.html, bool(built.is_rtl), _markup_signature(markup))
        key = (chat_id, message_id)
        if self._last_payload.get(key) == signature:
            # Telegram rejects "message is not modified"; short-circuit. This is a
            # per-process optimisation only — the authoritative defence is the
            # exception translation below (the cache is empty after a restart).
            return await self._get_message_stub(chat_id, message_id)
        try:
            message = await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                rich_message=built,
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                # The intended state is already on screen — success, not an error.
                log.info("sender.edit_not_modified", chat_id=chat_id, message_id=message_id)
                self._last_payload[key] = signature
                return await self._get_message_stub(chat_id, message_id)
            raise
        self._last_payload[key] = signature
        return message

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

    async def send_document(
        self, chat_id: int, filename: str, data: bytes, *, reply_to: int | None = None
    ) -> Message:
        """Deliver raw bytes as a Telegram document.

        Used only by the two documented archive-delivery paths (the exported ZIP
        and the archive the user uploaded); general file delivery remains CB-14.
        """
        return await self._bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(data, filename=filename),
            reply_parameters=ReplyParameters(message_id=reply_to) if reply_to is not None else None,
        )

    async def download_document(self, document: Any) -> bytes:
        """Download a Telegram document's bytes through the bot session."""
        buffer = await self._bot.download(document)
        return buffer.read()

    async def answer_callback(
        self, cb: Any, *, text: str | None = None, alert: bool = False
    ) -> None:
        await cb.answer(text=text or None, show_alert=alert)

    @staticmethod
    def build_message(html: str, *, is_rtl: bool) -> InputRichMessage:
        return InputRichMessage(html=html, is_rtl=is_rtl)

    async def _get_message_stub(self, chat_id: int, message_id: int) -> Message:
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)
