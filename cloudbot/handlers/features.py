"""Phase 2 handlers: packages (2a), tunnels (2b), ports (2c), metrics (2d),
processes (2e), archives (2g).

Thin aiogram routers: parse → call the domain service → render one screen →
send/edit. A slow install and a slow tunnel create are awaited off the update
task (the handler is the async work) so the bot never blocks; both are bounded by
the adapter's client timeout. The tunnel URL is only ever placed on a ``LINK``
button and is never stored.

The archive handlers are the **only** place a Telegram document is moved (the
uploaded archive in, the exported ZIP out); general file delivery to documents
remains CB-14 (Phase 3).
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
from cloudbot.presentation.i18n.formatters import format_bytes
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.mapping.errors import error_screen
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import archives as arc_screens
from cloudbot.presentation.screens import metrics as met_screens
from cloudbot.presentation.screens import packages as pkg_screens
from cloudbot.presentation.screens import ports as port_screens
from cloudbot.presentation.screens import processes as proc_screens
from cloudbot.presentation.screens import tunnels as tnl_screens
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents
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
    message: Message,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    state: FSMContext,
    events: RequestEvents,
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
    if not outcome.ok:
        events.package_install_failed(tg_id=ctx.tg_id, ws_id=str(ws_id), names=" ".join(names))
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
#: Engine refusals this path renders a dedicated, explanatory screen for. Any
#: other refusal is **re-raised** to the error screen (R1) — a refusal is never
#: swallowed into a silent redraw, and a new engine code cannot slip through
#: unmapped.
TUNNEL_REFUSAL_KINDS: dict[str, str] = {
    "too_many_tunnels": "limit",
    "tunnel_url_unavailable": "timeout",
    "tunnels_disabled": "gate",
}


async def _detected_ports(ctx: UserContext, services: Services, ws_id) -> list[int]:
    """The workspace's attributed listening ports, or ``[]`` when ports are off."""
    try:
        report = await services.ports.list(ctx.tg_id, ws_id)
    except EngineRefused as exc:
        if exc.engine_code != "ports_disabled":
            raise
        return []
    return [p.port for p in report.ports if p.attributed]


def _unlistened_warning(port: int, detected: list[int], locale) -> str | None:
    """The one-sentence warning for a port nothing is listening on (R3), or None.

    A detected port gets no warning; a port outside the detected set gets the
    sentence but is never blocked.
    """
    if port in detected:
        return None
    return catalog.t("tunnel.publish_unlistened", locale)


async def _tunnels_screen(ctx: UserContext, services: Services, ws_id):
    """Render the tunnels screen with the workspace's gate state and ceiling."""
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
        max_tunnels=meta.tunnels.max_per_workspace,
    )


