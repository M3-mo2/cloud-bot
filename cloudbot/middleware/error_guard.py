"""Error guard: catch domain errors, render the localized screen, never leak."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from cloudbot.core.errors import DomainError, EngineUnavailable
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
            self._record_engine_unreachable(exc, data)
            await self._render(event, data, exc)
            return None

    @staticmethod
    def _record_engine_unreachable(exc: DomainError, data: dict[str, Any]) -> None:
        """Emit ``engine.unreachable`` from the same buffer the event source writes.

        This is the single place a transient engine failure becomes an event, so
        handlers never grow their own logging calls.
        """
        events = data.get("events")
        if events is None or not isinstance(exc, EngineUnavailable):
            return
        try:
            events.engine_unreachable(reason=exc.cause or "unavailable")
        except Exception:  # noqa: BLE001
            pass

    async def _render(self, event: TelegramObject, data: dict[str, Any], exc: DomainError) -> None:
        # The guard is registered once, at the **update** level (``bootstrap/app.py``)
        # so it wraps the whole router and catches a DomainError raised anywhere
        # below. An update-level aiogram 3.x middleware therefore receives an
        # ``Update``, not the ``Message``/``CallbackQuery`` it wraps: unwrap it here
        # so a re-raised refusal always renders exactly one screen (F-01). Keeping
        # one registration (rather than adding the guard to both observers) avoids
        # double-wrapping and keeps the "single outermost catch" property.
        target: TelegramObject = event
        if isinstance(event, Update):
            target = event.callback_query or event.message or event
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
            ws_id=_workspace_of(target, ctx),
        )
        try:
            if isinstance(target, CallbackQuery) and target.message is not None:
                await self._sender.edit(
                    target.message.chat.id, target.message.message_id, screen.doc, markup=screen.markup
                )
            elif isinstance(target, Message):
                await self._sender.send(target.chat.id, screen.doc, markup=screen.markup)
        except Exception:  # noqa: BLE001
            log.error("error.render_failed", code=type(exc).__name__)


def _workspace_of(target: TelegramObject, ctx: UserContext) -> str | None:
    """The workspace an action was about, so a recovery button can name it.

    The guard has no handler arguments — only the update — so it reads the id
    back out of the callback's key parts (``ws:resume:<ws>``, ``file:ls:<ws>`` …)
    and falls back to the admin's entered workspace. Never guesses: no id means
    no Resume button, only the safe list destination.
    """
    data = getattr(target, "data", None)
    if isinstance(data, str) and data:
        from cloudbot.presentation.keyboards.callbacks import decode

        for part in decode(data).key:
            text = str(part)
            if text.startswith("ws_"):
                return text
    return ctx.entered_ws_id or None


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
