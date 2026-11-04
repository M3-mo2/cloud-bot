"""Handlers: /start, /help, /settings, language switching, and the reply menu."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from cloudbot.core.locale import Locale
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens.language import language_card, language_picker
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents

router = Router(name="common")


async def _open_menu(
    event: Message | CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
) -> None:
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(workspaces, ctx.locale, display_name=ctx.display_name)
    await show_screen(event, screen, ctx=ctx, sender=sender)


@router.message(Command("start"))
async def on_start(
    message: Message,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    events: RequestEvents,
) -> None:
    user, is_new = await services.account.onboard(
        ctx.tg_id, default_locale=ctx.locale
    )
    if is_new:
        events.user_new(tg_id=ctx.tg_id, name=ctx.display_name)
        await show_screen(message, language_card(ctx.locale), ctx=ctx, sender=sender)
        return
    # Returning user: menu, with the persistent reply menu attached.
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(workspaces, user.locale, display_name=ctx.display_name)
    markup = menu_screens.reply_menu(user.locale)
    await sender.send(message.chat.id, screen.doc, markup=markup)


@router.message(Command("help"))
async def on_help(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, menu_screens.help_screen(ctx.locale), ctx=ctx, sender=sender)


@router.message(Command("settings"))
async def on_settings(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, menu_screens.settings_screen(ctx.locale), ctx=ctx, sender=sender)


@router.message(F.text == "❓ مساعدة")
@router.message(F.text == "❓ Help")
async def on_reply_help(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, menu_screens.help_screen(ctx.locale), ctx=ctx, sender=sender)


@router.message(F.text == "⚙️ الإعدادات")
@router.message(F.text == "⚙️ Settings")
async def on_reply_settings(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, menu_screens.settings_screen(ctx.locale), ctx=ctx, sender=sender)


@router.message(F.text == "📦 المساحات")
@router.message(F.text == "📦 Workspaces")
async def on_reply_workspaces(
    message: Message, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    from cloudbot.presentation.screens.workspaces import workspace_list

    workspaces = await services.workspaces.list(ctx.tg_id)
    await show_screen(message, workspace_list(workspaces, ctx.locale), ctx=ctx, sender=sender)


@router.message(F.text == "🌐 اللغة")
@router.message(F.text == "🌐 Language")
async def on_reply_language(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, language_picker(ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:menu")
async def on_menu_cb(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    await _open_menu(cb, ctx, services, sender)


@router.callback_query(F.data == "nav:settings")
async def on_settings_cb(cb: CallbackQuery, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(cb, menu_screens.settings_screen(ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:language")
async def on_language_cb(cb: CallbackQuery, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(cb, language_picker(ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("lang:"))
async def on_language(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    target = Locale(cb.data.split(":", 1)[1])
    await services.account.set_locale(ctx.tg_id, target)
    # Edit the current message into the menu in the new locale.
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(workspaces, target, display_name=ctx.display_name)
    await sender.answer_callback(cb)
    if cb.message is not None:
        await sender.edit(cb.message.chat.id, cb.message.message_id, screen.doc, markup=screen.markup)
        # Reply keyboards can only be introduced by a send.
        await sender.send(cb.message.chat.id, screen.doc, markup=menu_screens.reply_menu(target))


@router.callback_query(F.data == "nav:cancel")
async def on_cancel(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    await _open_menu(cb, ctx, services, sender)


@router.callback_query(F.data == "nav:noop")
async def on_noop(cb: CallbackQuery) -> None:
    await cb.answer()
