"""Workspace handlers: list, detail, create wizard, lifecycle actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import InvalidInput
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.services.bundle import Services
from cloudbot.states.input import Input

router = Router(name="workspaces")


async def _show_list(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, page: int = 0) -> None:
    workspaces = await services.workspaces.list(ctx.tg_id)
    page_size = ws_screens.PAGE_SIZE
    start = page * page_size
    window = workspaces[start : start + page_size]
    total_pages = max(1, (len(workspaces) + page_size - 1) // page_size)
    screen = ws_screens.workspace_list(
        window, ctx.locale, page=page, total_pages=total_pages, has_more=(start + page_size) < len(workspaces)
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:list"))
async def on_list(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    page = int(decoded.key[0]) if decoded.key else 0
    await _show_list(cb, ctx, services, sender, page)


@router.callback_query(F.data.startswith("ws:view"))
async def on_view(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, active_run=ws.summary.runs_active > 0, mode=ctx.mode
    )
    await show_screen(cb, screen, ctx=ctx, sender=sender)


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
    await _do_create(message, ctx, services, sender, state, profile=meta.default_profile)


@router.callback_query(F.data.startswith("ws:profile"))
async def on_profile(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    decoded = decode(cb.data)
    profile = decoded.key[0]
    await _do_create(cb, ctx, services, sender, state, profile=profile)


@router.message(Input.waiting_profile)
async def on_profile_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    await _do_create(message, ctx, services, sender, state, profile=message.text.strip())


async def _do_create(event, ctx, services, sender, state, *, profile: str) -> None:
    data = await state.get_data()
    name = data.get("ws_name", "")
    await state.clear()
    ws = await services.workspaces.create(ctx.tg_id, name, profile=profile)
    screen = ws_screens.workspace_detail(
        ws, ctx.locale, confirmation=catalog.t("ws.create.ready", ctx.locale, name=ws.name)
    )
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=True)


# -- lifecycle ---------------------------------------------------------------
@router.callback_query(F.data.startswith("ws:delete_confirm"))
async def on_delete_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode(cb.data).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.delete_confirm(ws, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:delete"))
async def on_delete(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode(cb.data).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await services.workspaces.delete(ctx.tg_id, _wid(ws_id))
    from cloudbot.core.locale import Locale

    doc = _result_doc(ctx.locale, catalog.t("ws.delete.done", ctx.locale, name=ws.name))
    await sender.answer_callback(cb)
    if cb.message is not None:
        from cloudbot.presentation.rich.document import RichDoc

        d = RichDoc(ctx.locale)
        d.blockquote(_frag(catalog.t("ws.delete.done", ctx.locale, name=ws.name)))
        await sender.edit(cb.message.chat.id, cb.message.message_id, d)
    workspaces = await services.workspaces.list(ctx.tg_id)
    screen = ws_screens.workspace_list(workspaces, ctx.locale)
    if cb.message is not None:
        await sender.send(cb.message.chat.id, screen.doc, markup=screen.markup)


@router.callback_query(F.data.startswith("ws:stop_confirm"))
async def on_stop_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode(cb.data).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.stop_confirm(ws, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:stop"))
async def on_stop(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode(cb.data).key[0]
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
async def on_suspend(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    # Distinguish "suspend" (do it) from "suspend_confirm" (show confirm): the
    # content-addressed token differs, so compare the action string.
    if "confirm" in decoded.action:
        ws = await services.workspaces.get(ctx.tg_id, _wid(decoded.key[0]))
        await show_screen(cb, ws_screens.suspend_confirm(ws, ctx.locale), ctx=ctx, sender=sender)
        return
    ws_id = decoded.key[0]
    await services.workspaces.suspend(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.lifecycle_result(ws, ctx.locale, kind="suspend"), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:resume"))
async def on_resume(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = decode(cb.data).key[0]
    await services.workspaces.resume(ctx.tg_id, _wid(ws_id))
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.lifecycle_result(ws, ctx.locale, kind="resume"), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("ws:renew"))
async def on_renew(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    from cloudbot.core.errors import EngineRefused
    from cloudbot.presentation.mapping.errors import error_screen

    await cb.answer(catalog.t("ws.renew", ctx.locale))
    ws_id = decode(cb.data).key[0]
    ws = await services.workspaces.get(ctx.tg_id, _wid(ws_id))
    await show_screen(cb, ws_screens.workspace_detail(ws, ctx.locale), ctx=ctx, sender=sender)


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
