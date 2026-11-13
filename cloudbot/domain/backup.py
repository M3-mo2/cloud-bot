"""Backup domain: the schedule, the artifact description, and the restore verdict.

Framework-free and pure. The persistence adapter produces bytes and probes a
database; the service orchestrates the artifact and the restore; the presentation
layer names every state. Nothing here touches a file or a clock directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: A full backup (the whole bot database) or the configuration only.
BackupScope = Literal["full", "settings"]

#: The artifact file names this feature owns. Retention deletes only these, so a
#: file the operator put in the directory by hand is never touched.
FULL_PREFIX = "cloudbot-backup-"
SETTINGS_PREFIX = "cloudbot-settings-"
OWNED_PREFIXES = (FULL_PREFIX, SETTINGS_PREFIX)

#: The timezone his account is in; a daily schedule is wall-clock time there.
DEFAULT_TZ = "Africa/Cairo"

@dataclass(frozen=True)
class BackupConfig:
    enabled: bool = False
    period_kind: Literal["hours", "daily"] = "hours"
    period_hours: int = 24
    daily_time: str = "03:00"
    tz_name: str = DEFAULT_TZ
    destination_chat_id: int | None = None
    retention_keep: int = 7
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_error: str = ""
    #: The Telegram message id of the last delivered artifact; None when the last
    #: run did not deliver. Stored so "did it arrive?" is a fact, not a log hunt.
    last_message_id: str | None = None
    updated_at: datetime | None = None
    updated_by: int = 0

@dataclass(frozen=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime
    scope: BackupScope

@dataclass(frozen=True)
class DbProbe:
    """A measured reading of a SQLite database, from the adapter."""

    sqlite: bool
    table_names: frozenset[str] = frozenset()
    schema_version: int = 0
    integrity: str = ""
    detail: str = ""

    @property
    def has_migrations(self) -> bool:
        return "schema_migrations" in self.table_names

@dataclass(frozen=True)
class RestoreValidation:
    """The one verdict every restore path uses. ``reason`` is a stable code the
    presentation layer maps; it is never an English engine message."""

    ok: bool
    scope: BackupScope
    reason: str = ""
    detail: str = ""
    schema_version: int = 0
    manifest: dict = field(default_factory=dict)
    database_bytes: bytes = b""

@dataclass(frozen=True)
class BackupArtifact:
    scope: BackupScope
    filename: str
    data: bytes
    schema_version: int
    created_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.data)

@dataclass(frozen=True)
class BackupView:
    config: BackupConfig
    files: tuple[BackupFile, ...] = ()
    schema_version: int = 0
    code_schema_version: int = 0
    directory: str = ""
    admin_id: int = 0

# -- schedule arithmetic -----------------------------------------------------
def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)

def parse_daily_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        return 3, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 3, 0
    return hour, minute

def next_run_after(config: BackupConfig, after: datetime) -> datetime:
    """The next instant this schedule fires, computed **from ``after``**.

    Because the caller always passes the actual last run (never "now"), a restart
    that happens in between does not drift the cadence: the next firing stays on
    the grid anchored to the previous one.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    if config.period_kind == "hours":
        hours = max(1, int(config.period_hours))
        return after + timedelta(hours=hours)
    hour, minute = parse_daily_time(config.daily_time)
    zone = _tz(config.tz_name)
    local = after.astimezone(zone)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target = target + timedelta(days=1)
    return target.astimezone(timezone.utc)

def due(config: BackupConfig, now: datetime) -> bool:
    return bool(config.enabled and config.next_run_at is not None and now >= config.next_run_at)

def after_run(config: BackupConfig, ran_at: datetime) -> BackupConfig:
    """Advance the schedule exactly once, anchored on the run that just happened."""
    return replace(
        config,
        last_run_at=ran_at,
        next_run_at=next_run_after(config, ran_at),
        last_error="",
    )

def with_enabled(config: BackupConfig, enabled: bool, now: datetime) -> BackupConfig:
    """Turning it on (re)anchors the first run; turning it off keeps the anchor so
    re-enabling continues the cadence instead of firing instantly."""
    if not enabled:
        return replace(config, enabled=False)
    next_run = config.next_run_at
    if next_run is None:
        anchor = config.last_run_at or now
        next_run = next_run_after(config, anchor)
        if next_run <= now:
            next_run = next_run_after(config, now)
    return replace(config, enabled=True, next_run_at=next_run)

def destination(config: BackupConfig, admin_id: int) -> int:
    return int(config.destination_chat_id or admin_id)
