"""TASK nav-onehub — the main menu is the only hub.

His words: a logged-out user on «مساحاتي» should not see Settings / Wallet /
Help / Language again; every outside screen carries one «🏠 الصفحة الرئيسية»
button, Settings holds Help and Language only, Help is a page, and the wallet
keeps only money. These guards make that stick:

* **the hub rule** — every outside screen that is not the menu carries exactly
  ``{"nav:menu"}`` (adding back ``📦 مساحاتي`` on the wallet fails);
* **the menu holds the four** — new / workspaces / wallet / settings, and not
  help or language;
* **Settings holds exactly two** — help + language, plus home;
* **Help is a leaf** — no account destinations;
* **no dead ends** — every outside top-level screen still offers home;
* **the layout rule** — two columns max, nav block three rows max.

The inside-a-workspace block is deliberately *not* asserted here; it is covered,
unmodified, by ``test_inside_nav_session.py`` and ``test_workspace_home_exit.py``.
"""

from __future__ import annotations

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.screens import nav

LOCALES = (Locale.EN, Locale.AR)

#: Screens rendered inside a workspace by construction — their block is the
#: session's, and this task must not touch it.
INSIDE = frozenset(
    {
        "menu_inside",
        "help_inside",
        "settings_inside",
        "language_picker_inside",
        "ws_list_inside",
        "ws_detail",
        # ``ws_detail_more`` is gone: the workspace home is one flat page with no
        # disclosure control (TASK no-more-less), so it is no longer a screen.
        "ws_detail_suspended",
        "ws_detail_locked",
        "ws_create_name",
        "ws_create_network",
        "ws_create_plan",
        "ws_create_plan_short",
        "ws_create_plan_trial_used",
    }
)

#: The menu itself (the only hub) and its empty state.
MENU_SCREENS = frozenset({"menu", "menu_empty"})

#: Every top-level account screen reached while outside a workspace.
OUTSIDE_TOP_LEVEL = (
    "menu",
    "menu_empty",
    "ws_list",
    "ws_list_empty",
    "wallet",
    "wallet_no_plan",
    "wallet_expired",
    "wallet_two_plans",
    "help",
    "settings",
    "language_picker",
)


def _screen(name: str, locale: Locale):
    CallbackCodec()
    return screen_catalog.build(name, locale)


def _nav_actions(screen) -> set[str]:
    markup = screen.markup
    if markup is None:
        return set()
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data in nav.NAV_ACTIONS
    }


def _all_actions(screen) -> set[str]:
    markup = screen.markup
    if markup is None:
        return set()
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def _nav_block(screen) -> list[list]:
    markup = screen.markup
    if markup is None:
        return []
    return [
        row
        for row in markup.inline_keyboard
        if row and all(b.callback_data in nav.NAV_ACTIONS for b in row)
    ]


# -- 1. the hub rule ---------------------------------------------------------
#: The account screens that are *not* a hub. Settings is the second hub by
#: design (help + language), so it is asserted separately.
NON_HUB_OUTSIDE = tuple(
    name for name in OUTSIDE_TOP_LEVEL if name not in MENU_SCREENS and name != "settings"
)


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", NON_HUB_OUTSIDE)
def test_every_non_hub_outside_screen_carries_only_home(name, locale):
    """Outside a workspace, a screen that is not a hub carries exactly one entry.

    The rule is about the **nav block** (the trailing rows), not a screen's own
    action: the wallet's ``🔄 Refresh`` legitimately reaches ``pay:wallet`` again
    without being a destination, and Settings' two entries are asserted below.
    Adding ``📦 My workspaces`` back to the wallet fails this test.
    """
    block = _nav_block(_screen(name, locale))
    callbacks = {b.callback_data for row in block for b in row}
    assert callbacks == {"nav:menu"}, f"{name}/{locale}: nav block is {callbacks}"


@pytest.mark.parametrize("locale", LOCALES)
def test_no_outside_screen_repeats_an_account_destination_beside_home(locale):
    """Walk the catalog-wide account screens: home is the *only* entry afforded.

    Menu (home), Settings (the second hub) and inside-a-workspace screens are
    excluded by construction. System/error recovery screens are not account
    screens; their one contextual route is pinned by their own tests.
    """
    offenders: dict[str, set[str]] = {}
    for name in NON_HUB_OUTSIDE:
        callbacks = {b.callback_data for row in _nav_block(_screen(name, locale)) for b in row}
        if callbacks != {"nav:menu"}:
            offenders[name] = callbacks
    assert not offenders, f"{locale.value}: outside screens repeating destinations: {offenders}"


@pytest.mark.parametrize("locale", LOCALES)
def test_the_wallet_does_not_offer_another_account_destination(locale):
    """The explicit example from his words: the wallet keeps only money + home."""
    screen = _screen("wallet", locale)
    block = {b.callback_data for row in _nav_block(screen) for b in row}
    assert block == {"nav:menu"}
    # The wallet's own money actions survive; no account destination does.
    actions = _all_actions(screen)
    assert {"pay:packs", "pay:plans", "pay:history"} <= actions
    assert not ({"ws:list", "nav:settings", "nav:language", "nav:help"} & actions)


# -- 2. the menu holds the four ---------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_menu_offers_new_workspaces_wallet_settings_and_no_help_language(locale):
    for name in ("menu", "menu_empty"):
        actions = _all_actions(_screen(name, locale))
        assert {"ws:new", "ws:list", "pay:wallet", "nav:settings"} <= actions
        assert "nav:help" not in actions, f"{name}/{locale}: Help leaked back onto the menu"
        assert "nav:language" not in actions, f"{name}/{locale}: Language leaked back onto the menu"
        # The menu is home, so it offers no home entry.
        assert "nav:menu" not in actions


# -- 3. Settings holds exactly two ------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_settings_is_a_two_entry_hub(locale):
    screen = _screen("settings", locale)
    actions = _all_actions(screen)
    assert {"nav:help", "nav:language"} <= actions
    assert "nav:menu" in actions, "Settings must offer a way home"
    # Nothing else but help, language and home.
    assert actions == {"nav:help", "nav:language", "nav:menu"}


# -- 4. Help is a leaf -------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_help_is_a_page_not_a_hub(locale):
    actions = _all_actions(_screen("help", locale))
    assert actions == {"nav:menu"}, f"help/{locale} still offers {actions}"


# -- 5. no dead ends ---------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", OUTSIDE_TOP_LEVEL)
def test_every_outside_top_level_screen_offers_home(name, locale):
    actions = _all_actions(_screen(name, locale))
    if name in MENU_SCREENS:
        # The hub itself is home; it must not point at itself.
        assert "nav:menu" not in actions
    else:
        assert "nav:menu" in actions, f"{name}/{locale}: no way home"


# -- 6. the layout rule ------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", OUTSIDE_TOP_LEVEL)
def test_layout_rule_holds_on_every_outside_screen(name, locale):
    screen = _screen(name, locale)
    markup = screen.markup
    if markup is not None:
        for row in markup.inline_keyboard:
            assert len(row) <= 2, f"{name}/{locale}: a row has more than two columns"
    block = _nav_block(screen)
    assert len(block) <= 3, f"{name}/{locale}: nav block exceeds three rows"
