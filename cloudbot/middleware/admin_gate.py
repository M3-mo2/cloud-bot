"""The admin identity gate — one seam.

The panel is gated on ``Settings.is_admin(tg_id)`` (the ``CLOUDBOT_ADMIN_IDS``
allowlist), **never** on the run mode. Being an admin is an identity, not a mode;
``admin_commands`` and the DEV/DEBUG diagnostics are left exactly as they are.

A non-admin must not be able to tell the panel exists: ``/admin`` (and every
``adm:`` callback) for a non-admin is swallowed here, so it falls through to the
same path an unknown command takes — no "you are not an admin" message.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cloudbot.middleware.context import UserContext

log = structlog.get_logger("cloudbot.admin_gate")

#: The commands that open the panel. Only the NEW panel commands are recognised
#: here; the existing DEV/DEBUG diagnostics (``/diag``, ``/health``, ``/raw``,
#: ``/mode``, ``/replay``) are left exactly as they are, gated by the mode table.
ADMIN_COMMANDS: frozenset[str] = frozenset({"/admin", "/notifications"})


def admin_command(text: str) -> str | None:
    """The admin-panel command in ``text`` (without a bot suffix), or ``None``."""
    if not text:
        return None
    command = text.split()[0].split("@")[0].lower()
    return command if command in ADMIN_COMMANDS else None


class AdminGateMiddleware(BaseMiddleware):
    """Runs before the routers; the only place the admin identity is checked."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: UserContext | None = data.get("ctx")
        if not _wants_admin(event):
            return await handler(event, data)
        if ctx is None or not ctx.is_admin:
            # Indistinguishable from an unknown command / dead callback: swallow
            # it, send nothing, so the panel's existence is never revealed.
            log.info("admin.gate.refused")
            return None
        return await handler(event, data)


def _wants_admin(event: TelegramObject) -> bool:
    if isinstance(event, Message) and event.text:
        if admin_command(event.text) is not None:
            return True
    if isinstance(event, CallbackQuery) and event.data:
        if event.data.startswith("adm:"):
            return True
    return False
