"""Workspace handlers: list, detail, create wizard, lifecycle actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import InvalidInput
from cloudbot.handlers import billing
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import decode, decode_required
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import wallet as wallet_screens
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


# -- create wizard: name -> network -> plan -> create ------------------------
async def _show_network(event, ctx, services, sender, state) -> None:
    meta = await services.workspaces.meta()
    data = await state.get_data()
    limits = ws_screens.wizard_network(meta.limits_defaults, data)
    screen = ws_screens.create_network(
        meta.limits_defaults,
        ctx.locale,
        allow_net=limits.allow_net,
        allow_tunnels=limits.allow_tunnels,
    )
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))

async def _show_plan_picker(
    event, ctx, services, sender, state, *, needed_plan: str | None = None
) -> None:
    data = await state.get_data()
    options = await services.workspaces.plan_options(ctx.tg_id)
    balance = (await services.billing.wallet(ctx.tg_id)).balance_stars
    screen = ws_screens.create_plan_picker(
        options,
        balance,
        ctx.locale,
        workspace_name=data.get("ws_name", ""),
        needed_plan=needed_plan,
    )
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))

@router.callback_query(F.data == "ws:new")
async def on_new(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    await state.clear()
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
    await state.set_state(Input.creating)
    await _show_network(message, ctx, services, sender, state)

@router.callback_query(F.data.startswith("ws:tgl"))
async def on_toggle(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """Flip one boolean switch on the network step (net / tunnels)."""
    which = decode_required(cb.data, 1).key[0]
    data = await state.get_data()
    key = "ws_allow_net" if which == "net" else "ws_allow_tunnels"
    await state.update_data(**{key: not bool(data.get(key, False))})
    await _show_network(cb, ctx, services, sender, state)

@router.callback_query(F.data == "ws:back_net")
async def on_back_network(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    await _show_network(cb, ctx, services, sender, state)

@router.callback_query(F.data == "ws:continue")
async def on_continue(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    """«متابعة»: the plan step. No workspace exists yet."""
    data = await state.get_data()
    if not data.get("ws_name"):
        await state.set_state(Input.waiting_name)
        await show_screen(cb, ws_screens.create_name_prompt(ctx.locale), ctx=ctx, sender=sender)
        return
    await _show_plan_picker(cb, ctx, services, sender, state)

@router.callback_query(F.data == "ws:packs")
async def on_packs(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """Buy Stars, then come straight back to the plan step (state is kept)."""
    view = await billing._view(services, ctx.tg_id, ctx.locale)
    await show_screen(
        cb,
        wallet_screens.packs_screen(view, back_action="ws:continue"),
        ctx=ctx,
        sender=sender,
    )

@router.callback_query(F.data.startswith("ws:plan"))
async def on_plan(
    cb: CallbackQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
) -> None:
    """Pick a plan and create the workspace with it — the money step is the commit."""
    from cloudbot.core.errors import InsufficientFunds, TrialUsed

    plan_code = decode_required(cb.data, 1).key[0]
    data = await state.get_data()
    name = data.get("ws_name", "")
    if not name:
        await state.set_state(Input.waiting_name)
        await show_screen(cb, ws_screens.create_name_prompt(ctx.locale), ctx=ctx, sender=sender)
        return
    limits = ws_screens.wizard_network(
        (await services.workspaces.meta()).limits_defaults, data
    )
    try:
        ws = await services.workspaces.create(
            ctx.tg_id, name, plan_code=plan_code, limits=limits
        )
    except InsufficientFunds:
        # Not enough Stars: no workspace, no debit, state kept for a retry.
        await _show_plan_picker(cb, ctx, services, sender, state, needed_plan=plan_code)
        return
    except TrialUsed:
        await _show_plan_picker(cb, ctx, services, sender, state)
        return
    await state.clear()
    events.workspace_created(tg_id=ctx.tg_id, ws_id=str(ws.id), name=ws.name)
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, confirmation=catalog.t("ws.create.ready", ctx.locale, name=ws.name)
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender, force_send=True)

# -- lifecycle ---------------------------------------------------------------

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


@router.callback_query(F.data.startswith("ws:settings"))
async def on_settings(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    """The workspace's administration screen.

    Pause, stop-everything and delete are reachable only from here; the home
    screen offers the everyday tools. Nothing about the workspace is loaded twice:
    this reads it once, the same way every other workspace screen does.
    """
    ws_id = decode_required(cb.data, 1).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.workspace_settings(ws, ctx.locale), ctx=ctx, sender=sender)


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

    decoded = decode_required(cb.data, 1)
    await cb.answer()
    await billing.open_plans(cb, ctx, services, sender, ws_id=decoded.key[0])


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
