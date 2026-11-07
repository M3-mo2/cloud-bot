"""Force-join gate domain: the required chats and a user's membership result.

Framework-free. The Telegram facts arrive through a port
(``domain.ports.telegram_directory``); the persistence through
``domain.ports.forcejoin``. No user-facing text lives here — the presentation
layer names every state.

The **anti-lockout rule** is expressed by ``MembershipUnknown``: the gate can
only refuse when it *knows* the user is absent. A chat the bot does not
administer, a network failure, a deleted chat — every one of those is
``unknown`` and therefore must be allowed through (with a warning logged). A
broken configuration must never brick the product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ChatKind = Literal["channel", "group"]

def chat_kind_for_type(chat_type: str) -> ChatKind:
    """A Telegram chat type (``channel``/``supergroup``/``group``) → gate kind."""
    return "channel" if chat_type == "channel" else "group"

#: The runtime flag that turns the whole gate on/off (missing row means on).
GATE_FLAG = "forcejoin.enabled"

#: Telegram member statuses that mean "subscribed".
SUBSCRIBED_STATUSES = frozenset({"member", "administrator", "creator"})

@dataclass(frozen=True)
class ForceJoinChat:
    """One required chat, as stored and as shown in the admin panel."""

    id: int
    chat_id: int
    username: str
    title: str
    kind: ChatKind
    bot_is_admin: bool
    enabled: bool
    added_at: datetime
    added_by: int

    @property
    def label(self) -> str:
        """What a user sees on the gate button: the title, else @username."""
        return self.title or (f"@{self.username}" if self.username else str(self.chat_id))

    def link(self) -> str:
        """The public URL a user can open to join, or ``""`` when unknown."""
        if self.username:
            return f"https://t.me/{self.username}"
        return ""

@dataclass(frozen=True)
class MembershipResult:
    """The outcome of one membership check.

    ``subscribed`` is ``True`` / ``False`` only when the answer is known;
    ``unknown`` means the gate cannot tell and must **allow**.
    """

    chat_id: int
    subscribed: bool
    unknown: bool = False
    reason: str = ""

    @classmethod
    def allowed(cls, chat_id: int) -> "MembershipResult":
        return cls(chat_id=chat_id, subscribed=True)

    @classmethod
    def refused(cls, chat_id: int) -> "MembershipResult":
        return cls(chat_id=chat_id, subscribed=False)

    @classmethod
    def unknown_for(cls, chat_id: int, reason: str) -> "MembershipResult":
        return cls(chat_id=chat_id, subscribed=True, unknown=True, reason=reason)

@dataclass(frozen=True)
class GateStatus:
    """The full gate verdict for one user, ready for one screen."""

    enabled: bool
    required: tuple[ForceJoinChat, ...] = ()
    missing: tuple[ForceJoinChat, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def must_join(self) -> bool:
        return self.enabled and bool(self.missing)

def status_is_subscribed(status: str, *, is_member: bool = False) -> bool:
    """Map a ``getChatMember`` status to subscribed/not.

    ``restricted`` is subscribed only when ``is_member`` is true (the user is
    still in the chat with limited rights); ``left`` and ``kicked`` are not.
    """
    if status in SUBSCRIBED_STATUSES:
        return True
    if status == "restricted":
        return bool(is_member)
    return False


class ChatResolutionError(Exception):
    """A chat reference could not be resolved / read through the Bot API."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
