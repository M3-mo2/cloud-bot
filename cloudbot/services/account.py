"""Account use-cases: onboard, set_locale, profile."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import User
from cloudbot.domain.ports.users import UserRepository


class AccountService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def get_or_create(self, tg_id: int, *, default_locale: Locale) -> User:
        return await self._users.get_or_create(tg_id, default_locale=default_locale)

    async def onboard(self, tg_id: int, *, default_locale: Locale) -> tuple[User, bool]:
        """Return ``(user, is_new)``."""
        existing = await self._users.get(tg_id)
        if existing is not None:
            return existing, False
        user = await self._users.get_or_create(tg_id, default_locale=default_locale)
        return user, True

    async def set_locale(self, tg_id: int, locale: Locale) -> None:
        await self._users.set_locale(tg_id, locale)

    async def profile(self, tg_id: int, *, default_locale: Locale) -> User:
        return await self._users.get_or_create(tg_id, default_locale=default_locale)
