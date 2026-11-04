"""Error guard: catch domain errors, render the localized screen, never leak."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cloudbot.core.errors import DomainError
from cloudbot.core.modes import RunMode
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.mapping.errors import error_screen
from cloudbot.presentation.rich.sender import RichSender

log = structlog.get_logger("cloudbot.error_guard")


class ErrorGuardMiddleware(BaseMiddleware):
    def __init__(self, sender: RichSender) -> None:
        self._sender = sender

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except DomainError as exc:
            await self._render(event, data, exc)
            return None

    async def _render(self, event: TelegramObject, data: dict[str, Any], exc: DomainError) -> None:
        ctx: UserContext | None = data.get("ctx")
        if ctx is None:
            log.error("error.no_context", code=type(exc).__name__)
            return
        raw = None
        if isinstance(exc, DomainError) and hasattr(exc, "message"):
            raw = getattr(exc, "message", None)
        log.warning(
            "error.domain",
            code=type(exc).__name__,
            detail=getattr(exc, "engine_code", ""),
            tg_id=ctx.tg_id,
        )
        screen = error_screen(
            exc,
            ctx.locale,
            mode=ctx.mode,
            is_admin=ctx.is_admin,
            raw_payload=_raw_payload(exc) if ctx.is_admin else None,
        )
        try:
            if isinstance(event, CallbackQuery) and event.message is not None:
                await self._sender.edit(
                    event.message.chat.id, event.message.message_id, screen.doc, markup=screen.markup
                )
            elif isinstance(event, Message):
                await self._sender.send(event.chat.id, screen.doc, markup=screen.markup)
        except Exception:  # noqa: BLE001
            log.error("error.render_failed", code=type(exc).__name__)


def _raw_payload(exc: DomainError) -> str | None:
    import json

    if isinstance(exc, DomainError) and getattr(exc, "message", "") is not None:
        payload = {
            "error": {
                "code": getattr(exc, "engine_code", type(exc).__name__),
                "message": getattr(exc, "message", ""),
                "details": getattr(exc, "details", {}),
            }
        }
        return json.dumps(payload, ensure_ascii=False)
    return None
