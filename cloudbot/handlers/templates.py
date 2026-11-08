"""Starter-template handlers: list, confirm, and the honest result.

Thin aiogram router: decode → ask the domain catalog / the file service → render
one screen. The write itself goes through :class:`FilesService` so the size cap,
path rules, plan gate and error mapping are never bypassed. Nothing is written on
a single tap: ``tpl:pick`` only shows the confirmation with every path listed.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery

from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.keyboards.callbacks import decode_required
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import templates as tpl_screens
from cloudbot.services.bundle import Services
from cloudbot.services.files import FileWrite, TemplatePathsExist

log = structlog.get_logger("cloudbot.templates")

router = Router(name="templates")


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


def _template_or_none(key: str):
    from cloudbot.domain import templates as tpl_catalog

    return tpl_catalog.by_key(key)


@router.callback_query(F.data.startswith("tpl:list"))
async def on_list(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """The static catalog. No engine call, no per-user state."""
    decoded = decode_required(cb.data, 1)
    ws_id = _wid(decoded.key[0])
    # Ownership is enforced on every workspace-scoped path; a foreign id renders
    # exactly like a missing one (the not-found screen) rather than the catalog.
    await services.ownership.assert_owned(ctx.tg_id, ws_id)
    await show_screen(cb, tpl_screens.list_screen(ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("tpl:pick"))
async def on_pick(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """The confirmation: name the template, list every path, probe collisions.

    A single tap never writes. The collision probe is a ``files/stat`` per path;
    a hit disables the create button and nothing is written.
    """
    decoded = decode_required(cb.data, 2)
    template = _template_or_none(decoded.key[0])
    ws_id = _wid(decoded.key[1])
    if template is None:
        await show_screen(cb, tpl_screens.list_screen(ws_id, ctx.locale), ctx=ctx, sender=sender)
        return

    await services.ownership.assert_owned(ctx.tg_id, ws_id)
    from cloudbot.domain import templates as tpl_catalog

    conflicts = await services.files.existing(ctx.tg_id, ws_id, list(tpl_catalog.paths(template)))
    await show_screen(
        cb,
        tpl_screens.confirm_screen(template, ws_id, ctx.locale, conflicts=conflicts),
        ctx=ctx,
        sender=sender,
    )


@router.callback_query(F.data.startswith("tpl:confirm"))
async def on_confirm(
    cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender
) -> None:
    """Write the files — all of them, or honestly none/part of them.

    The service refuses the whole set on a collision *before* writing, so the
    refused screen names the path and truthfully says nothing was written. A
    part-way engine refusal reports the paths that landed and the one that failed.
    """
    decoded = decode_required(cb.data, 2)
    template = _template_or_none(decoded.key[0])
    ws_id = _wid(decoded.key[1])
    if template is None:
        await show_screen(cb, tpl_screens.list_screen(ws_id, ctx.locale), ctx=ctx, sender=sender)
        return

    from cloudbot.domain import templates as tpl_catalog

    writes = [
        FileWrite(path=tpl_catalog.template_path(template, f), content=f.content)
        for f in template.files
    ]
    try:
        result = await services.files.create_many(ctx.tg_id, ws_id, writes)
    except TemplatePathsExist as refused:
        await show_screen(
            cb,
            tpl_screens.done_refused(template, ws_id, ctx.locale, refused.paths),
            ctx=ctx,
            sender=sender,
        )
        return
    # Any other ``EngineRefused`` (a stat probe refused, an engine outage) is left
    # to propagate: the error guard is the one place a DomainError becomes a screen.

    if result.ok:
        screen = tpl_screens.done_created(template, ws_id, ctx.locale, result.written)
    else:
        screen = tpl_screens.done_failed(
            template,
            ws_id,
            ctx.locale,
            failed_path=result.failed_path or "",
            error_code=result.error_code,
            written=result.written,
            engine_message=result.message,
        )
    if not result.ok:
        # The raw engine message is logged where an operator can read it; it is
        # never rendered (AGENTS.md rule 6 — no raw engine output to a user).
        log.warning(
            "templates.create_partial",
            ws_id=str(ws_id),
            template=template.key,
            written=len(result.written),
            failed=result.failed_path,
            code=result.error_code,
            engine_message=result.message,
        )
    await show_screen(cb, screen, ctx=ctx, sender=sender)
