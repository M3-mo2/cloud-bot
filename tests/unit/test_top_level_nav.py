"""Top-level navigation: the shared inline nav block on every top-level screen.

There is no reply keyboard. Every top-level screen ends in the same 2×2 nav block
(Workspaces / Settings / Language / Help, plus Menu where the screen is not the
menu), with the destination the user is already on rendered muted (inert). The
block never exceeds two columns or three rows.
"""

from __future__ import annotations

import pytest
from aiogram.types import DisabledButton

from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens.language import language_picker

WS = _ws("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=1)


def _rows(screen):
    markup = screen.markup
    assert markup is not None, "screen has no keyboard"
    return list(markup.inline_keyboard)


def _callback(button) -> str | None:
    return button.callback_data


def _nav_rows(screen) -> list[list]:
    """Contiguous rows made only of nav-block callbacks."""
    return [
        row
        for row in _rows(screen)
        if row and all(_callback(b) in nav.NAV_ACTIONS for b in row)
    ]


def _screen_cases():
    return {
        "main_menu": menu_screens.main_menu([WS], Locale.EN, display_name="sara"),
        "welcome": menu_screens.welcome_screen(Locale.EN),
        "help": menu_screens.help_screen(Locale.EN),
        "settings": menu_screens.settings_screen(Locale.EN),
        "ws_list": ws_screens.workspace_list([WS], Locale.EN),
        "ws_list_empty": ws_screens.workspace_list([], Locale.EN),
        "language_picker": language_picker(Locale.EN),
    }


def _screens_both_locales():
    out = {}
    for locale in (Locale.EN, Locale.AR):
        out[("main_menu", locale)] = menu_screens.main_menu(
            [WS], locale, display_name="sara"
        )
        out[("welcome", locale)] = menu_screens.welcome_screen(locale)
        out[("help", locale)] = menu_screens.help_screen(locale)
        out[("settings", locale)] = menu_screens.settings_screen(locale)
        out[("ws_list", locale)] = ws_screens.workspace_list([WS], locale)
        out[("ws_list_empty", locale)] = ws_screens.workspace_list([], locale)
        out[("language_picker", locale)] = language_picker(locale)
    return out


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize(
    "name", ["main_menu", "welcome", "help", "settings", "ws_list", "ws_list_empty", "language_picker"]
)
def test_every_top_level_screen_has_the_nav_block(name, locale):
    screen = _screens_both_locales()[(name, locale)]
    nav_rows = _nav_rows(screen)
    assert nav_rows, f"{name}/{locale} has no nav block"
    assert len(nav_rows) <= 3, f"{name}/{locale} has >3 nav rows"
    for row in nav_rows:
        assert len(row) <= 2, f"{name}/{locale} nav row has >2 columns"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_no_screen_exceeds_two_columns_anywhere(locale):
    for name, screen in _screens_both_locales().items():
        if name[1] is not locale:
            continue
        for row in _rows(screen):
            assert len(row) <= 2, f"{name} has a row with more than two columns"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize(
    "name,current_action",
    [
        ("help", "nav:help"),
        ("settings", "nav:settings"),
        ("ws_list", "ws:list"),
        ("ws_list_empty", "ws:list"),
        ("language_picker", "nav:language"),
    ],
)
def test_current_destination_is_muted_and_not_tappable(name, current_action, locale):
    screen = _screens_both_locales()[(name, locale)]
    flat = [b for row in _rows(screen) for b in row]
    current = [b for b in flat if _callback(b) == current_action]
    assert current, f"{name}/{locale} does not render its own destination"
    for button in current:
        assert isinstance(button.disabled, DisabledButton), (
            f"{name}/{locale}: current destination {current_action} is tappable"
        )


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_menu_boards_do_not_offer_a_tappable_menu(locale):
    """On the menu, the current surface (the menu) is simply not offered, and no
    destination is muted — everything is reachable."""
    for name in ("main_menu", "welcome"):
        screen = _screens_both_locales()[(name, locale)]
        flat = [b for row in _rows(screen) for b in row]
        assert all(_callback(b) != "nav:menu" for b in flat)
        assert all(b.disabled is None for b in flat), (
            f"{name}/{locale} muted a destination on the menu board"
        )


def test_the_four_destinations_are_reachable_from_the_menu():
    actions = {_callback(b) for row in _rows(_screen_cases()["main_menu"]) for b in row}
    assert {"ws:list", "nav:settings", "nav:language", "nav:help"} <= actions


def test_menu_is_not_offered_on_the_menu_itself():
    actions = {_callback(b) for row in _rows(_screen_cases()["main_menu"]) for b in row}
    assert "nav:menu" not in actions


def test_menu_is_offered_on_non_menu_top_level_screens():
    for name in ("help", "settings", "ws_list", "ws_list_empty", "language_picker"):
        actions = {_callback(b) for row in _rows(_screen_cases()[name]) for b in row}
        assert "nav:menu" in actions, f"{name} does not offer Menu"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_main_menu_full_width_primary_then_nav_block(locale):
    """The rebuild: one full-width primary action, then the 2×2 nav block."""
    screen = menu_screens.main_menu([WS], locale, display_name="sara")
    rows = _rows(screen)
    assert [b.callback_data for b in rows[0]] == ["ws:new"]
    assert len(rows[0]) == 1
    # Everything after the first row is nav.
    assert all(_callback(b) in nav.NAV_ACTIONS for row in rows[1:] for b in row)


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_no_screen_returns_a_reply_keyboard(locale):
    for name, screen in _screens_both_locales().items():
        if name[1] is not locale:
            continue
        markup = screen.markup
        assert getattr(markup, "keyboard", None) is None, (
            f"{name} returned a reply keyboard"
        )
        assert getattr(markup, "inline_keyboard", None) is not None


def test_welcome_board_keeps_its_inline_start_button():
    """His b606238 fix: the empty board is still tappable inline."""
    screen = menu_screens.main_menu([], Locale.EN, display_name="sara")
    actions = {_callback(b) for row in _rows(screen) for b in row}
    assert "ws:new" in actions
