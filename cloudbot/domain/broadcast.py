"""Broadcast domain: the audience, the source reference, and the run verdicts.

Framework-free and pure. The service orchestrates a run; the screens name every
state; the store persists counts. Nothing here touches Telegram.

Two facts are load-bearing and live here as code, not prose:

* the audience preset is a *set of tg ids computed from stored state*, never a
  guess — :func:`audience_members` is the single implementation both the service
  and the tests use;
* a failure is a short stable code (``blocked`` / ``deactivated`` / ``other``),
  so the result screen groups failures without ever showing a raw sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

#: The fixed audience presets the panel offers, in the order it offers them.
AudienceKey = Literal["all", "active", "expired", "no_plan"]

AUDIENCES: tuple[AudienceKey, ...] = ("all", "active", "expired", "no_plan")

#: The message kinds ``copy_message`` can replay exactly. An album (a media
#: group) is deliberately absent: copying it one message at a time would silently
#: send only the first, so the compose step refuses it instead.
SourceKind = Literal["text", "photo", "document", "video"]

SOURCE_KINDS: tuple[SourceKind, ...] = ("text", "photo", "document", "video")

#: Run status. ``sending`` is the in-flight guard's state; a process that dies
#: mid-run leaves ``sending`` behind and the next open reports it as
#: ``interrupted`` (the truth), never a frozen progress bar.
RunStatus = Literal["composing", "sending", "done", "stopped", "interrupted"]

#: Failure buckets the result screen groups. ``blocked`` covers a user who
#: stopped the bot, ``deactivated`` a deleted account, ``other`` everything else.
FailureKind = Literal["blocked", "deactivated", "other"]

#: Pacing: a deliberate gap between two copy sends, so a broadcast never trips
#: Telegram's per-chat/per-bot limits. 20 messages/second is Telegram's documented
#: soft ceiling; 50 ms is 20/s exactly, and the retry below covers a burst.
DEFAULT_SEND_DELAY_S: float = 0.05

#: Consecutive failures after which the run stops. A wrong token or a broken
#: network makes every send fail; stopping after this many keeps the rest of the
#: audience (and the bot's own quota) intact.
DEFAULT_FAILURE_THRESHOLD: int = 5

#: The per-run ceiling. A run whose audience is larger refuses until the admin
#: explicitly confirms the bigger number on the preview screen.
DEFAULT_MAX_RECIPIENTS: int = 5000

#: How many past broadcasts the history shows.
HISTORY_LIMIT: int = 10

@dataclass(frozen=True)
class BroadcastSource:
    """The admin's composed message, by reference only.

    ``(chat_id, message_id)`` is the whole payload: it is what
    ``Bot.copy_message`` needs to replay the message byte-identically (entities,
    links, photo, caption). Storing the text instead would be the drift this
    feature exists to prevent.
    """

    chat_id: int
    message_id: int
    kind: SourceKind

@dataclass(frozen=True)
class AudienceOption:
    key: AudienceKey
    count: int

@dataclass(frozen=True)
class BroadcastRun:
    """One persisted run, as the model shows it."""

    id: int
    created_at: datetime
    created_by: int
    source: BroadcastSource
    audience: AudienceKey
    total: int
    sent: int
    failed: int
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stop_reason: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.sent - self.failed)

    @property
    def in_flight(self) -> bool:
        return self.status == "sending"

    @property
    def retryable(self) -> bool:
        """A finished/killed run with failures can have its failures retried."""
        return self.failed > 0 and self.status in ("done", "stopped", "interrupted")

@dataclass(frozen=True)
class FailureGroup:
    kind: FailureKind
    count: int

@dataclass(frozen=True)
class BroadcastResult:
    run: BroadcastRun
    groups: tuple[FailureGroup, ...] = ()

    def group_count(self, kind: FailureKind) -> int:
        for group in self.groups:
            if group.kind == kind:
                return group.count
        return 0

@dataclass(frozen=True)
class BroadcastHistory:
    runs: tuple[BroadcastRun, ...] = ()

@dataclass(frozen=True)
class DeliveryOutcome:
    """The real outcome of one ``copy_message`` attempt."""

    tg_id: int
    delivered: bool
    error: FailureKind | Literal[""] = ""
    message_id: int | None = None

@dataclass(frozen=True)
class RunPlan:
    """The frozen audience a preview confirmed.

    Built at preview and stored; the sender iterates exactly these ids, so the
    number on the confirm screen is the number attempted.
    """

    audience: AudienceKey
    recipients: tuple[int, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.recipients)

@dataclass(frozen=True)
class BroadcastView:
    """The section's read model: options with real counts, the history, an open run."""

    options: tuple[AudienceOption, ...] = ()
    history: tuple[BroadcastRun, ...] = ()
    open_run: BroadcastRun | None = None
    max_recipients: int = DEFAULT_MAX_RECIPIENTS
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD

def classify_failure(reason: str) -> FailureKind:
    """Map a Telegram/validation reason to the result screen's three buckets.

    ``reason`` is the already-short code the sender produced (see
    ``presentation.mapping.broadcast``); this only merges ``blocked``-like codes
    and turns everything else into ``other``. It never inspects raw English.
    """
    key = (reason or "").strip().lower()
    if key in ("blocked", "bot_blocked"):
        return "blocked"
    if key in ("deactivated", "user_deactivated", "chat_not_found"):
        return "deactivated"
    return "other"

def groups_for(failures: dict[str, int]) -> tuple[FailureGroup, ...]:
    """Group a ``reason -> count`` mapping into the three display buckets."""
    totals: dict[FailureKind, int] = {"blocked": 0, "deactivated": 0, "other": 0}
    for reason, count in failures.items():
        totals[classify_failure(reason)] += int(count)
    return tuple(
        FailureGroup(kind=kind, count=totals[kind])
        for kind in ("blocked", "deactivated", "other")
        if totals[kind]
    )

def over_ceiling(count: int, ceiling: int) -> bool:
    return count > max(0, ceiling)

__all__ = [
    "AUDIENCES",
    "AudienceKey",
    "AudienceOption",
    "BroadcastHistory",
    "BroadcastResult",
    "BroadcastRun",
    "BroadcastSource",
    "BroadcastView",
    "DEFAULT_FAILURE_THRESHOLD",
    "DEFAULT_MAX_RECIPIENTS",
    "DEFAULT_SEND_DELAY_S",
    "DeliveryOutcome",
    "FailureGroup",
    "FailureKind",
    "HISTORY_LIMIT",
    "RunPlan",
    "RunStatus",
    "SOURCE_KINDS",
    "SourceKind",
    "classify_failure",
    "groups_for",
    "over_ceiling",
]
