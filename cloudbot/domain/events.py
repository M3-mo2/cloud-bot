"""Event log vocabulary — categories, severity, records and delivery models.

This module is the **one place** a category is declared. Adding a category is one
``EventCategory`` member plus its catalog strings in ``en.py``/``ar.py``; nothing
else (no dispatcher, no notifier arithmetic) changes. ``EventCategory.label_key``
derives the catalog key from the member, so the mapping cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from cloudbot.domain.value_objects import WorkspaceId


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def emoji(self) -> str:
        return {"info": "ℹ️", "warning": "⚠️", "error": "❌"}[self.value]


class EventCategory(str, Enum):
    """Every event the bot can log. The value is the stored key."""

    USER_NEW = "user.new"
    USER_BLOCKED = "user.blocked"
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_DELETED = "workspace.deleted"
    WORKSPACE_SUSPENDED = "workspace.suspended"
    RUN_STARTED = "run.started"
    RUN_FAILED = "run.failed"
    PACKAGE_INSTALL_FAILED = "package.install_failed"
    TUNNEL_CREATED = "tunnel.created"
    ENGINE_UNREACHABLE = "engine.unreachable"
    ADMIN_ACTION = "admin.action"

    @property
    def label_key(self) -> str:
        return f"event.cat.{self.value}"

    @property
    def default_severity(self) -> Severity:
        return _DEFAULT_SEVERITY[self]


#: The default severity per category — the table (not a branch) the emitter reads.
_DEFAULT_SEVERITY: dict[EventCategory, Severity] = {
    EventCategory.USER_NEW: Severity.INFO,
    EventCategory.USER_BLOCKED: Severity.WARNING,
    EventCategory.WORKSPACE_CREATED: Severity.INFO,
    EventCategory.WORKSPACE_DELETED: Severity.WARNING,
    EventCategory.WORKSPACE_SUSPENDED: Severity.WARNING,
    EventCategory.RUN_STARTED: Severity.INFO,
    EventCategory.RUN_FAILED: Severity.WARNING,
    EventCategory.PACKAGE_INSTALL_FAILED: Severity.WARNING,
    EventCategory.TUNNEL_CREATED: Severity.INFO,
    EventCategory.ENGINE_UNREACHABLE: Severity.ERROR,
    EventCategory.ADMIN_ACTION: Severity.INFO,
}


def parse_category(value: str) -> EventCategory | None:
    try:
        return EventCategory(value)
    except ValueError:
        return None


# -- actors ------------------------------------------------------------------
#: An actor is a short string kept beside every event so an admin action is never
#: attributed to the user whose workspace it touched.
ACTOR_USER = "user"
ACTOR_SYSTEM = "system"


def actor_admin(tg_id: int) -> str:
    return f"admin:{tg_id}"


def actor_label_key(actor: str) -> str:
    if actor == ACTOR_USER:
        return "event.actor.user"
    if actor == ACTOR_SYSTEM:
        return "event.actor.system"
    if actor.startswith("admin:"):
        return "event.actor.admin"
    return "event.actor.system"


def actor_admin_id(actor: str) -> int | None:
    if actor.startswith("admin:"):
        try:
            return int(actor.split(":", 1)[1])
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class NewEvent:
    """An event on its way into the log (no id yet)."""

    category: EventCategory
    summary_key: str
    actor: str = ACTOR_SYSTEM
    severity: Severity | None = None
    tg_id: int | None = None
    ws_id: str | None = None
    params: dict[str, object] = field(default_factory=dict)

    @property
    def effective_severity(self) -> Severity:
        return self.severity or self.category.default_severity


@dataclass(frozen=True)
class EventRecord:
    """A persisted event, as read back for the audit/notification screens."""

    id: int
    ts: datetime
    category: EventCategory
    severity: Severity
    tg_id: int | None
    ws_id: str | None
    summary_key: str
    params: dict[str, object]
    actor: str


@dataclass(frozen=True)
class NotificationMessage:
    """One coalesced notification: N events of a category in a window."""

    category: EventCategory
    severity: Severity
    count: int
    first_event_id: int
    last_event_id: int
    summary_key: str
    params: dict[str, object]
    #: The exact event ids folded into this message; lets a test assert that
    #: every event was delivered exactly once.
    event_ids: tuple[int, ...] = ()
    gap_warning: bool = False


@dataclass(frozen=True)
class NotificationPlan:
    """What the notifier decided to send for one admin, and the new cursor.

    The dispatcher sends every ``messages`` item, then (when ``gap_events`` or
    ``suppressed_events`` is non-zero) the one gap/suppression message, and only
    then calls ``commit`` — so a failed send never advances the cursor and never
    loses an event.
    """

    admin_id: int
    messages: list[NotificationMessage] = field(default_factory=list)
    new_cursor: int = 0
    paused: bool = False
    #: Events older than the coalesce window (accumulated while down/paused):
    #: summarised in ONE message, never delivered one by one.
    gap_events: int = 0
    #: Coalesced messages dropped because the per-minute cap was hit; reported in
    #: ONE message carrying the real event count.
    suppressed_events: int = 0
    #: The persisted cursor was ahead of the log (a backwards clock): reset to the
    #: log's tip and say so rather than silently skip.
    backwards_clock: bool = False

    @property
    def has_content(self) -> bool:
        return bool(
            self.messages
            or self.gap_events
            or self.suppressed_events
            or self.backwards_clock
        )


def workspace_of(ws_id: str | WorkspaceId | None) -> str | None:
    return str(ws_id) if ws_id is not None else None
