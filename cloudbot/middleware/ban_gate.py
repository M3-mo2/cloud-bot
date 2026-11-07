"""The ban gate — a middleware, so a banned user is refused everywhere.

This is the only place a user ban is enforced for inbound updates. A handler
added tomorrow, a new command, even ``/start``: all pass through here first. The
gate renders the one refusal screen (reason, and until when if temporary) and
stops the update — never a stack trace, never silence.

An expired temporary ban is not a ban: ``ModerationService.active_user_ban``
returns ``None`` for an ``expires_at`` in the past, so the user is allowed with
no manual unban. Admins are exempt by identity (a ban cannot lock the panel that
lifts it); the service refuses to create an admin ban at all.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cloudbot.core.errors import UserBanned
from cloudbot.middleware.context import UserContext

log = structlog.get_logger("cloudbot.ban_gate")

class BanGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        ctx: UserContext | None = data.get("ctx")
        services = data.get("services")
        moderation = getattr(services, "moderation", None) if services is not None else None
        if ctx is None or moderation is None:
            return await handler(event, data)
        # An admin is never refused here: a broken ban must not lock the owner
        # out of the panel that lifts bans.
        if ctx.is_admin:
            return await handler(event, data)
        try:
            ban = await moderation.active_user_ban(ctx.tg_id)
        except Exception as exc:  # noqa: BLE001 - a gate bug must never brick the bot
            log.warning("ban.gate_error", error=type(exc).__name__)
            return await handler(event, data)
        if ban is None:
            return await handler(event, data)
        await _show_refusal(event, UserBanned(ban.reason, ban.expires_at), ctx, data)
        return None

async def _show_refusal(
    event: Message | CallbackQuery, err: UserBanned, ctx: UserContext, data: dict[str, Any]
) -> None:
    from cloudbot.presentation.mapping.errors import error_screen

    sender = data.get("sender")
    if sender is None:
        return
    screen = error_screen(err, ctx.locale, mode=ctx.mode, is_admin=ctx.is_admin)
    if isinstance(event, CallbackQuery):
        try:
            await sender.answer_callback(event)
        except Exception:  # noqa: BLE001
            pass
        message = event.message
        if message is not None:
            await sender.edit(message.chat.id, message.message_id, screen.doc, markup=screen.markup)
            return
        chat_id = getattr(event.from_user, "id", None)
        if chat_id is not None:
            await sender.send(chat_id, screen.doc, markup=screen.markup)
        return
    await sender.send(event.chat.id, screen.doc, markup=screen.markup)
