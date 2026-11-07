"""The backup scheduler — the project's **one** scheduler.

There was no cron/apscheduler in the tree (checked: no ``schedule`` import, no
APScheduler dependency); the only other background task is
``NotificationPoller``, which is a delivery poll and makes no time decisions.

This is a single asyncio task that wakes on the *stored* ``next_run_at`` — never
on a "sleep N and hope" grid — and calls the same
``BackupService.make_backup`` the panel's instant button calls. It is started only
from ``__main__`` and never in tests; a restart re-reads ``next_run_at`` from the
database, so the cadence does not drift.

Waking is bounded (never more than ``max_sleep_s``) so a schedule edited while the
task sleeps is picked up promptly without polling tightly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog

from cloudbot.domain.backup import destination
from cloudbot.services.backup import BackupService

log = structlog.get_logger("cloudbot.backup.scheduler")

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
    ) -> None:
        self._service = service
        self._admin_ids = admin_ids
        self._send = send
        self._max_sleep = max(1.0, max_sleep_s)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                sleep_for = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad row must not kill the loop
                log.error("backup.scheduler_tick_failed")
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
                log.error("backup.delivery_failed", chat_id=chat_id)
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
