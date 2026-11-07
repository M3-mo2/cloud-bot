"""The gate's own callbacks: «✅ تحقّقت» and a chat with no public link.

The middleware exempts ``fj:`` so these two routes are the only way a gated user
can get in — which is why they never depend on the gate having already passed.
"""

from __future__ import annotations

from aiogram import F, Router

from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens.forcejoin import GATE_CHECK, gate_screen
from cloudbot.presentation.screens.screen import Screen
from cloudbot.services.bundle import Services

router = Router(name="forcejoin")

@router.callback_query(F.data == GATE_CHECK)
async def on_gate_check(
    cb, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """Re-check every required chat (bypassing the cache) and admit on success."""
    status = await services.forcejoin.status(ctx.tg_id, force=True)
    if not status.must_join:
        # A short confirmation, then the product the user came for.
        doc = RichDoc(ctx.locale)
        doc.h(2, catalog.t("fj.welcome_title", ctx.locale))
        doc.p(catalog.t("fj.welcome", ctx.locale))
        await sender.edit(
            cb.message.chat.id,
            cb.message.message_id,
            doc,
            markup=None,
        )
        services.forcejoin.clear_user_cache(ctx.tg_id)
        return
    await show_screen(cb, gate_screen(status, ctx.locale), ctx=ctx, sender=sender)

@router.callback_query(F.data.startswith("fj:chat:"))
async def on_gate_chat(
    cb, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """A required chat without a public username: explain, and re-show the gate."""
    status = await services.forcejoin.status(ctx.tg_id)
    await show_screen(cb, gate_screen(status, ctx.locale), ctx=ctx, sender=sender)
