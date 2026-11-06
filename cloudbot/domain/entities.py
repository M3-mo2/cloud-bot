"""Domain entities — frozen, framework-free models.

Entities contain **no** user-facing text. Only ``presentation`` turns an entity
into words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    Limits,
    ResourceUsage,
    RunId,
    RunPage,
    RunStatus,
    SessionStatus,
    WorkspaceId,
    WorkspaceStatus,
    WorkspaceSummary,
)


@dataclass(frozen=True)
class User:
    tg_id: int
    locale: Locale
    created_at: datetime
    #: The plan label is **derived** from ``domain.subscription`` (see
    #: ``AdminService.user_summary``); it is not stored on the user any more, so
    #: there is one truth about whether a subscription is active. The legacy
    #: ``users.plan`` column remains in SQLite (additive migration) but is unused.
    #: The Telegram username/full name captured at first contact; a reading aid
    #: for the admin panel, never an identity.
    display_name: str = ""
    #: When the user was last seen (UTC); the admin's "active in 24 h" reads it.
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class Workspace:
    id: WorkspaceId
    owner_tg_id: int
    name: str
    profile: str
    status: WorkspaceStatus
    limits: Limits
    summary: WorkspaceSummary
    created_at: datetime
    last_activity_at: datetime


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    type: Literal["file", "dir", "symlink"]
    size: int | None
    mode: str | None
    mod_time: datetime | None
    runnable: str | None
    target: str | None = None


@dataclass(frozen=True)
class Run:
    id: RunId
    workspace_id: WorkspaceId
    kind: Literal["file", "command"]
    argv: list[str]
    cwd: str
    status: RunStatus
    exit_code: int | None
    interpreter: str | None
    timeout_sec: int | None
    started_at: datetime | None
    ended_at: datetime | None
    unlimited: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    logs_truncated: bool = False

    @property
    def command_display(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True)
class TerminalSession:
    id: str
    workspace_id: WorkspaceId
    status: SessionStatus
    cols: int = 80
    rows: int = 24
    out_offset: int = 0
    created_at: datetime | None = None
    #: Human-facing copy is derived from ``status`` + ``reason`` in presentation.
    reason: str = ""
    #: ``diagnostic`` is engine-internal (a failed launch); it must NEVER be
    #: rendered to a normal user — DEBUG+admin only, collapsed.
    diagnostic: str = ""
    exit_code: int | None = None
    output_truncated: bool = False


@dataclass(frozen=True)
class WorkspaceListPage:
    workspaces: tuple[Workspace, ...]
    total: int
    offset: int = 0
    limit: int = 10


__all__ = [
    "FileEntry",
    "Run",
    "RunPage",
    "RunStatus",
    "ResourceUsage",
    "SessionStatus",
    "TerminalSession",
    "User",
    "Workspace",
    "WorkspaceId",
    "WorkspaceListPage",
    "WorkspaceStatus",
]
