"""Per-user token-bucket throttle.

PRODUCTION only (relaxed in DEV, off in DEBUG). A burst is refused without any
engine call. The engine itself has no rate limiting, so this is the only guard.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from cloudbot.core.config import Settings
from cloudbot.core.modes import throttle_enabled, throttle_relaxed
from cloudbot.middleware.context import UserContext

log = structlog.get_logger("cloudbot.throttle")


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._buckets: dict[int, tuple[float, float]] = {}  # tg_id -> (tokens, last_ts)

    def _allow(self, tg_id: int) -> bool:
        rate = self._settings.throttle_rate
        if throttle_relaxed(self._settings.mode):
            rate *= 5
        now = time.monotonic()
        tokens, last = self._buckets.get(tg_id, (rate, now))
        tokens = min(rate, tokens + (now - last) * rate)
        if tokens < 1.0:
            self._buckets[tg_id] = (tokens, now)
            return False
        self._buckets[tg_id] = (tokens - 1.0, now)
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: UserContext | None = data.get("ctx")
        if ctx is None or not throttle_enabled(ctx.mode):
            return await handler(event, data)
        if not self._allow(ctx.tg_id):
            if isinstance(event, CallbackQuery):
                await event.answer("You're going a bit fast — one moment.", show_alert=False)
            log.info("throttle.refused", tg_id=ctx.tg_id)
            return None
        return await handler(event, data)
