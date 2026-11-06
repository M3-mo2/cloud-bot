"""The shared bottom navigation block for every top-level screen.

One helper, one layout, one place. While the user is **not inside** a workspace,
the block lists the top-level account destinations — Workspaces (``ws:list``),
Settings (``nav:settings``), Language (``nav:language``) and Help (``nav:help``)
— plus Menu (``nav:menu``) where the screen is not the menu itself.

**A nav entry never points at the screen you are already on (R1): the current
destination is omitted, not muted.** A disabled entry is still a button-shaped
thing that wastes a slot in the two-column grid and reads as a real destination;
the correct answer is that it is simply not there. This is keyed off ``current``
and applies to every screen that uses the block. ``Intent.MUTED`` stays available
for buttons that legitimately exist elsewhere (e.g. ``▶️ Run`` while a run is
active) — this rule is about the nav block only.

On the **workspaces list** Menu is dropped as well (R2): the menu's body is the
same workspace table, so for a user who owns workspaces the menu is a second
route to the screen the list already is.

**While the user is inside a workspace (task 13) the block is the session's, not
the account's.** It carries up to two entries: ``🏠 <workspace name>``
(``ws:home``) and ``📦 Workspaces`` (``ws:list``) to switch — minus the one you
are already on (R1/R5: on the workspace home itself ``ws:home`` is omitted,
leaving just ``📦 Workspaces``). The account destinations — Settings, Language,
Help and Menu — are dropped whenever ``inside`` is set; they remain reachable
the same way as before, through the ``/settings`` and ``/help`` commands, and by
leaving the workspace.

Layout rule (applies to every screen, forever): at most **two buttons per row**
and at most **three rows** in the nav block; a screen's own primary action may
sit full width *above* it. Never more than two columns anywhere.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.styles import Intent

#: **The one-destination-one-label rule (TASK labels).**
#:
#: * Default: a destination callback wears **one** static label everywhere in the
#:   catalogue. "Static" means the text is a catalog message with no substitution
#:   field; a label built from data (a workspace name, a truncated session id, a
#:   port number) is *not* the same label as another built from other data even
#:   though both reach the same callback, and is therefore not compared.
#: * The exceptions below are **affordance** labels: the same callback means "step
#:   back"/"abandon this"/"re-read this" on different screens, and the user should
#:   read the affordance, not the destination it happens to open. They are listed
#:   here, by catalog key, rather than left scattered across the screens.
#:
#: ``tests/unit/test_menu.py::test_every_destination_wears_one_static_label`` walks
#: ``SCREEN_BUILDERS`` and enforces both halves of the rule.
NAV_LABEL_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # Step back one level — the affordance, not the target screen.
        "common.back",
        # Abandon the current flow without doing it.
        "common.cancel",
        # Re-read the screen you are already on.
        "common.refresh",
        # Re-attempt the failed action (same destination, deliberately re-offered).
        "common.retry",
        # The admin panel's own step-back affordance, separate section tree.
        "admin.back",
        # Re-run the last exec; the destination is a prompt, not a screen to name.
        "exec.run_again",
        # Retry the install flow from its result screen.
        "package.result_retry",
        # On the cap screen this opens the list *to remove one*; the verb is the point.
        "tunnel.remove_one",
        # Same Publish destination, deliberately labelled with its ceiling
        # ("· limit reached") because the number is the message.
        "tunnel.publish_full",
    }
)

#: (current-key, catalog-label key, callback) in display order.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("workspaces", "menu.workspaces", "ws:list"),
    ("wallet", "menu.wallet", "pay:wallet"),
    ("settings", "menu.settings", "nav:settings"),
    ("language", "menu.language", "nav:language"),
    ("help", "menu.help", "nav:help"),
)

#: Every callback that belongs to the nav block (used by tests to count rows).
NAV_ACTIONS: frozenset[str] = frozenset(
    {
        "ws:list",
        "ws:home",
        "pay:wallet",
        "nav:settings",
        "nav:language",
        "nav:help",
        "nav:menu",
    }
)

#: Each ``current`` key's own callback (R1). The helper omits the entry whose
#: action is the caller's own destination; this map lets the rule be asserted
#: once, data-driven, for every screen.
CURRENT_ACTION: dict[str, str] = {
    "workspaces": "ws:list",
    "wallet": "pay:wallet",
    "settings": "nav:settings",
    "language": "nav:language",
    "help": "nav:help",
    "menu": "nav:menu",
    "workspace_home": "ws:home",
}


def nav_rows(locale: Locale, current: str, *, inside=None) -> list[list]:
    """Return the nav block's rows for ``current`` (chunked two per row).

    ``current`` is the surface the caller is rendering; its own destination is
    **omitted** (R1), never muted. ``inside`` is the workspace the user is
    currently inside (a domain ``Workspace``) or ``None``. When set, the block is
    the **session's block** (``ws:home`` + ``ws:list``, minus the current one);
    when unset, the block is the account's (the four destinations, plus Menu
    where the screen is not the menu and not the workspaces list).
    """
    if inside is not None:
        name = getattr(inside, "name", "") or ""
        if name:
            home_label = catalog.t("nav.workspace_home_named", locale, name=name)
        else:
            home_label = catalog.t("nav.workspace_home", locale)
        buttons = []
        if current != "workspace_home":
            buttons.append(inline(home_label, "ws:home", intent=Intent.PRIMARY))
        if current != "workspaces":
            buttons.append(
                inline(catalog.t("menu.workspaces", locale), "ws:list", intent=Intent.PRIMARY)
            )
        return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]

    buttons = []
    for key, label_key, action in DESTINATIONS:
        if key == current:
            continue
        buttons.append(inline(catalog.t(label_key, locale), action, intent=Intent.PRIMARY))
    # R2: on the workspaces list the menu duplicates the list itself; drop it.
    if current not in ("menu", "workspaces"):
        buttons.append(
            inline(catalog.t("common.menu", locale), "nav:menu", intent=Intent.PRIMARY)
        )
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
