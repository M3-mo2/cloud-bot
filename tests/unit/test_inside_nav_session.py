"""Task 13 — inside a workspace the nav block is the session's, not the account's.

Reported live: the workspace home was cluttered with the top-level account menu.
``nav.nav_rows(..., inside=<ws>)`` appended six entries — the ``🏠 <workspace>``
home, the four account destinations (Settings, Language, Help, Workspaces) and
Menu — so a place the user had *entered* still offered the account's own screens.
The rule: **while inside, the block is up to two entries** — ``ws:home`` and
``ws:list`` — and the account destinations are gone. They stay reachable exactly
as before: the ``/settings`` and ``/help`` commands, and by leaving the
workspace.

Task 14 (R1/R5) refines this: an entry is **omitted**, never muted, when it points
at the screen you are already on. On the workspace home itself that removes
``ws:home``, leaving just ``ws:list``.

While **not inside**, the block keeps its destinations plus Menu — minus its own
(R1).
"""

from __future__ import annotations

import pytest

from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens.language import language_picker

WS = _ws("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=1)

ACCOUNT_ACTIONS = {"nav:settings", "nav:language", "nav:help", "nav:menu"}
SESSION_ACTIONS = {"ws:home", "ws:list"}


def _rows(screen):
    markup = screen.markup
    assert markup is not None, "screen has no keyboard"
    return list(markup.inline_keyboard)


def _callbacks(screen) -> list[str | None]:
    return [b.callback_data for row in _rows(screen) for b in row]


def _nav_block(screen) -> list[list]:
    """The contiguous rows whose buttons all belong to the nav block."""
    return [
        row
        for row in _rows(screen)
        if row and all(b.callback_data in nav.NAV_ACTIONS for b in row)
    ]


def _inside_screens(locale: Locale) -> dict:
    """Every screen the user reaches while inside a workspace (task 13 §2)."""
    return {
        "workspace_home": ws_screens.workspace_detail(WS, locale, active_run=True),
        "workspace_list": ws_screens.workspace_list([WS], locale, inside=WS),
        "help": menu_screens.help_screen(locale, inside=WS),
        "settings": menu_screens.settings_screen(locale, inside=WS),
        "language_picker": language_picker(locale, inside=WS),
        "main_menu": menu_screens.main_menu([WS], locale, display_name="sara", inside=WS),
    }


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize(
    "name",
    ["help", "settings", "language_picker", "main_menu"],
)
def test_inside_block_is_exactly_home_and_workspaces(name, locale):
    callbacks = set(_callbacks(_inside_screens(locale)[name]))
    assert SESSION_ACTIONS <= callbacks, f"{name}/{locale}: the session block is incomplete"
    assert not (ACCOUNT_ACTIONS & callbacks), (
        f"{name}/{locale}: an account destination leaked into the inside block: "
        f"{ACCOUNT_ACTIONS & callbacks}"
    )


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_inside_workspace_list_omits_its_own_destination(locale):
    """R1 on the inside list: main_menu.workspaces is the screen itself, so the
    block is just the workspace-home entry."""
    screen = _inside_screens(locale)["workspace_list"]
    callbacks = set(_callbacks(screen))
    assert "ws:list" not in callbacks
    assert "ws:home" in callbacks
    assert not (ACCOUNT_ACTIONS & callbacks)


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_inside_block_on_the_workspace_home_is_just_workspaces(locale):
    """R5: on the home itself ``ws:home`` is omitted, leaving the session block at
    only ``ws:list`` — and no account destination."""
    screen = _inside_screens(locale)["workspace_home"]
    callbacks = set(_callbacks(screen))
    assert "ws:home" not in callbacks
    assert not (ACCOUNT_ACTIONS & callbacks)
    nav_actions = {c for c in callbacks if c in nav.NAV_ACTIONS}
    assert nav_actions == {"ws:list"}


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize(
    "name",
    ["help", "settings", "language_picker", "main_menu"],
)
def test_inside_block_is_one_row_of_two(name, locale):
    block = _nav_block(_inside_screens(locale)[name])
    assert block, f"{name}/{locale}: no nav block while inside"
    assert len(block) == 1 and len(block[0]) == 2, (
        f"{name}/{locale}: the inside block is not exactly one row of two: {block}"
    )


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_inside_workspace_list_block_is_one_row_of_one(locale):
    """R1: its own entry is omitted, so only the home entry remains."""
    block = _nav_block(_inside_screens(locale)["workspace_list"])
    assert len(block) == 1 and [b.callback_data for b in block[0]] == ["ws:home"]


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_helper_returns_exactly_two_entries_while_inside(locale):
    """The ONE helper is the single source of truth (D1): away from the home, the
    session block is the home entry and the workspaces entry, one row of two."""
    rows = nav.nav_rows(locale, "help", inside=WS)
    callbacks = [b.callback_data for row in rows for b in row]
    assert callbacks == ["ws:home", "ws:list"]
    assert len(rows) <= 3
    for row in rows:
        assert len(row) <= 2


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_home_entry_is_omitted_on_the_workspace_home_but_present_elsewhere(locale):
    home_block = nav.nav_rows(locale, "workspace_home", inside=WS)
    assert [b.callback_data for row in home_block for b in row] == ["ws:list"]

    other_block = nav.nav_rows(locale, "settings", inside=WS)
    home_btns = [b for row in other_block for b in row if b.callback_data == "ws:home"]
    assert home_btns and home_btns[0].disabled is None


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_not_inside_block_minus_its_own_destination(locale):
    """The guard against over-correcting: outside, the account block stays put —
    minus the entry for the screen itself (R1)."""
    rows = nav.nav_rows(locale, "help")
    callbacks = {b.callback_data for row in rows for b in row}
    assert callbacks == {"ws:list", "nav:settings", "nav:language", "nav:menu"}
    assert "nav:help" not in callbacks
    assert "ws:home" not in callbacks


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize(
    "name,current_action",
    [
        ("menu", None),
        ("help", "nav:help"),
        ("settings", "nav:settings"),
        ("workspaces", "ws:list"),
        ("language", "nav:language"),
    ],
)
def test_the_menu_board_still_offers_the_four_destinations(name, current_action, locale):
    screen = menu_screens.main_menu([WS], locale, display_name="sara")
    callbacks = set(_callbacks(screen))
    assert {"ws:list", "nav:settings", "nav:language", "nav:help"} <= callbacks


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_layout_rule_holds_on_every_inside_screen(locale):
    for name, screen in _inside_screens(locale).items():
        block = _nav_block(screen)
        assert len(block) <= 3, f"{name}/{locale}: nav block exceeds three rows"
        for row in block:
            assert len(row) <= 2, f"{name}/{locale}: a nav row has more than two columns"
        for row in _rows(screen):
            assert len(row) <= 2, f"{name}/{locale}: a row has more than two columns"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_layout_rule_holds_on_every_not_inside_screen(locale):
    screens = {
        "main_menu": menu_screens.main_menu([WS], locale, display_name="sara"),
        "welcome": menu_screens.welcome_screen(locale),
        "help": menu_screens.help_screen(locale),
        "settings": menu_screens.settings_screen(locale),
        "ws_list": ws_screens.workspace_list([WS], locale),
        "ws_list_empty": ws_screens.workspace_list([], locale),
        "language_picker": language_picker(locale),
    }
    for name, screen in screens.items():
        block = _nav_block(screen)
        assert block, f"{name}/{locale}: no nav block"
        assert len(block) <= 3, f"{name}/{locale}: nav block exceeds three rows"
        for row in block:
            assert len(row) <= 2, f"{name}/{locale}: a nav row has more than two columns"
        for row in _rows(screen):
            assert len(row) <= 2, f"{name}/{locale}: a row has more than two columns"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("status", list(WorkspaceStatus))
def test_workspace_home_keeps_its_logout_in_every_status_branch(status, locale):
    screen = ws_screens.workspace_detail(_ws("ws_x", "demo", status, active=1), locale)
    callbacks = _callbacks(screen)
    assert any(c and c.startswith("ws:logout") for c in callbacks), (
        f"{status}/{locale}: no explicit log-out on the workspace home"
    )
