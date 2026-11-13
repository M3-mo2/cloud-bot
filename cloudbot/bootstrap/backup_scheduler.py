"""The backup scheduler — **disabled by default**; the panel owns the schedule.

There was no cron/apscheduler in the tree (checked: no ``schedule`` import, no
APScheduler dependency); the only other background task is
``NotificationPoller``, which is a delivery poll and makes no time decisions.

Why it is off: two schedulers used to drive one ``backup_config`` row — this
task on the bot's clock and the panel's ``/api/backup/tick`` from the host
crontab. Both wrote ``last_error`` / ``last_run_at`` / ``next_run_at``, so they
raced and one overwrote the other's result, and this one produced an artifact in
a different format (SQLite ``bot.db``) from the panel's Postgres ``pg_dump``.
The panel already has the crontab entry, the tick key and the Postgres dump, so
**the panel is the single owner**. This class stays for the day the bot is the
only process, and is started only when ``CW_BOT_BACKUP_SCHEDULER=1``.

The **manual** backup action in Telegram never goes through here: it calls
``BackupService.make_backup`` directly and keeps working while this is off.

When enabled, it is a single asyncio task that wakes on the *stored*
``next_run_at`` — never on a "sleep N and hope" grid — and calls the same
``BackupService.make_backup`` the panel's instant button calls. A restart
re-reads ``next_run_at`` from the database, so the cadence does not drift.
Waking is bounded (never more than ``max_sleep_s``) so a schedule edited while
the task sleeps is picked up promptly without polling tightly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog

from cloudbot.domain.backup import destination
from cloudbot.services.backup import BackupService

log = structlog.get_logger("cloudbot.backup.scheduler")

#: The one switch. Default off: the panel's cron tick owns scheduled backups.
ENABLE_ENV = "CW_BOT_BACKUP_SCHEDULER"

def enabled_from_env(environ: "dict[str, str] | None" = None) -> bool:
    """True only when the operator explicitly set the switch to ``1``.

    Read directly rather than through ``Settings``: this is an operator switch,
    not bot configuration, and its name is the one the runbook uses.
    """
    import os

    source = os.environ if environ is None else environ
    return (source.get(ENABLE_ENV) or "").strip() == "1"

#: ``(chat_id, filename, data) -> awaitable`` — the real one sends a document.
SendFn = Callable[[int, str, bytes], Awaitable[None]]

class BackupScheduler:
    def __init__(
        self,
        service: BackupService,
        *,
        admin_ids: Callable[[], list[int]],
        send: SendFn,
        max_sleep_s: float = 60.0,
        enabled: bool = False,
    ) -> None:
        self._service = service
        self._admin_ids = admin_ids
        self._send = send
        self._max_sleep = max(1.0, max_sleep_s)
        self._enabled = enabled
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """Start when enabled; otherwise say once, at boot, that it is off and why."""
        if not self._enabled:
            log.info(
                "backup.scheduler_disabled",
                owner="panel",
                reason=(
                    "the panel's /api/backup/tick (host crontab) owns scheduled "
                    f"backups; set {ENABLE_ENV}=1 to let the bot schedule them again"
                ),
            )
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                sleep_for = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad row must not kill the loop
                log.error("backup.scheduler_tick_failed", exc_info=True)
                sleep_for = 5.0
            await asyncio.sleep(min(self._max_sleep, max(1.0, sleep_for)))

    async def tick(self, *, now: datetime | None = None) -> float:
        """Run once if due; return the seconds until the next wake."""
        now = now or datetime.now(timezone.utc)
        artifact = await self._service.run_scheduled(now=now)
        config = await self._service.config()
        if artifact is not None:
            chat_id = destination(config, (self._admin_ids() or [0])[0])
            try:
                await self._send(chat_id, artifact.filename, artifact.data)
            except Exception:  # noqa: BLE001 - the file is kept on disk regardless
                log.error("backup.delivery_failed", chat_id=chat_id, exc_info=True)
        if not config.enabled or config.next_run_at is None:
            return self._max_sleep
        return max(1.0, (config.next_run_at - now).total_seconds())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._task = None
