"""Main menu, help, and settings screens."""

from __future__ import annotations

from cloudbot.core.locale import LOCALE_LABELS, Locale
from cloudbot.domain.entities import Workspace
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_bytes_pair, format_count
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import display_state, workspace_status_label
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen


def welcome_screen(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    """The first-run / empty board.

    Shown whenever the user owns no workspaces: it states what the product is,
    the promise in one line, and a single obvious action (Start).
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("welcome.title", locale))
    doc.p(catalog.t("welcome.tagline", locale))
    doc.p(catalog.t("welcome.body", locale))
    doc.div()
    doc.p(catalog.t("welcome.ready", locale))
    doc.p(_raw(catalog.t("welcome.start_hint", locale)))
    rows = [[inline(catalog.t("welcome.start", locale), "ws:new", intent=Intent.SUCCESS)]]
    rows += nav.nav_rows(locale, "menu", inside=inside)
    return Screen(doc=doc, markup=inline_markup(rows))


def main_menu(
    workspaces: list[Workspace],
    locale: Locale,
    *,
    display_name: str = "",
    inside: Workspace | None = None,
) -> Screen:
    if not workspaces:
        return welcome_screen(locale, inside=inside)

    doc = RichDoc(locale)
    doc.h(2, catalog.t("menu.title", locale))
    doc.p(catalog.t("menu.hello", locale, name=display_name))
    doc.div()

    doc.p(format_count("count.workspaces", len(workspaces), locale))
    rows = []
    for ws in workspaces[:5]:
        label_key, emoji = display_state(ws.status, ws.summary.state)
        status = f"{emoji} {catalog.t(label_key, locale)}"
        runs = (
            catalog.t("ws.runs.active", locale, n=_num(ws.summary.runs_active, locale))
            if ws.summary.runs_active
            else catalog.t("ws.runs.none", locale)
        )
        disk = format_bytes_pair(
            ws.summary.disk_used_bytes,
            ws.summary.disk_limit_bytes,
            locale,
        )
        rows.append([ws.name, status, runs, disk])
    doc.table(
        [
            catalog.t("menu.column.workspace", locale),
            catalog.t("menu.column.status", locale),
            catalog.t("menu.column.runs", locale),
            catalog.t("menu.column.disk", locale),
        ],
        rows,
    )
    doc.p(_italic(catalog.t("menu.tap_hint", locale)))

    # Full-width primary action, then the shared 2×2 nav block below it.
    rows_kb = [[inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)]]
    rows_kb += nav.nav_rows(locale, "menu", inside=inside)
    return Screen(doc=doc, markup=inline_markup(rows_kb))


def help_screen(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    """What the product is, how a workspace and a terminal behave, and the two
    controls a new user needs (the in-session size tool, and the commands).

    Kept to one screen: a help screen is a starting point, not a manual, and
    every deep tool already has its own screen one tap away.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("help.title", locale))
    doc.p(catalog.t("help.body", locale))
    doc.p(_raw(catalog.t("help.workspace", locale)))
    doc.p(_raw(catalog.t("help.tools", locale)))
    doc.p(_raw(catalog.t("help.terminals", locale)))
    doc.div()
    doc.h(3, catalog.t("help.size_title", locale))
    doc.p(_raw(catalog.t("help.size", locale)))
    doc.raw(
        "<details><summary>"
        + catalog.t("help.commands", locale)
        + "</summary><ul>"
        + "".join(
            f"<li>{catalog.t(k, locale)}</li>"
            for k in ("help.cmd.start", "help.cmd.settings", "help.cmd.help")
        )
        + "</ul></details>"
    )
    doc.p(_italic(catalog.t("help.footer", locale)))
    return Screen(doc=doc, markup=inline_markup(nav.nav_rows(locale, "help", inside=inside)))


def settings_screen(locale: Locale, *, plan: str = "free", inside: Workspace | None = None) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("settings.title", locale))
    doc.p(_raw(f"<b>{catalog.t('settings.language', locale)}</b> {LOCALE_LABELS[locale]}"))
    doc.p(_raw(f"<b>{catalog.t('settings.plan', locale)}</b> {catalog.t(f'plan.{plan}', locale)}"))
    doc.div()
    doc.p(catalog.t("settings.choose", locale))
    return Screen(doc=doc, markup=inline_markup(nav.nav_rows(locale, "settings", inside=inside)))


def _italic(text: str):
    from cloudbot.presentation.rich.tags import italic, fragment

    return italic(fragment(text))


def _raw(text: str):
    from cloudbot.presentation.rich.tags import Html

    return Html(text)


def _num(value: int, locale: Locale) -> str:
    from cloudbot.presentation.i18n.formatters import localize_digits

    return localize_digits(str(value), locale)
