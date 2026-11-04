"""Phase 2 handlers: packages (2a), tunnels (2b), ports (2c).

Thin aiogram routers: parse → call the domain service → render one screen →
send/edit. A slow install and a slow tunnel create are awaited off the update
task (the handler is the async work) so the bot never blocks; both are bounded by
the adapter's client timeout. The tunnel URL is only ever placed on a ``LINK``
button and is never stored.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import EngineRefused
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.mapping.errors import error_screen
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import packages as pkg_screens
from cloudbot.presentation.screens import ports as port_screens
from cloudbot.presentation.screens import tunnels as tnl_screens
from cloudbot.services.bundle import Services
from cloudbot.services.packages import parse_package_input
from cloudbot.services.tunnels import parse_port
from cloudbot.states.feature_input import FeatureInput

log = structlog.get_logger("cloudbot.features")

router = Router(name="features")


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


# ============================================================ packages (2a) ===
@router.callback_query(F.data.startswith("pkg:list"))
async def on_packages(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    pkg_list = await services.packages.list(ctx.tg_id, ws_id)
    await show_screen(cb, pkg_screens.packages_screen(pkg_list, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("pkg:install"))
async def on_install_prompt(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(FeatureInput.awaiting_package_install)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, pkg_screens.install_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(FeatureInput.awaiting_package_install)
async def on_install_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    names = parse_package_input(message.text or "")
    if not names:
        await show_screen(
            message,
            error_screen(_invalid("package", "empty"), ctx.locale),
            ctx=ctx,
            sender=sender,
            force_send=True,
        )
        return
    # An install is slow (measured ~10 s for one package): show a working screen,
    # then await the (bounded) call before rendering the honest result.
    await show_screen(message, pkg_screens.installing_screen(ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=True)
    outcome = await services.packages.install(ctx.tg_id, ws_id, names)
    await show_screen(
        message,
        pkg_screens.install_result_screen(
            outcome,
            " ".join(names),
            ws_id,
            ctx.locale,
            mode=ctx.mode,
            is_admin=ctx.is_admin,
        ),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.callback_query(F.data.startswith("pkg:rm_all_confirm"))
async def on_remove_all_confirm(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    ws = await services.workspaces.get(ctx.tg_id, ws_id)
    await show_screen(cb, pkg_screens.remove_all_confirm(ws.name, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("pkg:rm_all"))
async def on_remove_all(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    await services.packages.remove_all(ctx.tg_id, ws_id)
    pkg_list = await services.packages.list(ctx.tg_id, ws_id)
    await show_screen(cb, pkg_screens.packages_screen(pkg_list, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("pkg:rm_prompt"))
async def on_remove_prompt(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(FeatureInput.awaiting_package_remove)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, pkg_screens.remove_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(FeatureInput.awaiting_package_remove)
async def on_remove_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    await services.packages.remove(ctx.tg_id, ws_id, message.text or "")
    pkg_list = await services.packages.list(ctx.tg_id, ws_id)
    await show_screen(message, pkg_screens.packages_screen(pkg_list, ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=True)


# ============================================================ tunnels (2b) ====
async def _tunnels_screen(ctx: UserContext, services: Services, ws_id):
    """Render the tunnels screen with the workspace's gate state resolved."""
    tunnels = await services.tunnels.list(ctx.tg_id, ws_id)
    meta = await services.tunnels.meta()
    ws = await services.workspaces.get(ctx.tg_id, ws_id)
    return tnl_screens.tunnels_screen(
        tunnels,
        ws_id,
        ctx.locale,
        allow_tunnels=ws.limits.allow_tunnels,
        allow_net=ws.limits.allow_net,
        server_enabled=meta.tunnels.enabled,
    )


@router.callback_query(F.data.startswith("tnl:list"))
async def on_tunnels(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    await show_screen(cb, await _tunnels_screen(ctx, services, ws_id), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("tnl:publish"))
async def on_publish(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    # Offer the discovered ports (best) plus a "type one" path.
    detected: list[int] = []
    try:
        report = await services.ports.list(ctx.tg_id, ws_id)
        detected = [p.port for p in report.ports if p.attributed]
    except EngineRefused as exc:
        if exc.engine_code != "ports_disabled":
            raise
    await show_screen(
        cb, tnl_screens.publish_port_screen(ws_id, ctx.locale, detected=detected), ctx=ctx, sender=sender
    )


@router.callback_query(F.data.startswith("tnl:port_custom"))
async def on_publish_custom(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(FeatureInput.awaiting_port)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, tnl_screens.publish_custom_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(FeatureInput.awaiting_port)
async def on_port_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    await _publish(message, ctx, services, sender, ws_id, parse_port(message.text or ""))


@router.callback_query(F.data.startswith("tnl:port"))
async def on_port_pick(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    port = int(decoded.key[1])
    await _publish(cb, ctx, services, sender, ws_id, port)


async def _publish(event, ctx: UserContext, services: Services, sender: RichSender, ws_id, port: int) -> None:
    await show_screen(event, tnl_screens.publishing_screen(ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=isinstance(event, Message))
    try:
        await services.tunnels.publish(ctx.tg_id, ws_id, port)
    except EngineRefused as exc:
        if exc.engine_code not in ("tunnels_disabled", "too_many_tunnels"):
            raise
        # A gate refusal: re-render the tunnels screen, which names the off switch
        # and offers the fix (the actionable state the roadmap requires).
        log.info("tunnel.publish_refused", code=exc.engine_code)
        screen = await _tunnels_screen(ctx, services, ws_id)
        await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))
        return
    screen = await _tunnels_screen(ctx, services, ws_id)
    await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))


@router.callback_query(F.data.startswith("tnl:remove_confirm"))
async def on_remove_confirm(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    tunnels = await services.tunnels.list(ctx.tg_id, ws_id)
    tunnel = next((t for t in tunnels.tunnels if t.id == decoded.key[1]), None)
    if tunnel is None:
        await show_screen(cb, await _tunnels_screen(ctx, services, ws_id), ctx=ctx, sender=sender)
        return
    await show_screen(cb, tnl_screens.remove_confirm(tunnel, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("tnl:remove"))
async def on_remove(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    try:
        await services.tunnels.remove(ctx.tg_id, ws_id, decoded.key[1])
    except EngineRefused as exc:
        if exc.engine_code != "not_found":
            raise
    await show_screen(cb, await _tunnels_screen(ctx, services, ws_id), ctx=ctx, sender=sender)


# ============================================================ ports (2c) =====
@router.callback_query(F.data.startswith("port:list"))
async def on_ports(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    report = await services.ports.list(ctx.tg_id, ws_id)
    await show_screen(cb, port_screens.ports_screen(report, ws_id, ctx.locale), ctx=ctx, sender=sender)


def _invalid(field: str, reason: str):
    from cloudbot.core.errors import InvalidInput

    return InvalidInput(field, reason)
