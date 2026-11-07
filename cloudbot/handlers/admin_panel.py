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

import structlog
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
from cloudbot.presentation.screens.admin import forcejoin as forcejoin_screen_mod
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin import registry
from cloudbot.presentation.screens.admin import user_page as user_page_mod
from cloudbot.presentation.screens.admin import users as users_screen_mod
from cloudbot.presentation.screens.admin import wallet as wallet_screen_mod
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

log = structlog.get_logger("cloudbot.admin_panel")

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
    fj_chats = await services.forcejoin.chats()
    fj_enabled = await services.forcejoin.enabled()
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
        forcejoin_chats=fj_chats,
        forcejoin_enabled=fj_enabled,
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


# -- force-join gate ---------------------------------------------------------
async def _show_forcejoin(cb, ctx: UserContext, services: Services, sender: RichSender, *, banner: str = "") -> None:
    view = await _view(ctx, services)
    screen = forcejoin_screen_mod.forcejoin_screen(view)
    if banner:
        from cloudbot.presentation.rich.tags import blockquote, fragment

        screen.doc.prepend_raw(str(blockquote(fragment(banner))))
    await show_screen(cb, screen, ctx=ctx, sender=sender)

async def _route_fj_add(cb, parsed, ctx, services, sender, state, events) -> None:
    """One message, no wizard: the next text message is the chat reference."""
    await state.set_state(AdminInput.awaiting_forcejoin_chat)
    view = await _view(ctx, services)
    await show_screen(cb, forcejoin_screen_mod.add_prompt(view), ctx=ctx, sender=sender)

