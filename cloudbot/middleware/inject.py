"""Inject the service bundle and sender into every handler's data."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from cloudbot.presentation.rich.sender import RichSender
from cloudbot.services.bundle import Services


class InjectMiddleware(BaseMiddleware):
    def __init__(self, services: Services, sender: RichSender) -> None:
        self._services = services
        self._sender = sender

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["services"] = self._services
        data["sender"] = self._sender
        return await handler(event, data)
