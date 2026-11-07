"""The mandatory-subscription gate — a middleware, not a per-handler check.

A user who has not joined every enabled chat cannot use any bot feature. Being a
middleware is the point: a handler added tomorrow cannot forget the gate.

Two narrow, documented exemptions (see ``_exempt``):

* the gate's own ``fj:`` callbacks — the «تحقّقت» button must always be able to
  re-check (otherwise a gated user could not press the only button that admits
  them);
* ``/start`` — the one command that must render the gate itself (the handler
  calls the same service, so the copy is identical).

The **anti-lockout rule** lives in the service (``status`` never reports an
unverifiable chat as missing), so this middleware only renders its verdict. The
admin (``Settings.is_admin``) is exempt: a misconfiguration must not lock the
owner out of the panel that would fix it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cloudbot.middleware.context import UserContext

log = structlog.get_logger("cloudbot.forcejoin_gate")

#: The gate's own callback namespace; never gated so «تحقّقت» always works.
GATE_PREFIX = "fj:"

def _command(text: str | None) -> str:
    if not text:
        return ""
    return text.split()[0].split("@")[0].lower()

def _exempt(event: TelegramObject) -> bool:
    if isinstance(event, CallbackQuery) and (event.data or "").startswith(GATE_PREFIX):
        return True
    if isinstance(event, Message) and _command(event.text) == "/start":
        return True
    return False

class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        ctx: UserContext | None = data.get("ctx")
        # The owner is exempt; otherwise a broken gate is un-fixable from the panel.
        if ctx is None or ctx.is_admin:
            return await handler(event, data)
        if _exempt(event):
            return await handler(event, data)

        services = data.get("services")
        if services is None:
            return await handler(event, data)
        try:
            status = await services.forcejoin.status(ctx.tg_id)
        except Exception as exc:  # noqa: BLE001 - a gate bug must never brick the bot
            log.warning("forcejoin.gate_error", error=type(exc).__name__)
            return await handler(event, data)
        if not status.must_join:
            return await handler(event, data)

        await _show_gate(event, status, ctx, data)
        return None

async def _show_gate(event: Message | CallbackQuery, status, ctx: UserContext, data: dict[str, Any]) -> None:
    """Render the one gate screen (in place when the event has a message)."""
    from cloudbot.presentation.screens.forcejoin import gate_screen

    sender = data.get("sender")
    screen = gate_screen(status, ctx.locale)
    if sender is None:
        return
    if isinstance(event, CallbackQuery):
        try:
            await sender.answer_callback(event)
        except Exception:  # noqa: BLE001
            pass
        message = event.message
        if message is not None:
            await sender.edit(
                message.chat.id,
                message.message_id,
                screen.doc,
                markup=screen.markup,
            )
            return
        chat_id = getattr(event.from_user, "id", None)
        if chat_id is not None:
            await sender.send(chat_id, screen.doc, markup=screen.markup)
        return
    await sender.send(event.chat.id, screen.doc, markup=screen.markup)
