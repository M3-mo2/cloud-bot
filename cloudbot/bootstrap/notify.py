"""Notification delivery wiring (composition layer).

The notifier decides *what* to send; this module renders the plan (through the
presentation layer) and sends it, then commits the cursor. It lives in
``bootstrap`` because it is wiring — the one place allowed to touch both the
services layer and the presentation layer.

``NotificationPoller`` is the optional runtime driver; it is started only from
``__main__`` and never in tests.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import structlog

from cloudbot.core.locale import Locale
from cloudbot.domain.events import NotificationPlan
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.services.notifier import NotifierService

log = structlog.get_logger("cloudbot.notify")

#: ``(admin_id, doc) -> awaitable``. The real one uses ``RichSender.send``.
SendFn = Callable[[int, RichDoc], Awaitable[None]]
LocaleFn = Callable[[int], Awaitable[Locale]]
AdminIdsFn = Callable[[], list[int]]


class NotificationDispatcher:
    def __init__(
        self,
        notifier: NotifierService,
        *,
        admin_ids: AdminIdsFn,
        send: SendFn,
        locale_for: LocaleFn,
    ) -> None:
        self._notifier = notifier
        self._admin_ids = admin_ids
        self._send = send
        self._locale_for = locale_for

    async def flush(self) -> int:
        sent = 0
        for admin_id in self._admin_ids():
            plan = await self._notifier.plan(admin_id)
            if not plan.has_content:
                continue
            try:
                await self._dispatch_plan(plan)
            except Exception:  # noqa: BLE001
                # Leave the cursor untouched so the next flush retries; never
                # advance past an undelivered event.
                log.error("notify.dispatch_failed", admin_id=admin_id)
                continue
            await self._notifier.commit(plan)
            sent += len(plan.messages)
        return sent

    async def _dispatch_plan(self, plan: NotificationPlan) -> None:
        from cloudbot.presentation.screens.admin.notification_message import (
            backwards_clock_message,
            gap_message,
            notification_message,
            suppression_message,
        )

        locale = await self._locale_for(plan.admin_id)
        for message in plan.messages:
            await self._send(plan.admin_id, notification_message(message, locale))
        if plan.gap_events:
            await self._send(plan.admin_id, gap_message(plan.gap_events, locale))
        if plan.suppressed_events:
            await self._send(
                plan.admin_id, suppression_message(plan.suppressed_events, locale)
            )
        if plan.backwards_clock:
            await self._send(plan.admin_id, backwards_clock_message(locale))


class NotificationPoller:
    """A bounded, cancellable background poll; one task in the bot process."""

    def __init__(
        self, dispatcher: NotificationDispatcher, *, interval_s: float = 15.0
    ) -> None:
        self._dispatcher = dispatcher
        self._interval = interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self._dispatcher.flush()
            except Exception:  # noqa: BLE001
                log.error("notify.poll_failed")
            await asyncio.sleep(self._interval)

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
