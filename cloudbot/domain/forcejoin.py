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
ChatState = Literal["subscribed", "absent", "unknown"]

#: The decision outcomes written to ``forcejoin_events``.
#:
#: ``pass`` / ``deny`` / ``unknown`` / ``error`` are membership decisions; a
#: ``config`` row is an operator change (add/remove/enable/disable/test/switch) so
#: one table answers both "what did users experience" and "what changed".
EventOutcome = Literal["pass", "deny", "unknown", "error", "joined", "config"]

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
class GateEvent:
    """One recorded gate decision (a row of ``forcejoin_events``).

    ``reason`` is a short stable code; ``detail`` a scrubbed error string capped
    at :data:`EVENT_DETAIL_MAX`. Written by the service, never by a handler.
    """

    outcome: EventOutcome
    reason: str = ""
    tg_id: int | None = None
    chat_id: int | None = None
    latency_ms: int | None = None
    cached: bool = False
    detail: str = ""

    def normalized(self) -> "GateEvent":
        from dataclasses import replace

        return replace(self, detail=scrub_detail(self.detail))

#: The detail column is capped at 300 characters (TASK forcejoin-v2 hard rule 3).
EVENT_DETAIL_MAX = 300
#: Substrings that must never reach ``detail`` (a token is a credential).
_SECRET_MARKERS = ("bearer ", "token", "api_key", "apikey", "secret", "password")

def scrub_detail(text: str) -> str:
    """Cap at :data:`EVENT_DETAIL_MAX` and blank anything credential-shaped.

    A Telegram error never carries a bot token, but a hard rule is cheap to honour
    here: an error string that mentions a token/key is not written at all.
    """
    raw = " ".join(str(text or "").split())
    lowered = raw.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return ""
    return raw[:EVENT_DETAIL_MAX]


@dataclass(frozen=True)
class MembershipResult:
    """The outcome of one membership check.

    ``subscribed`` is ``True`` / ``False`` only when the answer is known;
    ``unknown`` means the gate cannot tell and must **allow**.

    ``latency_ms`` and ``cached`` describe how the answer was obtained (the
    membership call's own duration, and whether a positive cache answered) so the
    service can record them without measuring twice.
    """

    chat_id: int
    subscribed: bool
    unknown: bool = False
    reason: str = ""
    latency_ms: int = 0
    cached: bool = False
    detail: str = ""
    retry_after: int = 0

    @classmethod
    def allowed(cls, chat_id: int, *, reason: str = "subscribed") -> "MembershipResult":
        return cls(chat_id=chat_id, subscribed=True, reason=reason)

    @classmethod
    def refused(cls, chat_id: int, *, reason: str = "absent") -> "MembershipResult":
        return cls(chat_id=chat_id, subscribed=False, reason=reason)

    @classmethod
    def unknown_for(
        cls, chat_id: int, reason: str, *, detail: str = "", retry_after: int = 0
    ) -> "MembershipResult":
        return cls(
            chat_id=chat_id,
            subscribed=True,
            unknown=True,
            reason=reason,
            detail=detail,
            retry_after=int(retry_after or 0),
        )

    @property
    def state(self) -> ChatState:
        if self.unknown:
            return "unknown"
        return "subscribed" if self.subscribed else "absent"

    def with_measurement(self, *, latency_ms: int, cached: bool) -> "MembershipResult":
        from dataclasses import replace

        return replace(self, latency_ms=int(latency_ms), cached=cached)


@dataclass(frozen=True)
class ChatVerdict:
    """One chat's canonical state for one user, with the chat it names.

    The UI never has to join two sources to name a pending chat: ``chat`` carries
    the title/username and ``state`` is exactly ``subscribed | absent | unknown``.
    """

    chat: ForceJoinChat
    state: ChatState
    reason: str = ""
    cached: bool = False


@dataclass(frozen=True)
class GateStatus:
    """The full gate verdict for one user, ready for one screen."""

    enabled: bool
    required: tuple[ForceJoinChat, ...] = ()
    missing: tuple[ForceJoinChat, ...] = ()
    warnings: tuple[str, ...] = ()
    verdicts: tuple[ChatVerdict, ...] = ()

    @property
    def must_join(self) -> bool:
        return self.enabled and bool(self.missing)

    @property
    def unknown_chats(self) -> tuple[ChatVerdict, ...]:
        """Chats the gate could not verify — the user was let through them."""
        return tuple(v for v in self.verdicts if v.state == "unknown")

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
