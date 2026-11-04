"""Context middleware: resolve/create the user and attach ``UserContext``."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from cloudbot.core.config import Settings
from cloudbot.middleware.context import UserContext
from cloudbot.services.account import AccountService


class ContextMiddleware(BaseMiddleware):
    def __init__(self, account: AccountService, settings: Settings) -> None:
        self._account = account
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)
        user = await self._account.get_or_create(
            tg_user.id, default_locale=self._settings.default_locale
        )
        display_name = tg_user.username or tg_user.full_name or ""
        ctx = UserContext(
            tg_id=tg_user.id,
            locale=user.locale,
            is_admin=self._settings.is_admin(tg_user.id),
            mode=self._settings.mode,
            display_name=display_name,
        )
        data["ctx"] = ctx
        data["settings"] = self._settings
        return await handler(event, data)
