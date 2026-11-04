"""Event-source middleware — the one writer seam for the event log.

Every update gets a fresh ``RequestEvents`` intent buffer in ``data["events"]``.
Handlers add intents; this middleware flushes them to the persisted log **after**
the update has been handled, so an event is only logged for work that actually
completed. A failed engine call is turned into ``engine.unreachable`` by the error
guard, which writes into the same buffer.

It is also the observer for **entered mode**: while an admin is inside another
user's workspace, each interaction is recorded as an ``admin.action`` attributed
to the ADMIN, on the subject's workspace — one seam, never a dozen call sites.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from cloudbot.middleware.context import UserContext
from cloudbot.services.events import EventsService, RequestEvents

log = structlog.get_logger("cloudbot.events")


class EventSourceMiddleware(BaseMiddleware):
    def __init__(self, events: EventsService) -> None:
        self._events = events

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        recorder = RequestEvents()
        data["events"] = recorder
        try:
            return await handler(event, data)
        finally:
            self._record_entered_interaction(recorder, event, data)
            try:
                await self._events.flush(recorder)
            except Exception:  # noqa: BLE001
                # Logging must never fail the user's update.
                log.error("events.flush_failed")

    @staticmethod
    def _record_entered_interaction(
        recorder: RequestEvents, event: TelegramObject, data: dict[str, Any]
    ) -> None:
        ctx: UserContext | None = data.get("ctx")
        if ctx is None or not ctx.in_entered_mode:
            return
        target = event.message if isinstance(event, Update) else event
        if not isinstance(target, (CallbackQuery, Message)):
            return
        action = ""
        if isinstance(target, CallbackQuery) and target.data:
            action = target.data.split(":", 1)[0]
        elif isinstance(target, Message) and target.text:
            action = target.text.split()[0][:32]
        recorder.admin_action(
            admin_id=ctx.admin_tg_id or ctx.tg_id,
            action=action or "interaction",
            ws_id=ctx.entered_ws_id,
            owner_tg_id=ctx.entered_owner_tg_id,
        )
