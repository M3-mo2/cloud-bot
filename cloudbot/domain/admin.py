"""Admin read models — pure data for the panel screens.

No I/O, no aiogram. ``AdminService`` builds these; the screens render them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import User, Workspace
from cloudbot.domain.events import EventRecord
from cloudbot.domain.forcejoin import ForceJoinChat
from cloudbot.domain.moderation import UserBan, WorkspaceBan
from cloudbot.domain.backup import BackupView
from cloudbot.domain.subscription import Subscription
from cloudbot.domain.value_objects import ResourceUsage


@dataclass(frozen=True)
class UserSummary:
    tg_id: int
    display_name: str
    locale: Locale
    plan: str
    created_at: datetime
    last_seen_at: datetime | None
    workspace_count: int


@dataclass(frozen=True)
class WorkspaceWithUsage:
    workspace: Workspace
    usage: ResourceUsage | None = None
    usage_error: str = ""


@dataclass(frozen=True)
class EngineHealthView:
    """An honest engine-health reading: ``reachable`` false means the zeros below
    are unknown, not zero."""

    reachable: bool
    health_status: str = ""
    ready_status: str = ""
    version: str = ""
    api_version: str = ""
    error: str = ""


@dataclass(frozen=True)
class AdminOverview:
    total_users: int
    active_24h: int
    workspaces_by_status: dict[str, int] = field(default_factory=dict)
    live_runs: int = 0
    live_sessions: int = 0
    engine: EngineHealthView = field(
        default_factory=lambda: EngineHealthView(reachable=False)
    )
    uptime_seconds: float = 0.0
    queue_state: str = ""


@dataclass(frozen=True)
class UserPage:
    user: UserSummary
    workspaces: list[WorkspaceWithUsage] = field(default_factory=list)


@dataclass(frozen=True)
class UserListPage:
    users: list[UserSummary]
    total: int
    offset: int = 0
    limit: int = 10
    query: str = ""


@dataclass(frozen=True)
class AuditPage:
    events: list[EventRecord]
    total: int
    offset: int = 0
    limit: int = 20
    category: str | None = None
    actor: str | None = None


@dataclass(frozen=True)
class EnteredWorkspace:
    """The admin's current entered-workspace view."""

    ws_id: str
    owner_tg_id: int
    workspace: Workspace | None = None
    owner_name: str = ""


@dataclass(frozen=True)
class AdminView:
    """The single read model every admin section screen is a pure function of.

    Assembled by ``AdminService``/the handler; screens never call the engine or
    the DB. Each field is ``None`` when the section that needs it was not built.
    """

    locale: Locale
    #: The instant the screen is rendered at; screens stay pure by never calling
    #: the clock themselves.
    now: datetime | None = None
    overview: AdminOverview | None = None
    users_page: UserListPage | None = None
    user_page: UserPage | None = None
    audit_page: AuditPage | None = None
    recent_events: list[EventRecord] = field(default_factory=list)
    #: Per-admin notification categories (``event_subscriptions``), NOT the
    #: user's paid subscription. Named explicitly so the two cannot be confused.
    notification_subscriptions: dict[str, bool] = field(default_factory=dict)
    #: The subject's paid subscription, when the screen shows one.
    subscription: "Subscription | None" = None
    notify_paused: bool = False
    #: When set, the admin only receives events for this user.
    notify_scope: int | None = None
    notification_cursor: int = 0
    entered: EnteredWorkspace | None = None
    #: The force-join gate's chats and its one global switch (admin section).
    forcejoin_chats: list[ForceJoinChat] = field(default_factory=list)
    forcejoin_enabled: bool = True
    #: The backup section's schedule, owned files and schema readings.
    backup: "BackupView | None" = None
    #: The admin's own Telegram id, so a section can default a destination to
    #: "this chat". Read from the identity gate's context, never from input.
    admin_id: int = 0
    #: The subject user's ban, when the screen is about a specific user.
    user_ban: UserBan | None = None
    #: The subject workspace's ban, when the screen is about a specific workspace.
    workspace_ban: WorkspaceBan | None = None
    #: The moderation section: every ban currently in force (users + workspaces).
    banned_users: list[UserBan] = field(default_factory=list)
    banned_workspaces: list[WorkspaceBan] = field(default_factory=list)
    #: ``ws_id -> admin reason`` for the workspaces on a user's page, so each row
    #: can render its ban state and the lift button.
    workspace_bans: dict[str, str] = field(default_factory=dict)


def summarize_user(user: User, workspace_count: int, *, plan: str = "free") -> UserSummary:
    """``plan`` is passed in by the service from the derived subscription."""
    return UserSummary(
        tg_id=user.tg_id,
        display_name=user.display_name or "",
        locale=user.locale,
        plan=plan,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        workspace_count=workspace_count,
    )
