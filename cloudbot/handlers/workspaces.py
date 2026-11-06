"""Workspace handlers: list, detail, create wizard, lifecycle actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import InvalidInput
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import decode, decode_required
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents
from cloudbot.states.input import Input

router = Router(name="workspaces")


def _list_screen(
    workspaces,
    locale,
    *,
    page: int = 0,
    inside=None,
    confirmation: str | None = None,
):
    """One entry point for every render of the workspace list, paged."""
    from cloudbot.presentation import pagination

    window, total_pages, has_more = pagination.window(workspaces, page)
    page = max(0, min(page, total_pages - 1))
    return ws_screens.workspace_list(
        window,
        locale,
        page=page,
        total_pages=total_pages,
        has_more=has_more,
        inside=inside,
        confirmation=confirmation,
    )


async def _show_list(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    page: int = 0,
    *,
    confirmation: str | None = None,
) -> None:
    workspaces = await services.workspaces.list(ctx.tg_id)
    inside = await services.session.current(ctx.tg_id)
    screen = _list_screen(
        workspaces, ctx.locale, page=page, inside=inside, confirmation=confirmation
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:list"))
async def on_list(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    page = int(decoded.key[0]) if decoded.key else 0
    await _show_list(cb, ctx, services, sender, page)


@router.callback_query(F.data.startswith("ws:view"))
async def on_view(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode_required(cb.data, 1)
    ws_id = decoded.key[0]
    # Opening a workspace is entering it, like a login (task 10): persist the
    # session before rendering, so this detail screen is an "inside" screen.
    await services.session.enter(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, active_run=ws.summary.runs_active > 0, mode=ctx.mode
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data == "ws:home")
async def on_home(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """The nav block's "workspace home" entry: render the workspace we are inside."""
    inside = await services.session.current(ctx.tg_id)
    if inside is None:
        await _show_list(cb, ctx, services, sender)
        return
    screen = ws_screens.workspace_detail(
        inside, ctx.locale, active_run=inside.summary.runs_active > 0, mode=ctx.mode
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:more"))
async def on_more(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """Reveal the workspace home's secondary tools.

    These are the destinations a user reaches less often than files, runs and
    terminals, so they start collapsed. Expanding is a re-render, not a navigation:
    the screen the user is looking at stays the screen they are looking at.
    """

    decoded = decode_required(cb.data, 1)
    ws_id = decoded.key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, active_run=ws.summary.runs_active > 0, mode=ctx.mode, more=True
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:less"))
async def on_less(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """Collapse the secondary tools again."""

    decoded = decode_required(cb.data, 1)
    ws_id = decoded.key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, active_run=ws.summary.runs_active > 0, mode=ctx.mode
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:logout"))
async def on_logout(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """Leave the workspace and return to the chooser (the list)."""
    await services.session.leave(ctx.tg_id)
    await _show_list(
        cb,
        ctx,
        services,
        sender,
        confirmation=catalog.t("ws.logout.done", ctx.locale),
    )


# -- create wizard -----------------------------------------------------------
@router.callback_query(F.data == "ws:new")
async def on_new(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    await state.set_state(Input.waiting_name)
    await show_screen(cb, ws_screens.create_name_prompt(ctx.locale), ctx=ctx, sender=sender)


@router.message(Input.waiting_name)
async def on_name(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        screen = ws_screens.create_name_prompt(ctx.locale)
        from cloudbot.presentation.mapping.errors import error_screen
        from cloudbot.core.errors import InvalidInput as II

        reason = "empty" if not name else "too_long"
        await show_screen(message, error_screen(II("name", reason), ctx.locale), ctx=ctx, sender=sender)
        return
    await state.update_data(ws_name=name)
    meta = await services.workspaces.meta()
    profiles = list(meta.profiles.profiles) or []
    if len(profiles) > 1:
        await state.set_state(Input.waiting_profile)
        listing = [(p.name, p.name == meta.default_profile) for p in profiles]
        await show_screen(message, ws_screens.create_profile(listing, ctx.locale, meta.default_profile), ctx=ctx, sender=sender)
        return
    await state.update_data(ws_profile=meta.default_profile)
    await _show_advanced(message, ctx, services, sender, state)


async def _show_advanced(event, ctx, services, sender, state) -> None:
    """Render the advanced create screen with the wizard's current toggles."""
    meta = await services.workspaces.meta()
    data = await state.get_data()
    await state.set_state(Input.waiting_advanced)
    limits = ws_screens.wizard_limits(meta.limits_defaults, data)
    screen = ws_screens.create_advanced(
        limits,
        meta.limits_maxima,
        ctx.locale,
        selected_profile=data.get("ws_profile", meta.default_profile),
        allow_net=limits.allow_net,
        allow_tunnels=limits.allow_tunnels,
    )
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))


@router.callback_query(F.data.startswith("ws:profile"))
async def on_profile(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    decoded = decode_required(cb.data, 1)
    profile = decoded.key[0]
    await state.update_data(ws_profile=profile)
    await _show_advanced(cb, ctx, services, sender, state)


@router.message(Input.waiting_profile)
async def on_profile_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    await state.update_data(ws_profile=message.text.strip())
    await _show_advanced(message, ctx, services, sender, state)


@router.callback_query(F.data.startswith("ws:tgl"))
async def on_toggle(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Flip one boolean switch on the advanced create screen (net / tunnels)."""
    which = decode_required(cb.data, 1).key[0]
    data = await state.get_data()
    key = "ws_allow_net" if which == "net" else "ws_allow_tunnels"
    await state.update_data(**{key: not bool(data.get(key, False))})
    await _show_advanced(cb, ctx, services, sender, state)


async def _do_create(event, ctx, services, sender, state, *, profile: str | None = None, events=None) -> None:
    data = await state.get_data()
    name = data.get("ws_name", "")
    limits = await _wizard_limits(services, data)
    profile = profile or data.get("ws_profile")
    await state.clear()
    ws = await services.workspaces.create(ctx.tg_id, name, profile=profile, limits=limits)
    if events is not None:
        events.workspace_created(tg_id=ctx.tg_id, ws_id=str(ws.id), name=ws.name)
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, confirmation=catalog.t("ws.create.ready", ctx.locale, name=ws.name)
    )
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=True)


async def _wizard_limits(services: Services, data: dict):
    meta = await services.workspaces.meta()
    return ws_screens.wizard_limits(meta.limits_defaults, data)


# -- lifecycle ---------------------------------------------------------------
@router.callback_query(F.data.startswith("ws:create"))
async def on_create(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    """Create from the advanced screen (the wizard's final step)."""
    await _do_create(cb, ctx, services, sender, state, events=events)


@router.callback_query(F.data.startswith("ws:delete_confirm"))
async def on_delete_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.delete_confirm(ws, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:delete"))
async def on_delete(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    events: RequestEvents,
) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await services.workspaces.delete(ctx.tg_id, _wid(ws_id))
    events.workspace_deleted(tg_id=ctx.tg_id, ws_id=str(ws_id), name=ws.name)
    from cloudbot.core.locale import Locale

    doc = _result_doc(ctx.locale, catalog.t("ws.delete.done", ctx.locale, name=ws.name))
    await sender.answer_callback(cb)
    if cb.message is not None:
        from cloudbot.presentation.rich.document import RichDoc

        d = RichDoc(ctx.locale)
        d.blockquote(_frag(catalog.t("ws.delete.done", ctx.locale, name=ws.name)))
        await sender.edit(cb.message.chat.id, cb.message.message_id, d)
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = _list_screen(workspaces, ctx.locale)
    if cb.message is not None:
        await sender.send(cb.message.chat.id, screen.doc, markup=screen.markup)


@router.callback_query(F.data.startswith("ws:stop_confirm"))
async def on_stop_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.stop_confirm(ws, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:stop"))
async def on_stop(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    result = await services.workspaces.stop(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    screen = ws_screens.lifecycle_result(
        ws, ctx.locale, kind="stop",
        stopped_run_count=result.stopped_run_count,
        stopped_session_count=result.stopped_session_count,
        stopped_process_count=result.stopped_process_count,
        changed=result.changed,
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:suspend"))
async def on_suspend(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    events: RequestEvents,
) -> None:
    decoded = decode_required(cb.data, 1)
    # Distinguish "suspend" (do it) from "suspend_confirm" (show confirm): the
    # content-addressed token differs, so compare the action string.
    if "confirm" in decoded.action:
        ws = await services.workspaces.get(ctx.tg_id, _wid(decoded.key[0]))
        await show_screen(cb, ws_screens.suspend_confirm(ws, ctx.locale), ctx=ctx, sender=sender)
        return
    ws_id = decoded.key[0]
    await services.workspaces.suspend(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    events.workspace_suspended(tg_id=ctx.tg_id, ws_id=str(ws_id), name=ws.name)
    await show_screen(cb, ws_screens.lifecycle_result(ws, ctx.locale, kind="suspend"), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:resume"))
async def on_resume(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode_required(cb.data, 1).key[0]
    await services.workspaces.resume(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.lifecycle_result(ws, ctx.locale, kind="resume"), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:renew"))
async def on_renew(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """Renew: a locked workspace's explicit route to the plan picker.

    The button carried the workspace id, but the fix is an account action — buying
    a plan — so the user lands on the plan picker, not on another locked screen.
    """
    from cloudbot.handlers import billing

    decode_required(cb.data, 1)
    await cb.answer()
    await billing.open_plans(cb, ctx, services, sender)


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


def _frag(text: str):
    from cloudbot.presentation.rich.tags import fragment

    return fragment(text)


def _result_doc(locale, text: str):
    from cloudbot.presentation.rich.document import RichDoc

    d = RichDoc(locale)
    d.blockquote(_frag(text))
    return d
