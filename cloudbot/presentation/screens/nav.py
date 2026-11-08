"""The shared bottom navigation block for every top-level screen.

One helper, one layout, one place. **The main menu is the only hub.** While the
user is **not inside** a workspace:

* on the menu itself the block is the account's four destinations — New Workspace
  (drawn by the screen itself), Workspaces (``ws:list``), Wallet (``pay:wallet``)
  and Settings (``nav:settings``);
* on **every other screen** the block is exactly one entry: the home destination
  ``nav:menu``. A screen does not repeat the account's own destinations; Help and
  Language live under Settings, and the wallet keeps only money.

**A nav entry never points at the screen you are already on (R1): the current
destination is omitted, not muted.** On the menu that means the home entry is
absent because the menu *is* home. ``Intent.MUTED`` stays available for buttons
that legitimately exist elsewhere (e.g. ``▶️ Run`` while a run is active) — this
rule is about the nav block only.

**While the user is inside a workspace (task 13) the block is the session's, not
the account's.** It carries one entry — ``🏠 <workspace name>`` (``ws:home``) —
and nothing else. There is no ``ws:list`` while inside: reaching another
workspace is an account action, taken after logging out, and offering it here
made the session block look like a hierarchy. The account destinations are
dropped whenever ``inside`` is set; they remain reachable through the
``/settings`` and ``/help`` commands, and by leaving the workspace. This branch
is deliberately untouched by the one-hub task.

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
        # On the cap screen this opens the list *to remove one*; the verb is the point.
        "tunnel.remove_one",
        # Same Publish destination, deliberately labelled with its ceiling
        # ("· limit reached") because the number is the message.
        "tunnel.publish_full",
    }
)

#: (current-key, catalog-label key, callback) in display order. **The menu's own
#: body** — the only place that offers these destinations (the one-hub rule).
#: ``settings`` owns Help and Language now, so they are not listed here.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("workspaces", "menu.workspaces", "ws:list"),
    ("wallet", "menu.wallet", "pay:wallet"),
    ("settings", "menu.settings", "nav:settings"),
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
    currently inside (a domain ``Workspace``) or ``None``.

    Outside a workspace the **menu is the only hub**: on the menu the block is
    the four destinations from :data:`DESTINATIONS` (minus the menu itself, which
    is not one of them); on every other screen it is exactly one entry — the home
    destination ``nav:menu``. Inside, the block is the session's: one
    ``ws:home`` entry and never ``ws:list``.
    """
    if inside is not None:
        # Inside a workspace the block is the session's, and it carries exactly one
        # entry: the workspace you are in. Listing the other workspaces is an
        # account action — it is what you do *after* leaving — so ``ws:list`` is not
        # here at all. Leaving is the explicit log-out button on the screens above;
        # two ways out on one screen is one too many, and the pair read as a
        # hierarchy that does not exist (this workspace is not "under" the list).
        name = getattr(inside, "name", "") or ""
        if name:
            home_label = catalog.t("nav.workspace_home_named", locale, name=name)
        else:
            home_label = catalog.t("nav.workspace_home", locale)
        if current == "workspace_home":
            # The home screen *is* the workspace; its own entry would point here.
            return []
        return [[inline(home_label, "ws:home", intent=Intent.PRIMARY)]]

    if current != "menu":
        # The one-hub rule: a screen outside a workspace does not repeat the
        # account's own destinations. It carries exactly one nav entry — home.
        return [
            [inline(catalog.t("common.menu", locale), "nav:menu", intent=Intent.PRIMARY)]
        ]

    buttons = []
    for key, label_key, action in DESTINATIONS:
        if key == current:
            continue
        buttons.append(inline(catalog.t(label_key, locale), action, intent=Intent.PRIMARY))
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
