"""Main menu, help, and settings screens."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import Workspace
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_count
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import display_state
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, bold, fragment
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen

#: How many workspace lines the menu body shows before summarising the rest.
#: Five lines plus the "and N more" line keep the board inside one phone screen;
#: the remainder is never silently dropped (see ``main_menu``).
MENU_WORKSPACE_LINES = 5


def _line(name: str, status: str, runs: str | None = None) -> Html:
    """One scannable workspace line: name, state, and the one number that matters."""
    parts = [bold(fragment(name)), " — ", fragment(status)]
    if runs:
        parts += [" · ", fragment(runs)]
    return fragment(parts)


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
    plan: str | None = None,
) -> Screen:
    """The main board: the account's **only** hub.

    The body answers *which workspaces do I have, which is running, and how do I
    start a new one* — one compact line per workspace, never a four-column table.
    The keyboard reads top-to-bottom in the order attention moves: **the thing
    they came to do** (``New Workspace``, full width), then the account's own
    destinations — ``My workspaces`` · ``Wallet`` · ``Settings``. Help and
    Language are **not** on the menu ("the one-hub task"): they live under
    Settings, so repeating them here would be the duplication he asked to end.

    Subscription seam: ``plan`` is the *derived* plan label (``active``, ``trial``,
    ``expiring`` …) for the wallet/subscription screens added by ``subs2``. Those
    screens do not exist yet, so this place is deliberately a no-op: pass ``plan``
    and the status line appears; leave it ``None`` and nothing is invented.
    """
    if not workspaces:
        return welcome_screen(locale, inside=inside)

    doc = RichDoc(locale)
    doc.h(2, catalog.t("menu.title", locale))
    doc.p(catalog.t("menu.hello", locale, name=display_name))
    doc.div()

    # -- subscription status line (seam for subs2's wallet screens) ----------
    if plan:
        doc.p(catalog.t("menu.plan_line", locale, plan=catalog.t(f"plan.{plan}", locale)))

    doc.p(format_count("count.workspaces", len(workspaces), locale))
    shown = workspaces[:MENU_WORKSPACE_LINES]
    for ws in shown:
        label_key, emoji = display_state(ws.status, ws.summary.state)
        status = f"{emoji} {catalog.t(label_key, locale)}"
        runs = (
            catalog.t("menu.ws.running", locale, n=_num(ws.summary.runs_active, locale))
            if ws.summary.runs_active
            else None
        )
        doc.p(_line(ws.name, status, runs))
    hidden = len(workspaces) - len(shown)
    if hidden:
        # Never a silent truncation: say how many more there are and where.
        doc.p(
            _italic(
                catalog.t("menu.and_more", locale, n=_num(hidden, locale))
                + " "
                + catalog.t("menu.workspaces_more", locale, n=_num(len(workspaces), locale))
            )
        )
    doc.p(_italic(catalog.t("menu.tap_hint", locale)))

    # Full-width primary action, then the shared 2×2 nav block below it.
    rows_kb = [[inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)]]
    rows_kb += nav.nav_rows(locale, "menu", inside=inside)
    return Screen(doc=doc, markup=inline_markup(rows_kb))


def help_screen(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    """What the product is, how a workspace and a terminal behave, and the two
    controls a new user needs (the in-session size tool, and the commands).

    Help is a **page, not a hub**: its body and the home entry, nothing else. A
    help screen is a starting point, not a manual, and every deep tool already
    has its own screen one tap away.
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


def settings_screen(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    """The two-entry account settings hub: **Help** and **Language**, plus home.

    Settings is where the account's reference destinations live now; the menu no
    longer offers them directly. Nothing else belongs here — the wallet owns the
    money, and the menu owns the account's destinations.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("settings.title", locale))
    doc.p(catalog.t("settings.choose", locale))
    if inside is not None:
        # Inside a workspace the block is the session's (one ``ws:home`` entry).
        # Settings' own two destinations are account ones and stay out, exactly
        # as on every other inside screen.
        return Screen(
            doc=doc, markup=inline_markup(nav.nav_rows(locale, "settings", inside=inside))
        )
    rows = [
        [
            inline(catalog.t("menu.help", locale), "nav:help", intent=Intent.PRIMARY),
            inline(catalog.t("menu.language", locale), "nav:language", intent=Intent.PRIMARY),
        ]
    ]
    rows += nav.nav_rows(locale, "settings")
    return Screen(doc=doc, markup=inline_markup(rows))


def _italic(text: str):
    from cloudbot.presentation.rich.tags import italic, fragment

    return italic(fragment(text))


def _raw(text: str):
    from cloudbot.presentation.rich.tags import Html

    return Html(text)


def _num(value: int, locale: Locale) -> str:
    from cloudbot.presentation.i18n.formatters import localize_digits

    return localize_digits(str(value), locale)
