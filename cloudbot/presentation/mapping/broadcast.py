"""Telegram copy outcome → the broadcast's failure buckets.

The one place a ``copy_message`` exception is interpreted for a broadcast, so the
result screen can group ``blocked`` / ``deactivated`` / ``other`` without ever
touching a raw English sentence. Mirrors ``mapping.delivery`` for the single-user
message path; the broadcast's three buckets are deliberately coarser.
"""

from __future__ import annotations

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from cloudbot.domain.broadcast import DeliveryOutcome, FailureKind

def classify_copy_error(exc: BaseException) -> FailureKind:
    """Map a Telegram failure to one of the broadcast's three buckets.

    * a user who stopped the bot, or whose account is deleted, raises
      ``TelegramForbiddenError`` (403); the message text distinguishes the two;
    * everything else — a bad chat id, a blocked bot on the other side surfaced
      as a Bad Request, an unknown API error — is ``other``.

    ``TelegramRetryAfter`` is **not** a failure: the caller sleeps and retries, so
    it is handled before this is ever asked.
    """
    if isinstance(exc, TelegramForbiddenError):
        reason = (getattr(exc, "message", "") or str(exc)).lower()
        if "deactivated" in reason:
            return "deactivated"
        return "blocked"
    # A Bad Request that names a deleted/blocked account is still a dead audience.
    reason = (getattr(exc, "message", "") or str(exc)).lower()
    if "bot was blocked" in reason or "user is blocked" in reason:
        return "blocked"
    if "deactivated" in reason:
        return "deactivated"
    return "other"

def is_retry_after(exc: BaseException) -> bool:
    return isinstance(exc, TelegramRetryAfter)

def retry_after_seconds(exc: TelegramRetryAfter) -> int:
    """The exact value Telegram gave (never a guess of our own)."""
    return int(getattr(exc, "retry_after", 0) or 0)

def delivered(tg_id: int, message_id: int | None) -> DeliveryOutcome:
    return DeliveryOutcome(tg_id=tg_id, delivered=True, message_id=message_id)

def failed(tg_id: int, kind: FailureKind) -> DeliveryOutcome:
    return DeliveryOutcome(tg_id=tg_id, delivered=False, error=kind)
