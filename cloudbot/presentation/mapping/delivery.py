"""Telegram delivery outcome → the panel's honest verdict.

The admin's message goes out through ``RichSender`` (as the bot, in the rich
format). This module turns whatever Telegram answers into a :class:`DeliveryResult`:
the delivered message id, or Telegram's own reason as a short code. It is the only
place a Telegram send exception is interpreted, so "sent ✅" can never be drawn
without a real ``message_id``.
"""

from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from cloudbot.core.locale import Locale
from cloudbot.domain.moderation import DeliveryResult
from cloudbot.presentation.rich.document import RichDoc

def classify_send_error(exc: BaseException) -> str:
    """Map a Telegram send failure to a short, catalog-backed code."""
    if isinstance(exc, TelegramForbiddenError):
        return "forbidden"
    if isinstance(exc, TelegramBadRequest):
        reason = (getattr(exc, "message", "") or str(exc)).lower()
        if "blocked" in reason or "deactivated" in reason:
            return "blocked"
        if "chat not found" in reason or "user not found" in reason:
            return "chat_not_found"
        if "too many requests" in reason:
            return "rate_limited"
    return "unknown"

async def deliver(sender, tg_id: int, text: str, *, locale: Locale = Locale.EN) -> DeliveryResult:
    """Send ``text`` to ``tg_id`` as the bot and report the real outcome."""
    doc = RichDoc(locale)
    # A plain paragraph: the admin's words, escaped by RichDoc, never raw markup.
    doc.p(text)
    try:
        message = await sender.send(tg_id, doc)
    except Exception as exc:  # noqa: BLE001 - the verdict is the reason, not the stack
        return DeliveryResult(tg_id=tg_id, delivered=False, error=classify_send_error(exc))
    message_id = getattr(message, "message_id", None)
    if message_id is None:
        return DeliveryResult(tg_id=tg_id, delivered=False, error="unknown")
    return DeliveryResult(tg_id=tg_id, delivered=True, message_id=message_id)