async def _tunnel_refusal_screen(
    exc: EngineRefused, ctx: UserContext, services: Services, ws_id, port: int
):
    """A rendered sentence for a known tunnel refusal, or ``None`` to re-raise."""
    kind = TUNNEL_REFUSAL_KINDS.get(exc.engine_code)
    if kind == "limit":
        limit = await services.tunnels.limit()
        return tnl_screens.publish_refused_screen(
            port, ws_id, ctx.locale, max_tunnels=limit
        )
    if kind == "timeout":
        return tnl_screens.tunnel_timeout_screen(port, ws_id, ctx.locale)
    if kind == "gate":
        return await _tunnels_screen(ctx, services, ws_id)
    return None


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
    detected = await _detected_ports(ctx, services, ws_id)
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
    port = parse_port(message.text or "")
    # R3: a port that is not in the detected set gets a plain warning before
    # publishing — it is never blocked (starting a server after publishing is
    # legitimate), so this is a confirm, not a refusal.
    detected = await _detected_ports(ctx, services, ws_id)
    warning = _unlistened_warning(port, detected, ctx.locale)
    await show_screen(
        message,
        tnl_screens.publish_confirm(port, ws_id, ctx.locale, warning=warning),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.callback_query(F.data.startswith("tnl:port"))
async def on_port_pick(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, events: RequestEvents) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    port = int(decoded.key[1])
    await _publish(cb, ctx, services, sender, ws_id, port, events)


async def _publish(event, ctx: UserContext, services: Services, sender: RichSender, ws_id, port: int, events: RequestEvents) -> None:
    await show_screen(event, tnl_screens.publishing_screen(ws_id, ctx.locale), ctx=ctx, sender=sender, force_send=isinstance(event, Message))
    try:
        await services.tunnels.publish(ctx.tg_id, ws_id, port)
    except EngineRefused as exc:
        # Every refusal on this path renders a sentence; an unmapped code is
        # re-raised to the error screen rather than silently redrawing (R1).
        screen = await _tunnel_refusal_screen(exc, ctx, services, ws_id, port)
        if screen is None:
            raise
        log.info("tunnel.publish_refused", code=exc.engine_code)
        await show_screen(event, screen, ctx=ctx, sender=sender, force_send=isinstance(event, Message))
        return
    events.tunnel_created(tg_id=ctx.tg_id, ws_id=str(ws_id), port=port)
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


# ============================================================ metrics (2d) ===
@router.callback_query(F.data.startswith("met:view"))
async def on_metrics(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    metrics = await services.metrics.snapshot(ctx.tg_id, ws_id)
    await show_screen(cb, met_screens.metrics_screen(metrics, ws_id, ctx.locale), ctx=ctx, sender=sender)


# ========================================================== processes (2e) ===
@router.callback_query(F.data.startswith("proc:list"))
async def on_processes(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    process_list = await services.processes.list(ctx.tg_id, ws_id)
    await show_screen(cb, proc_screens.processes_screen(process_list, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("proc:stop_confirm"))
async def on_process_stop_confirm(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    process_id = decoded.key[1]
    process_list = await services.processes.list(ctx.tg_id, ws_id)
    entry = next((p for p in process_list.processes if p.id == process_id), None)
    if entry is None:
        # The process left the inventory between listing and tapping: treat it as
        # already gone rather than as an error.
        await show_screen(
            cb,
            proc_screens.stop_result_screen(
                _gone(process_id), ws_id, ctx.locale
            ),
            ctx=ctx,
            sender=sender,
        )
        return
    await show_screen(
        cb, proc_screens.stop_confirm(entry, ws_id, ctx.locale), ctx=ctx, sender=sender
    )


@router.callback_query(F.data.startswith("proc:stop"))
async def on_process_stop(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    result = await services.processes.stop(ctx.tg_id, ws_id, decoded.key[1])
    await show_screen(
        cb, proc_screens.stop_result_screen(result, ws_id, ctx.locale), ctx=ctx, sender=sender
    )


def _gone(process_id: str):
    from cloudbot.domain.value_objects import ProcessStopResult

    return ProcessStopResult(process_id=process_id, status="gone", stopped=False)


# =========================================================== archives (2g) ==
#: A short-lived in-memory registry of an uploaded archive awaiting "Apply".
#: The dry-run preview is computed from the bytes; Apply must not ask the user
#: to upload again, so the bytes are held (bounded) until applied or evicted.
_PENDING_UPLOADS: "dict[str, tuple]" = {}
_MAX_PENDING = 4


def _remember_upload(ws_id: str, filename: str, data: bytes) -> str:
    import secrets

    token = secrets.token_hex(6)
    if len(_PENDING_UPLOADS) >= _MAX_PENDING:
        _PENDING_UPLOADS.pop(next(iter(_PENDING_UPLOADS)), None)
    _PENDING_UPLOADS[token] = (ws_id, filename, data)
    return token


@router.callback_query(F.data.startswith("arc:menu"))
async def on_archive_menu(cb: CallbackQuery, ctx: UserContext, sender: RichSender) -> None:
    ws_id = _wid(decode(cb.data).key[0])
    await show_screen(cb, arc_screens.archive_menu(ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("arc:upload"))
async def on_archive_upload(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    meta = await services.archives.meta()
    await state.set_state(FeatureInput.awaiting_archive_upload)
    await state.update_data(ws_id=ws_id)
    await show_screen(
        cb,
        arc_screens.upload_prompt(_wid(ws_id), meta.archives.limits.upload_mb, ctx.locale),
        ctx=ctx,
        sender=sender,
    )


@router.message(FeatureInput.awaiting_archive_upload, F.document)
async def on_archive_document(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    document = message.document
    meta = await services.archives.meta()
    limit_bytes = meta.archives.limits.upload_mb * 1024 * 1024
    if document.file_size and limit_bytes and document.file_size > limit_bytes:
        await show_screen(
            message,
            arc_screens.upload_too_large(
                format_bytes(document.file_size, ctx.locale),
                format_bytes(limit_bytes, ctx.locale),
                ws_id,
                ctx.locale,
            ),
            ctx=ctx,
            sender=sender,
            force_send=True,
        )
        return
    blob = await sender.download_document(document)
    # Dry-run first: preview what the archive would do, writing nothing.
    result = await services.archives.upload_and_extract(
        ctx.tg_id,
        ws_id,
        filename=document.file_name or "archive",
        data=blob,
        path="/",
        dry_run=True,
        size_bytes=document.file_size,
    )
    token = _remember_upload(str(ws_id), document.file_name or "archive", blob)
    await show_screen(
        message,
        arc_screens.dry_run_prompt(
            result, ws_id, ctx.locale, action="upload", target=token
        ),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.message(FeatureInput.awaiting_archive_upload)
async def on_archive_upload_other(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    meta = await services.archives.meta()
    await show_screen(
        message,
        arc_screens.upload_prompt(ws_id, meta.archives.limits.upload_mb, ctx.locale),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.callback_query(F.data.startswith("arc:extract_prompt"))
async def on_archive_extract_prompt(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(FeatureInput.awaiting_archive_extract)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, arc_screens.extract_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(FeatureInput.awaiting_archive_extract)
async def on_archive_extract_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    source = (message.text or "").strip()
    if not source or source == "/":
        await show_screen(
            message,
            error_screen(_invalid("archive", "empty"), ctx.locale),
            ctx=ctx,
            sender=sender,
            force_send=True,
        )
        return
    result = await services.archives.extract(
        ctx.tg_id, ws_id, path=source, dry_run=True
    )
    await show_screen(
        message,
        arc_screens.dry_run_prompt(
            result, ws_id, ctx.locale, action="extract", target=source
        ),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.callback_query(F.data.startswith("arc:export_prompt"))
async def on_archive_export_prompt(
    cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext
) -> None:
    ws_id = decode(cb.data).key[0]
    await state.set_state(FeatureInput.awaiting_archive_export)
    await state.update_data(ws_id=ws_id)
    await show_screen(cb, arc_screens.export_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(FeatureInput.awaiting_archive_export)
async def on_archive_export_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    path = (message.text or "").strip() or "/"
    filename, blob = await services.archives.export(ctx.tg_id, ws_id, path)
    if message.chat:
        await sender.send_document(message.chat.id, filename, blob)
    await show_screen(
        message,
        arc_screens.export_done_screen(filename, len(blob), ws_id, ctx.locale),
        ctx=ctx,
        sender=sender,
        force_send=True,
    )


@router.callback_query(F.data.startswith("arc:apply"))
async def on_archive_apply(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    action = decoded.key[1]
    target = decoded.key[2]
    if action == "upload":
        pending = _PENDING_UPLOADS.pop(target, None)
        if pending is None:
            # The preview expired (or the process restarted): ask for it again.
            await show_screen(cb, arc_screens.archive_menu(ws_id, ctx.locale), ctx=ctx, sender=sender)
            return
        _ws, filename, blob = pending
        result = await services.archives.upload_and_extract(
            ctx.tg_id, ws_id, filename=filename, data=blob, path="/", dry_run=False
        )
    else:
        result = await services.archives.extract(
            ctx.tg_id, ws_id, path=target, dry_run=False
        )
    await show_screen(
        cb, arc_screens.archive_result_screen(result, ws_id, ctx.locale), ctx=ctx, sender=sender
    )
