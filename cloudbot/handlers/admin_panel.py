"""Admin panel handlers (``/admin``).

Thin aiogram routers: build the ``AdminView`` read model through ``AdminService``
→ render one registry-driven screen → send/edit. Every handler here is reached
only through the identity gate (``middleware/admin_gate.py``); none of them
re-checks ``is_admin``, and none renders a raw engine value.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.domain.admin import AdminView
from cloudbot.domain.events import EventCategory
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens.admin import audit as audit_screen_mod
from cloudbot.presentation.screens.admin import registry
from cloudbot.presentation.screens.admin import user_page as user_page_mod
from cloudbot.presentation.screens.admin import users as users_screen_mod
from cloudbot.presentation.screens.admin.actions import (
    action,
    confirmation_screen,
    ADMIN_ACTION_PREFIX,
)
from cloudbot.presentation.screens.screen import Screen
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents
from cloudbot.states.admin_input import AdminInput

router = Router(name="admin_panel")

PAGE_SIZE = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _section(key: str):
    for section in registry.all():
        if section.key == key:
            return section
    return None


async def _view(
    ctx: UserContext,
    services: Services,
    *,
    now: datetime | None = None,
    users_page=None,
    user_page=None,
    audit_page=None,
    recent_events=None,
    overview=None,
) -> AdminView:
    entered = await services.admin.entered(ctx.actor_tg_id)
    subs = await services.admin.subscriptions(ctx.actor_tg_id)
    cursor, paused = await services.admin.notify_state(ctx.actor_tg_id)
    scope = await services.admin.notify_scope(ctx.actor_tg_id)
    return AdminView(
        locale=ctx.locale,
        now=now or _now(),
        overview=overview,
        users_page=users_page,
        user_page=user_page,
        audit_page=audit_page,
        recent_events=recent_events or [],
        subscriptions=subs,
        notify_paused=paused,
        notify_scope=scope,
        notification_cursor=cursor,
        entered=entered,
    )


# -- entry -------------------------------------------------------------------
@router.message(Command("admin"))
async def on_admin(message: Message, ctx: UserContext, services: Services, sender: RichSender) -> None:
    overview = await services.admin.overview()
    view = await _view(ctx, services, overview=overview)
    await show_screen(message, _section("overview").build(view), ctx=ctx, sender=sender)


@router.message(Command("notifications"))
async def on_notifications(
    message: Message, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """The quiet switch, reachable without going through the overview."""
    await show_screen(message, _section("notify").build(await _view(ctx, services)), ctx=ctx, sender=sender)


@router.callback_query(F.data == "adm:menu")
async def on_menu(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    overview = await services.admin.overview()
    view = await _view(ctx, services, overview=overview)
    await show_screen(cb, _section("overview").build(view), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:sec:"))
async def on_section(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    key = cb.data.split(":", 2)[2]
    section = _section(key)
    if section is None:
        await show_screen(cb, _not_found(ctx), ctx=ctx, sender=sender)
        return
    view = await _view(ctx, services)
    if key == "users":
        view = await _view(ctx, services, users_page=await services.admin.users_page(limit=PAGE_SIZE))
    await show_screen(cb, section.build(view), ctx=ctx, sender=sender)


# -- users list --------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:users_page:"))
async def on_users_page(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    page = int(cb.data.split(":", 2)[2])
    page_obj = await services.admin.users_page(offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    view = await _view(ctx, services, users_page=page_obj)
    await show_screen(cb, users_screen_mod.users_screen(view, page=page), ctx=ctx, sender=sender)


@router.callback_query(F.data == "adm:users_search")
async def on_users_search(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    await state.set_state(AdminInput.awaiting_user_search)
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.users.search_title", ctx.locale))
    doc.p(catalog.t("admin.users.search_prompt", ctx.locale))
    markup = inline_markup(
        [[inline(catalog.t("admin.back", ctx.locale), "adm:sec:users", intent=Intent.PRIMARY)]]
    )
    await show_screen(cb, Screen(doc=doc, markup=markup), ctx=ctx, sender=sender)


@router.message(AdminInput.awaiting_user_search)
async def on_users_search_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    await state.clear()
    query = (message.text or "").strip()
    page_obj = await services.admin.users_page(query=query, limit=PAGE_SIZE)
    view = await _view(ctx, services, users_page=page_obj)
    await show_screen(message, users_screen_mod.users_screen(view, page=0), ctx=ctx, sender=sender)


# -- user page ---------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:user:"))
async def on_user(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    tg_id = int(cb.data.split(":", 2)[2])
    page = await services.admin.user_page(tg_id)
    if page is None:
        await show_screen(cb, _not_found(ctx), ctx=ctx, sender=sender)
        return
    recent = await services.admin.recent_for_user(tg_id, limit=10)
    view = await _view(ctx, services, user_page=page, recent_events=recent)
    await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)


# -- actions -----------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{ADMIN_ACTION_PREFIX}:ask:"))
async def on_action_ask(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    parts = cb.data.split(":", 3)
    # adm:ask:<key>:<subject>
    key, subject = parts[2], parts[3]
    act = action(key)
    if act is None:
        await show_screen(cb, _not_found(ctx), ctx=ctx, sender=sender)
        return
    label = await _subject_label(services, subject)
    await show_screen(cb, confirmation_screen(act, label, subject, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:cancel:"))
async def on_action_cancel(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    subject = cb.data.split(":", 2)[2]
    owner = await services.admin.owner_of(WorkspaceId(subject))
    if owner is not None:
        page = await services.admin.user_page(owner)
        if page is not None:
            view = await _view(ctx, services, user_page=page)
            await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)
            return
    await show_screen(cb, _section("overview").build(await _view(ctx, services)), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:enter:"))
async def on_enter(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, events: RequestEvents
) -> None:
    ws_id = cb.data.split(":", 2)[2]
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    if owner is None:
        await show_screen(cb, _not_found(ctx), ctx=ctx, sender=sender)
        return
    await services.admin.enter_workspace(ctx.actor_tg_id, ws_id, owner)
    events.admin_action(admin_id=ctx.actor_tg_id, action="enter", ws_id=ws_id, owner_tg_id=owner)
    # Entered mode is now on, but this update's ctx was resolved before the call,
    # so build an entered ctx for this one render (the banner follows from here).
    from dataclasses import replace

    entered_ctx = replace(
        ctx,
        admin_tg_id=ctx.actor_tg_id,
        entered_ws_id=ws_id,
        entered_owner_tg_id=owner,
    )
    section = _section("workspace")
    view = await _view(ctx, services)
    await show_screen(cb, section.build(view), ctx=entered_ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:suspend:"))
async def on_suspend(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, events: RequestEvents
) -> None:
    ws_id = cb.data.split(":", 2)[2]
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    await services.admin.suspend_workspace(WorkspaceId(ws_id))
    events.admin_action(
        admin_id=ctx.actor_tg_id, action="suspend", ws_id=ws_id, owner_tg_id=owner
    )
    await _back_to_owner(cb, ctx, services, sender, owner)


@router.callback_query(F.data.startswith("adm:resume:"))
async def on_resume(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, events: RequestEvents
) -> None:
    ws_id = cb.data.split(":", 2)[2]
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    await services.admin.resume_workspace(WorkspaceId(ws_id))
    events.admin_action(
        admin_id=ctx.actor_tg_id, action="resume", ws_id=ws_id, owner_tg_id=owner
    )
    await _back_to_owner(cb, ctx, services, sender, owner)


@router.callback_query(F.data.startswith("adm:delete:"))
async def on_delete(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, events: RequestEvents
) -> None:
    ws_id = cb.data.split(":", 2)[2]
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    await services.admin.delete_workspace(WorkspaceId(ws_id))
    events.admin_action(
        admin_id=ctx.actor_tg_id, action="delete", ws_id=ws_id, owner_tg_id=owner
    )
    await _back_to_owner(cb, ctx, services, sender, owner)


async def _subject_label(services: Services, subject: str) -> str:
    try:
        ws = await services.admin.get_workspace(WorkspaceId(subject))
        return f"{ws.name} ({ws.id})"
    except Exception:  # noqa: BLE001
        return subject


async def _back_to_owner(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, owner: int | None
) -> None:
    if owner is not None:
        page = await services.admin.user_page(owner)
        if page is not None:
            view = await _view(ctx, services, user_page=page)
            await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)
            return
    await show_screen(cb, _section("overview").build(await _view(ctx, services)), ctx=ctx, sender=sender)


@router.callback_query(F.data == "adm:leave")
async def on_leave(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    await services.admin.leave_workspace(ctx.actor_tg_id)
    # This update's ctx still carries entered mode; render with a cleared one so
    # the banner is not re-added on the way out.
    from dataclasses import replace

    exited_ctx = replace(ctx, admin_tg_id=0, entered_ws_id="", entered_owner_tg_id=0)
    overview = await services.admin.overview()
    view = await _view(ctx, services, overview=overview)
    await show_screen(cb, _section("overview").build(view), ctx=exited_ctx, sender=sender)


# -- notifications -----------------------------------------------------------
@router.callback_query(F.data.startswith("adm:notify_toggle:"))
async def on_notify_toggle(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    category = cb.data.split(":", 2)[2]
    subs = await services.admin.subscriptions(ctx.actor_tg_id)
    enabled = not subs.get(category, True)
    await services.admin.set_subscription(ctx.actor_tg_id, category, enabled)
    await _show_notifications(cb, ctx, services, sender)


@router.callback_query(F.data.startswith("adm:notify_all:"))
async def on_notify_all(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    enabled = cb.data.split(":", 2)[2] == "on"
    await services.admin.set_all_subscriptions(ctx.actor_tg_id, enabled)
    await _show_notifications(cb, ctx, services, sender)


@router.callback_query(F.data.startswith("adm:notify_pause:"))
async def on_notify_pause(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    paused = cb.data.split(":", 2)[2] == "on"
    await services.admin.set_paused(ctx.actor_tg_id, paused)
    await _show_notifications(cb, ctx, services, sender)


async def _show_notifications(cb, ctx, services, sender) -> None:
    view = await _view(ctx, services)
    await show_screen(cb, _section("notify").build(view), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:notify_scope:"))
async def on_notify_scope(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    raw = cb.data.split(":", 2)[2]
    if raw == "none":
        await services.admin.set_notify_scope(ctx.actor_tg_id, None)
    else:
        tg_id = int(raw)
        # Tapping the scoped user again clears the scope (a toggle).
        current = await services.admin.notify_scope(ctx.actor_tg_id)
        await services.admin.set_notify_scope(
            ctx.actor_tg_id, None if current == tg_id else tg_id
        )
    page = await services.admin.user_page(int(raw)) if raw != "none" else None
    if page is not None:
        view = await _view(ctx, services, user_page=page)
        recent = await services.admin.recent_for_user(int(raw), limit=10)
        view = await _view(ctx, services, user_page=page, recent_events=recent)
        await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)
    else:
        await _show_notifications(cb, ctx, services, sender)


# -- audit -------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:audit_cat:"))
async def on_audit_cat(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    parts = cb.data.split(":")
    category, page = parts[2], int(parts[3])
    page_obj = await services.admin.audit_page(category=category, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE)
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:audit_all:"))
async def on_audit_all(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    scope, page = cb.data.split(":")[2], int(cb.data.split(":")[3])
    tg_id = int(scope.split(":", 1)[1]) if scope.startswith("user:") else None
    page_obj = await services.admin.audit_page(
        tg_id=tg_id, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE
    )
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page, tg_id=tg_id), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:audit:"))
async def on_audit(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    page = int(cb.data.split(":", 2)[2])
    page_obj = await services.admin.audit_page(offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE)
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("adm:audit_user:"))
async def on_audit_user(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    parts = cb.data.split(":")
    tg_id, page = int(parts[2]), int(parts[3])
    page_obj = await services.admin.audit_page(tg_id=tg_id, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE)
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page, tg_id=tg_id), ctx=ctx, sender=sender)


def _not_found(ctx: UserContext) -> Screen:
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.not_found.title", ctx.locale))
    doc.p(catalog.t("admin.not_found.body", ctx.locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [[inline(catalog.t("admin.back", ctx.locale), "adm:menu", intent=Intent.PRIMARY)]]
        ),
    )
