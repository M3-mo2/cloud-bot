"""Observability: correlation id, timing, structured log per update."""

from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

log = structlog.get_logger("cloudbot.update")


class ObservabilityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            file_id = data.get("event_update")
            log.info(
                "update.handled",
                correlation_id=correlation_id,
                duration_ms=duration_ms,
                update_type=type(event).__name__,
            )
            structlog.contextvars.clear_contextvars()
