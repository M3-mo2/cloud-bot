"""The shared bottom navigation block for every top-level screen.

One helper, one layout, one place. The block lists the four top-level
destinations — Workspaces (``ws:list``), Settings (``nav:settings``), Language
(``nav:language``) and Help (``nav:help``) — plus Menu (``nav:menu``) where the
screen is not the menu itself. The destination the user is already on is rendered
muted/disabled so it stays visible but is not tappable.

While the user is **inside a workspace** (task 10) a ``🏠 <workspace name>`` entry
becomes the FIRST nav item, going to the workspace home; the Workspaces entry stays
put so the user can switch.

Layout rule (applies to every screen, forever): at most **two buttons per row**
and at most **three rows** in the nav block; a screen's own primary action may
sit full width *above* it. Never more than two columns anywhere. While inside the
block carries six buttons (home + four destinations + menu) — exactly three rows
of two.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.styles import Intent

#: (current-key, catalog-label key, callback) in display order.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("workspaces", "menu.workspaces", "ws:list"),
    ("settings", "menu.settings", "nav:settings"),
    ("language", "menu.language", "nav:language"),
    ("help", "menu.help", "nav:help"),
)

#: Every callback that belongs to the nav block (used by tests to count rows).
NAV_ACTIONS: frozenset[str] = frozenset(
    {"ws:list", "ws:home", "nav:settings", "nav:language", "nav:help", "nav:menu"}
)


def nav_rows(locale: Locale, current: str, *, inside=None) -> list[list]:
    """Return the nav block's rows for ``current`` (chunked two per row).

    ``inside`` is the workspace the user is currently inside (a domain
    ``Workspace``) or ``None``. When set, a workspace-home entry leads the block.
    """
    buttons = []
    if inside is not None:
        name = getattr(inside, "name", "") or ""
        if name:
            label = catalog.t("nav.workspace_home_named", locale, name=name)
        else:
            label = catalog.t("nav.workspace_home", locale)
        home_intent = Intent.MUTED if current == "workspace_home" else Intent.PRIMARY
        buttons.append(inline(label, "ws:home", intent=home_intent))
    for key, label_key, action in DESTINATIONS:
        intent = Intent.MUTED if key == current else Intent.PRIMARY
        buttons.append(inline(catalog.t(label_key, locale), action, intent=intent))
    if current != "menu":
        buttons.append(
            inline(catalog.t("common.menu", locale), "nav:menu", intent=Intent.PRIMARY)
        )
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
