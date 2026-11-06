"""Admin panel handlers (``/admin``).

Thin aiogram routers: build the ``AdminView`` read model through ``AdminService``
→ render one registry-driven screen → send/edit. Every handler here is reached
only through the identity gate (``middleware/admin_gate.py``); none of them
re-checks ``is_admin``, and none renders a raw engine value.

Every ``adm:`` callback is parsed by the panel's **one codec**
(``presentation/screens/admin/callbacks.py``) — no handler does
``cb.data.split(":")[n]``. A malformed or out-of-date callback is answered with a
correctable screen (``_stale``), never an ``IndexError`` (Task 07, Bug A).
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
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin import registry
from cloudbot.presentation.screens.admin import user_page as user_page_mod
from cloudbot.presentation.screens.admin import users as users_screen_mod
from cloudbot.presentation.screens.admin.actions import (
    action,
    confirmation_screen,
    ADMIN_ACTION_PREFIX,
)
from cloudbot.presentation.screens.admin.callbacks import AdminCallback
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
        notification_subscriptions=subs,
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


# -- every ``adm:`` callback: parse with the codec, then dispatch -------------
# Bug A was a handler reading ``cb.data.split(":")[3]`` from a button whose
# producer had emitted only two components. No handler here splits callback data:
# parsing is the codec's job, it is total, and an invalid result is answered with
# a correctable screen instead of raising.
@router.callback_query(F.data.startswith(f"{ADMIN_ACTION_PREFIX}:"))
async def on_admin_callback(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    parsed = admin_cb.parse(cb.data)
    route = _ROUTES.get(parsed.action) if parsed.valid else None
    if route is None:
        await show_screen(cb, _stale(ctx), ctx=ctx, sender=sender)
        return
    await route(cb, parsed, ctx, services, sender, state, events)


# -- overview / sections -----------------------------------------------------
async def _route_menu(cb, parsed, ctx, services, sender, state, events) -> None:
    overview = await services.admin.overview()
    view = await _view(ctx, services, overview=overview)
    await show_screen(cb, _section("overview").build(view), ctx=ctx, sender=sender)


async def _route_section(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    key = parsed.args[0]
    section = _section(key)
    if section is None:
        await show_screen(cb, _stale(ctx), ctx=ctx, sender=sender)
        return
    view = await _view(ctx, services)
    if key == "users":
        view = await _view(ctx, services, users_page=await services.admin.users_page(limit=PAGE_SIZE))
    await show_screen(cb, section.build(view), ctx=ctx, sender=sender)


async def _route_users_page(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    page = parsed.arg_int(0)
    page_obj = await services.admin.users_page(offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    view = await _view(ctx, services, users_page=page_obj)
    await show_screen(cb, users_screen_mod.users_screen(view, page=page), ctx=ctx, sender=sender)


async def _route_users_search(cb, parsed, ctx, services, sender, state, events) -> None:
    await state.set_state(AdminInput.awaiting_user_search)
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.users.search_title", ctx.locale))
    doc.p(catalog.t("admin.users.search_prompt", ctx.locale))
    markup = inline_markup(
        [[inline(catalog.t("admin.back", ctx.locale), admin_cb.section("users"), intent=Intent.PRIMARY)]]
    )
    await show_screen(cb, Screen(doc=doc, markup=markup), ctx=ctx, sender=sender)


# -- user page ---------------------------------------------------------------
async def _route_user(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    tg_id = parsed.arg_int(0)
    page = await services.admin.user_page(tg_id)
    if page is None:
        await show_screen(cb, _not_found(ctx), ctx=ctx, sender=sender)
        return
    recent = await services.admin.recent_for_user(tg_id, limit=10)
    view = await _view(ctx, services, user_page=page, recent_events=recent)
    await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)


# -- actions -----------------------------------------------------------------
async def _route_ask(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    key, subject = parsed.args
    act = action(key)
    if act is None:
        await show_screen(cb, _stale(ctx), ctx=ctx, sender=sender)
        return
    label = await _subject_label(services, subject)
    await show_screen(cb, confirmation_screen(act, label, subject, ctx.locale), ctx=ctx, sender=sender)


async def _route_cancel(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    subject = parsed.subject
    owner = await services.admin.owner_of(WorkspaceId(subject))
    if owner is not None:
        page = await services.admin.user_page(owner)
        if page is not None:
            view = await _view(ctx, services, user_page=page)
            await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)
            return
    await show_screen(cb, _section("overview").build(await _view(ctx, services)), ctx=ctx, sender=sender)


async def _route_enter(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    ws_id = parsed.subject
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


async def _route_suspend(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    ws_id = parsed.subject
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    await services.admin.suspend_workspace(WorkspaceId(ws_id))
    events.admin_action(
        admin_id=ctx.actor_tg_id, action="suspend", ws_id=ws_id, owner_tg_id=owner
    )
    await _back_to_owner(cb, ctx, services, sender, owner)


async def _route_resume(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    ws_id = parsed.subject
    owner = await services.admin.owner_of(WorkspaceId(ws_id))
    await services.admin.resume_workspace(WorkspaceId(ws_id))
    events.admin_action(
        admin_id=ctx.actor_tg_id, action="resume", ws_id=ws_id, owner_tg_id=owner
    )
    await _back_to_owner(cb, ctx, services, sender, owner)


async def _route_delete(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    ws_id = parsed.subject
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


async def _route_leave(cb, parsed, ctx, services, sender, state, events) -> None:
    await services.admin.leave_workspace(ctx.actor_tg_id)
    # This update's ctx still carries entered mode; render with a cleared one so
    # the banner is not re-added on the way out.
    from dataclasses import replace

    exited_ctx = replace(ctx, admin_tg_id=0, entered_ws_id="", entered_owner_tg_id=0)
    overview = await services.admin.overview()
    view = await _view(ctx, services, overview=overview)
    await show_screen(cb, _section("overview").build(view), ctx=exited_ctx, sender=sender)


# -- notifications -----------------------------------------------------------
async def _route_notify_toggle(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    category = parsed.args[0]
    subs = await services.admin.subscriptions(ctx.actor_tg_id)
    enabled = not subs.get(category, True)
    await services.admin.set_subscription(ctx.actor_tg_id, category, enabled)
    await _show_notifications(cb, ctx, services, sender)


async def _route_notify_all(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    enabled = parsed.args[0] == "on"
    await services.admin.set_all_subscriptions(ctx.actor_tg_id, enabled)
    await _show_notifications(cb, ctx, services, sender)


async def _route_notify_pause(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    paused = parsed.args[0] == "on"
    await services.admin.set_paused(ctx.actor_tg_id, paused)
    await _show_notifications(cb, ctx, services, sender)


async def _show_notifications(cb, ctx, services, sender) -> None:
    view = await _view(ctx, services)
    await show_screen(cb, _section("notify").build(view), ctx=ctx, sender=sender)


async def _route_notify_scope(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    raw = parsed.args[0]
    if raw == "none":
        await services.admin.set_notify_scope(ctx.actor_tg_id, None)
        await _show_notifications(cb, ctx, services, sender)
        return
    tg_id = int(raw)
    # Tapping the scoped user again clears the scope (a toggle).
    current = await services.admin.notify_scope(ctx.actor_tg_id)
    await services.admin.set_notify_scope(
        ctx.actor_tg_id, None if current == tg_id else tg_id
    )
    page = await services.admin.user_page(tg_id)
    if page is not None:
        recent = await services.admin.recent_for_user(tg_id, limit=10)
        view = await _view(ctx, services, user_page=page, recent_events=recent)
        await show_screen(cb, user_page_mod.user_screen(view), ctx=ctx, sender=sender)
    else:
        await _show_notifications(cb, ctx, services, sender)


# -- audit -------------------------------------------------------------------
async def _route_audit_cat(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    category, page = parsed.args[0], parsed.arg_int(1)
    page_obj = await services.admin.audit_page(
        category=category, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE
    )
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page), ctx=ctx, sender=sender)


async def _route_audit_all(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    if parsed.args[0] == "global":
        tg_id = None
        page = parsed.arg_int(1)
    else:
        tg_id = parsed.arg_int(1)
        page = parsed.arg_int(2)
    page_obj = await services.admin.audit_page(
        tg_id=tg_id, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE
    )
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page, tg_id=tg_id), ctx=ctx, sender=sender)


async def _route_audit(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    page = parsed.arg_int(0)
    page_obj = await services.admin.audit_page(
        offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE
    )
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page), ctx=ctx, sender=sender)


async def _route_audit_user(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    tg_id, page = parsed.arg_int(0), parsed.arg_int(1)
    page_obj = await services.admin.audit_page(
        tg_id=tg_id, offset=page * audit_screen_mod.PAGE_SIZE, limit=audit_screen_mod.PAGE_SIZE
    )
    view = await _view(ctx, services, audit_page=page_obj)
    await show_screen(cb, audit_screen_mod.audit_screen(view, page=page, tg_id=tg_id), ctx=ctx, sender=sender)


#: The one dispatch table: action key (from the codec) → route. Adding a section
#: or an action is one codec builder + one entry here; a callback the parser does
#: not know is answered with the stale screen, never a crash.
_ROUTES = {
    admin_cb.MENU: _route_menu,
    admin_cb.SECTION: _route_section,
    admin_cb.USERS_PAGE: _route_users_page,
    admin_cb.USERS_SEARCH: _route_users_search,
    admin_cb.USER: _route_user,
    admin_cb.ASK: _route_ask,
    admin_cb.CANCEL: _route_cancel,
    "enter": _route_enter,
    "suspend": _route_suspend,
    "resume": _route_resume,
    "delete": _route_delete,
    admin_cb.LEAVE: _route_leave,
    admin_cb.NOTIFY_TOGGLE: _route_notify_toggle,
    admin_cb.NOTIFY_ALL: _route_notify_all,
    admin_cb.NOTIFY_PAUSE: _route_notify_pause,
    admin_cb.NOTIFY_SCOPE: _route_notify_scope,
    admin_cb.AUDIT_CAT: _route_audit_cat,
    admin_cb.AUDIT_ALL: _route_audit_all,
    admin_cb.AUDIT: _route_audit,
    admin_cb.AUDIT_USER: _route_audit_user,
}


# -- users search (text step) ------------------------------------------------
@router.message(AdminInput.awaiting_user_search)
async def on_users_search_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    await state.clear()
    query = (message.text or "").strip()
    page_obj = await services.admin.users_page(query=query, limit=PAGE_SIZE)
    view = await _view(ctx, services, users_page=page_obj)
    await show_screen(message, users_screen_mod.users_screen(view, page=0), ctx=ctx, sender=sender)


def _not_found(ctx: UserContext) -> Screen:
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.not_found.title", ctx.locale))
    doc.p(catalog.t("admin.not_found.body", ctx.locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [[inline(catalog.t("admin.back", ctx.locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
        ),
    )


def _stale(ctx: UserContext) -> Screen:
    """A callback the current panel cannot resolve (stale or malformed button).

    The user must never see a crash or nothing at all; the screen names the fix.
    """
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.stale.title", ctx.locale))
    doc.p(catalog.t("admin.stale.body", ctx.locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [[inline(catalog.t("admin.stale.reopen", ctx.locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
        ),
    )
