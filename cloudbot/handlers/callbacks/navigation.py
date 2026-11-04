"""Settings/navigation callback handlers not covered by the common router."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from cloudbot.middleware.context import UserContext
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.handlers.render import show_screen
from cloudbot.services.bundle import Services

router = Router(name="navigation")


@router.callback_query(F.data == "nav:clear")
async def on_clear(cb: CallbackQuery, ctx: UserContext, sender: RichSender) -> None:
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.keyboards.styles import Intent
    from cloudbot.presentation.i18n.catalog import catalog
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("common.clear", ctx.locale))
    markup = inline_markup(
        [[inline(catalog.t("common.menu", ctx.locale), "nav:menu", intent=Intent.PRIMARY)]]
    )
    await show_screen(cb, Screen(doc=doc, markup=markup), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:refresh")
async def on_refresh(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    inside = await services.session.current(ctx.tg_id)
    workspaces = await services.workspaces.list(ctx.tg_id)
    await show_screen(
        cb,
        menu_screens.main_menu(
            workspaces, ctx.locale, display_name=ctx.display_name, inside=inside
        ),
        ctx=ctx,
        sender=sender,
    )


@router.callback_query(F.data.startswith("nav:"))
async def on_other_nav(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """Fallback for nav actions whose feature is not in Phase 1 (terminals, etc.)."""
    inside = await services.session.current(ctx.tg_id)
    workspaces = await services.workspaces.list(ctx.tg_id)
    await show_screen(
        cb,
        menu_screens.main_menu(
            workspaces, ctx.locale, display_name=ctx.display_name, inside=inside
        ),
        ctx=ctx,
        sender=sender,
    )
