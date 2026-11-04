"""Admin/DEBUG-only commands.

The mode gate runs before these routers, so a non-admin never reaches them.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens.screen import Screen
from cloudbot.services.bundle import Services

router = Router(name="admin")


@router.message(Command("health"))
@router.message(Command("diag"))
async def on_diag(
    message: Message, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("health.title", ctx.locale))
    from cloudbot.adapters.cloud_service.errors import translate  # noqa: F401

    engine = services.workspaces._engine  # composition-time access, admin only
    try:
        health = await engine.health()
        ready = await engine.ready()
        meta = await services.workspaces.meta(refresh=True)
        doc.kv_table(
            [
                ("/healthz", str(health.get("status", "?"))),
                ("/readyz", str(ready.get("status", "?"))),
                ("engine version", meta.version),
                ("api_version", meta.api_version),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        doc.p(f"diagnostics failed: {type(exc).__name__}")
    markup = inline_markup(
        [[inline(catalog.t("common.back", ctx.locale), "nav:menu", intent=Intent.PRIMARY)]]
    )
    await show_screen(message, Screen(doc=doc, markup=markup), ctx=ctx, sender=sender)