async def _route_fj_toggle(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    chat_id = parsed.arg_int(0)
    chat = await services.forcejoin.recheck(chat_id)
    if chat is None:
        await show_screen(cb, _stale(ctx), ctx=ctx, sender=sender)
        return
    await services.forcejoin.set_chat_enabled(chat_id, not chat.enabled)
    refreshed = await services.forcejoin.recheck(chat_id)
    banner = ""
    if refreshed is not None and not refreshed.bot_is_admin:
        banner = catalog.t("admin.fj.banner_not_admin", ctx.locale, chat=refreshed.label)
    await _show_forcejoin(cb, ctx, services, sender, banner=banner)

async def _route_fj_remove(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    chat_id = parsed.arg_int(0)
    chat = await services.forcejoin.recheck(chat_id)
    if chat is None:
        await show_screen(cb, _stale(ctx), ctx=ctx, sender=sender)
        return
    await services.forcejoin.remove_chat(chat_id)
    await _show_forcejoin(cb, ctx, services, sender)

async def _route_fj_switch(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    enabled = parsed.args[0] == "on"
    await services.forcejoin.set_enabled(enabled)
    await _show_forcejoin(cb, ctx, services, sender)

async def _route_fj_check(cb, parsed, ctx, services, sender, state, events) -> None:
    """The admin's end-to-end proof: this admin's own membership, measured live."""
    results = await services.forcejoin.probe(ctx.actor_tg_id)
    rows = [(chat.label, result.subscribed, result.unknown) for chat, result in results]
    ok = bool(rows) and all(subscribed or unknown for _, subscribed, unknown in rows)
    view = await _view(ctx, services)
    await show_screen(
        cb,
        forcejoin_screen_mod.check_result_screen(view, rows=rows, ok=ok),
        ctx=ctx,
        sender=sender,
    )

# -- wallet + subscription controls -----------------------------------------
async def _open_wallet(
    cb, ctx: UserContext, services: Services, sender: RichSender, tg_id: int, *, banner=None
) -> None:
    wallet = await services.admin.wallet(tg_id)
    subscription = await services.admin.subscription(tg_id)
    payments = await services.admin.payments(tg_id)
    view = await _view(ctx, services)
    screen = wallet_screen_mod.wallet_screen(
        view,
        tg_id=tg_id,
        wallet=wallet,
        subscription=subscription,
        payments=payments,
    )
    if banner:
        from cloudbot.presentation.rich.tags import fragment

        from cloudbot.presentation.rich.tags import blockquote

        screen.doc.prepend_raw(str(blockquote(fragment(banner))))
    await show_screen(cb, screen, ctx=ctx, sender=sender)

async def _route_wallet(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    await _open_wallet(cb, ctx, services, sender, parsed.arg_int(0))

async def _route_wallet_op(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    op, tg_id_raw, step, arg = parsed.args
    tg_id = int(tg_id_raw)
    if step == "go":
        await _wallet_perform(cb, ctx, services, sender, state, op, tg_id, arg, events)
        return
    # ask: the prompt appropriate to the operation.
    await _wallet_ask(cb, ctx, services, sender, state, op, tg_id)

async def _route_wallet_plan(cb, parsed: AdminCallback, ctx, services, sender, state, events) -> None:
    op, tg_id_raw, plan_code, step, arg = parsed.args
    tg_id = int(tg_id_raw)
    if step == "go":
        await _wallet_perform(cb, ctx, services, sender, state, op, tg_id, plan_code, events)
        return
    # ask: the plan is chosen; now ask for the days.
    await state.set_state(AdminInput.awaiting_wallet_amount)
    await state.update_data(wop=op, wtg=tg_id, wplan=plan_code)
    await show_screen(
        cb,
        _wallet_prompt(ctx.locale, wallet_screen_mod.OPS[op], tg_id),
        ctx=ctx,
        sender=sender,
    )

async def _wallet_ask(
    cb, ctx: UserContext, services: Services, sender: RichSender, state, op: str, tg_id: int
) -> None:
    if op == "grant":
        await show_screen(cb, wallet_screen_mod.plan_prompt(tg_id, op, ctx.locale), ctx=ctx, sender=sender)
        return
    if op == "revoke":
        await state.set_state(AdminInput.awaiting_wallet_reason)
        await state.update_data(wop=op, wtg=tg_id, wplan="", wamount=0)
        await show_screen(cb, _wallet_prompt(ctx.locale, "admin.wallet.prompt_reason", tg_id), ctx=ctx, sender=sender)
        return
    if op == "refund":
        await state.set_state(AdminInput.awaiting_wallet_charge)
        await state.update_data(wop=op, wtg=tg_id)
        await show_screen(cb, _wallet_prompt(ctx.locale, "admin.wallet.prompt_refund", tg_id), ctx=ctx, sender=sender)
        return
    current = await services.admin.subscription(tg_id)
    plan_code = current.plan_code if current is not None else "month"
    await state.set_state(AdminInput.awaiting_wallet_amount)
    await state.update_data(wop=op, wtg=tg_id, wplan=plan_code)
    await show_screen(cb, _wallet_prompt(ctx.locale, wallet_screen_mod.OPS[op], tg_id), ctx=ctx, sender=sender)

def _wallet_prompt(locale, key: str, tg_id: int) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.wallet.title", locale))
    doc.p(catalog.t(key, locale))
    row = [
        inline(
            catalog.t("admin.wallet.cancel", locale),
            admin_cb.wallet(tg_id),
            intent=Intent.PRIMARY,
        )
    ]
    return Screen(doc=doc, markup=inline_markup([row]))

async def _wallet_perform(
    cb, ctx: UserContext, services: Services, sender: RichSender, state, op: str, tg_id: int, arg: str,
    events: RequestEvents,
) -> None:
    data = await state.get_data()
    await state.clear()
    actor = ctx.actor_tg_id
    reason = str(data.get("wreason") or arg or "")
    amount = int(data.get("wamount") or 0)
    plan_code = str(data.get("wplan") or "")
    banner = ""
    if op == "credit":
        wallet = await services.admin.credit(tg_id, amount, reason, actor=actor)
        banner = catalog.t("admin.wallet.done_credit", ctx.locale, stars=_d(amount, ctx.locale), balance=_d(wallet.balance_stars, ctx.locale))
    elif op == "debit":
        wallet = await services.admin.debit(tg_id, amount, reason, actor=actor)
        banner = catalog.t("admin.wallet.done_debit", ctx.locale, stars=_d(amount, ctx.locale), balance=_d(wallet.balance_stars, ctx.locale))
    elif op == "set":
        wallet = await services.admin.set_balance(tg_id, amount, reason, actor=actor)
        banner = catalog.t("admin.wallet.done_set", ctx.locale, stars=_d(wallet.balance_stars, ctx.locale))
    elif op in ("grant", "extend", "shorten"):
        days = amount if op != "shorten" else -amount
        await services.admin.grant(tg_id, plan_code or "month", days, reason, actor=actor)
        if op == "grant":
            banner = catalog.t("admin.wallet.done_grant", ctx.locale, plan=catalog.t(f"plan.{plan_code}", ctx.locale), days=_d(amount, ctx.locale))
        elif op == "extend":
            banner = catalog.t("admin.wallet.done_extend", ctx.locale, days=_d(amount, ctx.locale))
        else:
            banner = catalog.t("admin.wallet.done_shorten", ctx.locale, days=_d(amount, ctx.locale))
    elif op == "revoke":
        await services.admin.revoke(tg_id, reason, actor=actor)
        banner = catalog.t("admin.wallet.done_revoke", ctx.locale)
    elif op == "refund":
        charge_id = arg
        existing = await services.billing.payment(charge_id)
        if existing is None or existing.tg_id != tg_id:
            banner = catalog.t("pay.nothing_to_refund", ctx.locale)
        elif not existing.refundable:
            banner = catalog.t("pay.refund_already", ctx.locale)
        else:
            # Telegram first (the money actually leaves), then the ledger
            # reversal. If Telegram refuses, the books are left untouched so the
            # ledger still equals what Telegram believes.
            try:
                await sender.refund_star_payment(tg_id, charge_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("billing.refund_api_failed", error=type(exc).__name__)
                banner = catalog.t("admin.wallet.refund_failed", ctx.locale)
            else:
                await services.billing.refund(
                    tg_id=tg_id,
                    charge_id=charge_id,
                    actor=actor,
                    reason=reason or f"admin refund {charge_id}",
                )
                banner = catalog.t("admin.wallet.done_refund", ctx.locale)
    events.admin_action(
        admin_id=actor,
        action=f"wallet_{op}",
        owner_tg_id=tg_id,
        params={"amount": amount, "plan": plan_code, "reason": reason},
    )
    await _open_wallet(cb, ctx, services, sender, tg_id, banner=banner)

def _d(value: int, locale) -> str:
    from cloudbot.presentation.i18n.formatters import localize_digits

    return localize_digits(str(value), locale)

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
    admin_cb.WALLET: _route_wallet,
    admin_cb.WALLET_OP: _route_wallet_op,
    admin_cb.WALLET_PLAN: _route_wallet_plan,
    admin_cb.FJ_ADD: _route_fj_add,
    admin_cb.FJ_TOGGLE: _route_fj_toggle,
    admin_cb.FJ_REMOVE: _route_fj_remove,
    admin_cb.FJ_SWITCH: _route_fj_switch,
    admin_cb.FJ_CHECK: _route_fj_check,
}


# -- force-join add (the one-message step) -----------------------------------
@router.message(AdminInput.awaiting_forcejoin_chat)
async def on_forcejoin_chat(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Resolve, verify the bot is an admin, store — or refuse with the exact reason."""
    await state.clear()
    reference = (message.text or "").strip()
    banner = ""
    try:
        chat = await services.forcejoin.add_chat(reference, added_by=ctx.actor_tg_id)
    except Exception as exc:  # noqa: BLE001 - mapped to one refusal line below
        reason = getattr(exc, "reason", "not_found")
        banner = catalog.t(f"admin.fj.refuse.{reason}", ctx.locale)
    else:
        banner = catalog.t(
            "admin.fj.added", ctx.locale, chat=chat.label, kind=catalog.t(f"admin.fj.kind.{chat.kind}", ctx.locale)
        )
    view = await _view(ctx, services)
    screen = forcejoin_screen_mod.forcejoin_screen(view)
    from cloudbot.presentation.rich.tags import blockquote, fragment

    screen.doc.prepend_raw(str(blockquote(fragment(banner))))
    await show_screen(message, screen, ctx=ctx, sender=sender, force_send=True)

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


@router.message(AdminInput.awaiting_wallet_amount)
async def on_wallet_amount(
    message: Message, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    op = str(data.get("wop") or "")
    tg_id = int(data.get("wtg") or 0)
    text = (message.text or "").strip().lstrip("+")
    try:
        amount = int(text)
    except ValueError:
        amount = 0
    if amount <= 0:
        await show_screen(
            message,
            _wallet_prompt(ctx.locale, wallet_screen_mod.OPS.get(op, "admin.wallet.prompt_credit"), tg_id),
            ctx=ctx,
            sender=sender,
            force_send=True,
        )
        return
    await state.update_data(wamount=amount)
    await state.set_state(AdminInput.awaiting_wallet_reason)
    doc = RichDoc(ctx.locale)
    doc.h(2, catalog.t("admin.wallet.title", ctx.locale))
    doc.p(catalog.t("admin.wallet.step_reason", ctx.locale))
    doc.p(catalog.t("admin.wallet.prompt_reason", ctx.locale))
    row = [inline(catalog.t("admin.wallet.cancel", ctx.locale), admin_cb.wallet(tg_id), intent=Intent.PRIMARY)]
    await show_screen(message, Screen(doc=doc, markup=inline_markup([row])), ctx=ctx, sender=sender, force_send=True)

@router.message(AdminInput.awaiting_wallet_reason)
async def on_wallet_reason(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext, events: RequestEvents
) -> None:
    data = await state.get_data()
    op = str(data.get("wop") or "")
    tg_id = int(data.get("wtg") or 0)
    text = (message.text or "").strip()
    reason = "" if text in ("/skip", "") else text
    await state.update_data(wreason=reason)
    await _wallet_perform(
        message, ctx, services, sender, state, op, tg_id, reason, events
    )

@router.message(AdminInput.awaiting_wallet_charge)
async def on_wallet_charge(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext, events: RequestEvents
) -> None:
    data = await state.get_data()
    tg_id = int(data.get("wtg") or 0)
    charge_id = (message.text or "").strip()
    await _wallet_perform(message, ctx, services, sender, state, "refund", tg_id, charge_id, events)

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
