"""Mode gate: block admin/debug commands for non-admins and outside DEV/DEBUG.

Runs before admin routers so a new admin command cannot forget the gate. A
non-admin gets no reveal that the command exists.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from cloudbot.core.modes import allows_admin_commands
from cloudbot.middleware.context import UserContext

_ADMIN_COMMANDS = {"/diag", "/raw", "/mode", "/replay", "/health"}


class ModeGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: UserContext | None = data.get("ctx")
        if ctx is not None and isinstance(event, Message) and event.text:
            command = event.text.split()[0].split("@")[0].lower()
            if command in _ADMIN_COMMANDS:
                if not (allows_admin_commands(ctx.mode) and ctx.is_admin):
                    # Silently ignore: do not reveal the command exists.
                    return None
        return await handler(event, data)
