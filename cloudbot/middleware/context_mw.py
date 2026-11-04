"""Context middleware: resolve/create the user and attach ``UserContext``."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from cloudbot.core.config import Settings
from cloudbot.middleware.context import UserContext
from cloudbot.services.account import AccountService


class ContextMiddleware(BaseMiddleware):
    def __init__(
        self,
        account: AccountService,
        settings: Settings,
        *,
        admin_state: Any | None = None,
    ) -> None:
        self._account = account
        self._settings = settings
        self._admin_state = admin_state

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
        # Persist the display name the first time we learn it (the event log and
        # the admin users list read it); harmless to repeat.
        if display_name and display_name != user.display_name:
            try:
                await self._account.set_display_name(tg_user.id, display_name)
            except Exception:  # noqa: BLE001
                pass

        is_admin = self._settings.is_admin(tg_user.id)
        effective_tg_id = tg_user.id
        admin_tg_id = 0
        entered_ws_id = ""
        entered_owner_tg_id = 0
        # Entered mode: an admin inside another user's workspace keeps the admin's
        # identity for attribution, but the *subject* becomes that user's id so the
        # ordinary ownership gate passes and the data shown is the owner's.
        if is_admin and self._admin_state is not None:
            row = await self._admin_state.entered(tg_user.id)
            if row is not None:
                entered_ws_id, entered_owner_tg_id = row
                admin_tg_id = tg_user.id
                effective_tg_id = entered_owner_tg_id

        ctx = UserContext(
            tg_id=effective_tg_id,
            locale=user.locale,
            is_admin=is_admin,
            mode=self._settings.mode,
            display_name=display_name,
            admin_tg_id=admin_tg_id,
            entered_ws_id=entered_ws_id,
            entered_owner_tg_id=entered_owner_tg_id,
        )
        data["ctx"] = ctx
        data["settings"] = self._settings
        return await handler(event, data)
