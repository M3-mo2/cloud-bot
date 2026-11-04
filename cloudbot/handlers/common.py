"""Handlers: /start, /help, /settings, language switching.

Every button is inline; there is no reply keyboard anywhere. ``/start`` sends one
one-time ``ReplyKeyboardRemove`` so a user who still has a legacy persistent reply
keyboard from an older build gets it cleared (see ``docs/DECISIONS.md`` CB-25).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from cloudbot.core.locale import Locale
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.keyboards.markup import remove_reply
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens.language import language_card, language_picker
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents

router = Router(name="common")

#: A zero-width space body: the removal message carries no visible content, only
#: the ``ReplyKeyboardRemove`` markup. Telegram requires a non-empty body.
_EMPTY = "\u200b"


async def _open_menu(
    event: Message | CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
) -> None:
    inside = await services.session.current(ctx.tg_id)
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(
        workspaces, ctx.locale, display_name=ctx.display_name, inside=inside
    )
    await show_screen(event, screen, ctx=ctx, sender=sender)


async def _clear_legacy_keyboard(
    chat_id: int, locale: Locale, sender: RichSender, services: Services, tg_id: int
) -> None:
    """Clear a legacy reply keyboard ONCE per user, leaving nothing visible.

    The carrier message's body is a single zero-width space, so sending it on every
    `/start` put a permanent blank message in the chat. Two guards: an already-cleared
    user is skipped (persisted, so a restart cannot repeat it), and the carrier is
    deleted immediately so even the one time leaves no blank message.
    """
    if await services.account.legacy_keyboard_cleared(tg_id):
        return
    doc = RichDoc(locale)
    doc.p(_EMPTY)
    sent = await sender.send(chat_id, doc, markup=remove_reply())
    message_id = getattr(sent, "message_id", None)
    if message_id:
        await sender.delete(chat_id, message_id)
    # Recorded even if the delete was refused: retrying forever would re-spam the user,
    # and the keyboard is cleared by then in either case.
    await services.account.mark_legacy_keyboard_cleared(tg_id)


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
    # Returning user: clear a legacy persistent reply keyboard at most once, then show
    # the menu. If they are inside a workspace, resume it directly (task 10): the
    # persisted session survives a restart/redeploy, so `/start` puts them back
    # inside instead of re-asking.
    await _clear_legacy_keyboard(message.chat.id, user.locale, sender, services, ctx.tg_id)
    from cloudbot.presentation.screens import workspaces as ws_screens

    inside = await services.session.current(ctx.tg_id)
    if inside is not None:
        screen = ws_screens.workspace_detail(
            inside, user.locale, active_run=inside.summary.runs_active > 0
        )
        await show_screen(message, screen, ctx=ctx, sender=sender)
        return
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(workspaces, user.locale, display_name=ctx.display_name)
    await show_screen(message, screen, ctx=ctx, sender=sender)


@router.message(Command("help"))
async def on_help(message: Message, ctx: UserContext, sender: RichSender, services: Services) -> None:
    inside = await services.session.current(ctx.tg_id)
    await show_screen(message, menu_screens.help_screen(ctx.locale, inside=inside), ctx=ctx, sender=sender)


@router.message(Command("settings"))
async def on_settings(message: Message, ctx: UserContext, sender: RichSender, services: Services) -> None:
    inside = await services.session.current(ctx.tg_id)
    await show_screen(message, menu_screens.settings_screen(ctx.locale, inside=inside), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:menu")
async def on_menu_cb(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    await _open_menu(cb, ctx, services, sender)


@router.callback_query(F.data == "nav:settings")
async def on_settings_cb(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    inside = await services.session.current(ctx.tg_id)
    await show_screen(cb, menu_screens.settings_screen(ctx.locale, inside=inside), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:help")
async def on_help_cb(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    inside = await services.session.current(ctx.tg_id)
    await show_screen(cb, menu_screens.help_screen(ctx.locale, inside=inside), ctx=ctx, sender=sender)


@router.callback_query(F.data == "nav:language")
async def on_language_cb(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    inside = await services.session.current(ctx.tg_id)
    await show_screen(cb, language_picker(ctx.locale, inside=inside), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("lang:"))
async def on_language(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    target = Locale(cb.data.split(":", 1)[1])
    await services.account.set_locale(ctx.tg_id, target)
    # Edit the current message into the menu in the new locale.
    inside = await services.session.current(ctx.tg_id)
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = menu_screens.main_menu(
        workspaces, target, display_name=ctx.display_name, inside=inside
    )
    await sender.answer_callback(cb)
    if cb.message is not None:
        await sender.edit(cb.message.chat.id, cb.message.message_id, screen.doc, markup=screen.markup)


@router.callback_query(F.data == "nav:cancel")
async def on_cancel(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    await _open_menu(cb, ctx, services, sender)


@router.callback_query(F.data == "nav:noop")
async def on_noop(cb: CallbackQuery) -> None:
    await cb.answer()
