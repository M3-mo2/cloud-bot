"""Developer tools: the in-bot live button inspector (``/dev``).

Reachable only through the identity gate **and** the explicit
``CLOUDBOT_DEV_TOOLS`` switch (``middleware/admin_gate.py``): the gate swallows a
disabled/`dev:` event before it is routed, so this handler is never reached when
the tools are off or by a non-admin.

``/dev`` walks the product's own screen catalog (``presentation.catalog``) and,
for a chosen screen, renders it exactly as the user sees it, tabulates every live
button with a verdict, and lets the admin **tap** a button through a **real**
dispatcher whose handlers all talk to a fake bot and a fake engine (a dedicated
``DevSandbox``). Nothing reaches Telegram, nothing reaches the engine, and the
tool cannot mutate the live state because the sandbox has its own in-memory world.

The verdict logic and the walk live in ``presentation.catalog`` — the **same**
code the contract tests use — so the tool and the tests cannot drift.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.handlers.dev_sandbox import DevSandbox
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import dev as dev_screens
from cloudbot.presentation.screens.dev import DEV_PREFIX, parse as parse_dev

log = structlog.get_logger("cloudbot.dev")

router = Router(name="dev")

#: A fallback update id when Telegram's callback id is not numeric.
_UID = 1


def _loc(value: str) -> Locale:
    return Locale.AR if value == "ar" else Locale.EN


def _section_stats(locale: Locale) -> list[tuple[str, int, int]]:
    """Per-section (key, screen count, button count) for the sections screen."""
    stats: list[tuple[str, int, int]] = []
    for section in screen_catalog.sections():
        specs = screen_catalog.screens_in(section.key)
        buttons = 0
        for spec in specs:
            try:
                screen = screen_catalog.build(spec.name, locale)
            except Exception:  # noqa: BLE001 - a broken screen must not break the list
                continue
            buttons += len(screen_catalog.button_infos(screen))
        stats.append((section.key, len(specs), buttons))
    return stats


def _sections(ctx: UserContext):
    return dev_screens.sections_screen(_section_stats(ctx.locale), ctx.locale)


async def _sandbox(ctx: UserContext, settings: Settings) -> DevSandbox:
    return await DevSandbox.start(settings, ctx.tg_id, ctx.locale)


async def _claims_for(sandbox: DevSandbox):
    async def _claims(data: str) -> bool:
        return await sandbox.claims(data)

    return _claims


# -- entry ---------------------------------------------------------------------
@router.message(Command("dev"))
async def on_dev(
    message: Message, ctx: UserContext, settings: Settings, sender: RichSender
) -> None:
    # The gate already proved identity + switch; settings ride in via middleware.
    await show_screen(message, _sections(ctx), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith(DEV_PREFIX + ":"))
async def on_dev_callback(
    cb: CallbackQuery, ctx: UserContext, settings: Settings, sender: RichSender
) -> None:
    parsed = parse_dev(cb.data)
    if not parsed.valid:
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)
        return
    uid = int(cb.id) if (cb.id or "").isdigit() else _UID
    try:
        await _dispatch(cb, ctx, settings, sender, parsed, uid)
    except Exception as exc:  # noqa: BLE001 - the tool must never crash the handler
        log.warning("dev.tool.error", error=str(exc))
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)


async def _dispatch(cb, ctx, settings, sender, parsed, uid: int) -> None:
    action = parsed.action
    if action == "sections":
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)
    elif action == "section":
        key = parsed.args[0]
        if not screen_catalog.has_section(key):
            await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)
            return
        names = [spec.name for spec in screen_catalog.screens_in(key)]
        await show_screen(cb, dev_screens.section_screen(key, names, ctx.locale), ctx=ctx, sender=sender)
    elif action == "inspect":
        await _inspect(cb, ctx, settings, sender, parsed.args[0], _loc(parsed.args[1]))
    elif action == "tap":
        await _tap(
            cb, ctx, settings, sender, parsed.args[0], _loc(parsed.args[1]), int(parsed.args[2]), uid
        )
    elif action == "run":
        section = None if parsed.args == ("catalog",) else parsed.args[1]
        await _run(cb, ctx, settings, sender, section)
    else:
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)


async def _inspect(cb, ctx, settings, sender, name: str, locale: Locale) -> None:
    if not screen_catalog.has(name):
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)
        return
    sandbox = await _sandbox(ctx, settings)
    try:
        verdict = await screen_catalog.walk_screen(
            name, locale, claims=await _claims_for(sandbox)
        )
        if verdict.error is not None:
            screen = dev_screens.inspect_screen(
                name, "", True, (), ctx.locale, error=verdict.error
            )
        else:
            real = screen_catalog.build(name, locale)
            screen = dev_screens.inspect_screen(
                name, real.doc.html, real.doc.build().is_rtl, verdict.buttons, ctx.locale
            )
    finally:
        await sandbox.aclose()
    await show_screen(cb, screen, ctx=ctx, sender=sender)


async def _tap(cb, ctx, settings, sender, name: str, locale: Locale, index: int, uid: int) -> None:
    if not screen_catalog.has(name):
        await show_screen(cb, _sections(ctx), ctx=ctx, sender=sender)
        return
    sandbox = await _sandbox(ctx, settings)
    try:
        infos = screen_catalog.button_infos(screen_catalog.build(name, locale))
        target = next((b for b in infos if b.index == index), None)
        if target is None or target.data is None:
            screen = dev_screens.tap_result_screen(name, ctx.locale, rendered_html=None, no_render=True)
        else:
            rendered, html, error = await sandbox.tap(target.data, uid=uid)
            screen = dev_screens.tap_result_screen(
                name, ctx.locale, rendered_html=html, error=error,
                no_render=not rendered and error is None,
            )
    finally:
        await sandbox.aclose()
    await show_screen(cb, screen, ctx=ctx, sender=sender)


async def _run(cb, ctx, settings, sender, section: str | None) -> None:
    locales = (Locale.EN, Locale.AR)
    sandbox = await _sandbox(ctx, settings)
    try:
        claims = await _claims_for(sandbox)
        if section is None:
            report = await screen_catalog.walk_catalog(locales, claims=claims)
            scope = catalog.t("dev.run.catalog", ctx.locale)
        else:
            report = await screen_catalog.walk_section(section, locales, claims=claims)
            scope = catalog.t("dev.section." + section, ctx.locale)
    finally:
        await sandbox.aclose()
    await show_screen(cb, dev_screens.report_screen(report, ctx.locale, scope=scope), ctx=ctx, sender=sender)
