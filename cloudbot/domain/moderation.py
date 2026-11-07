"""Moderation read models — pure data, no I/O and no aiogram.

A *user ban* is an identity decision enforced by a middleware. A *workspace ban*
is an admin decision enforced at the service layer. Both keep the admin's own
words (``reason``) so the subject is never left guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class UserBan:
    tg_id: int
    reason: str
    banned_at: datetime
    banned_by: int
    #: ``None`` is a permanent ban; a past instant is already expired.
    expires_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now

    @property
    def temporary(self) -> bool:
        return self.expires_at is not None

@dataclass(frozen=True)
class DeliveryResult:
    """The **real** outcome of an admin's message to a user.

    ``delivered`` is true only when Telegram accepted the message (``message_id``
    is set); otherwise ``error`` is Telegram's own reason as a short, mapped code
    (``blocked`` / ``chat_not_found`` / ``forbidden`` / ``unknown``), never a fake
    "sent ✅" and never a raw exception string.
    """

    tg_id: int
    delivered: bool
    message_id: int | None = None
    error: str = ""

@dataclass(frozen=True)
class WorkspaceBan:
    ws_id: str
    reason: str
    banned_at: datetime
    banned_by: int
