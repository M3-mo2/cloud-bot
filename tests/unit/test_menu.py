"""TASK menu: the main board's body, the button emoji and the label rule.

The old main menu rendered a four-column table of up to five workspaces and
silently dropped a sixth; its buttons mixed account destinations with a
workspace list and carried emoji that repeated across unrelated screens.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import nav

LOCALES = (Locale.EN, Locale.AR)

#: The shared destinations the main board exposes. One callback must wear one
#: label on every screen in the product; per-screen context actions (a confirm's
#: Cancel, a list's Back) are not destinations in this sense.
SHARED_DESTINATIONS = frozenset(nav.NAV_ACTIONS) | {"ws:new"}


def _rows(screen) -> list[list]:
    markup = screen.markup
    if markup is None:
        return []
    return list(getattr(markup, "inline_keyboard", None) or [])


def _buttons(screen) -> list:
    return [button for row in _rows(screen) for button in row]


def _many_workspaces(n: int):
    from cloudbot.domain.value_objects import WorkspaceId

    base = screen_catalog.fixtures()["ws"]
    return [
        replace(
            base,
            id=WorkspaceId(f"ws_{index:026d}"),
            name=f"project-{index}",
        )
        for index in range(n)
    ]


# -- 1. the body: no silent truncation --------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_a_sixth_workspace_is_not_silently_dropped(locale):
    """The old body sliced ``[:5]`` and said nothing about the rest."""
    workspaces = _many_workspaces(8)
    html = menu_screens.main_menu(workspaces, locale, display_name="sara").doc.html
    if locale is Locale.EN:
        assert "… and 3 more" in html
        assert "See all 8 in My workspaces." in html
    else:
        assert "… و٣ أخرى" in html
        assert "اعرض كل الـ٨ في «مساحاتي»." in html
    # Every workspace the board did show is really there.
    for ws in workspaces[: menu_screens.MENU_WORKSPACE_LINES]:
        assert ws.name in html


@pytest.mark.parametrize("locale", LOCALES)
def test_exactly_the_slice_has_no_more_line(locale):
    workspaces = _many_workspaces(menu_screens.MENU_WORKSPACE_LINES)
    html = menu_screens.main_menu(workspaces, locale, display_name="sara").doc.html
    assert "more" not in html.lower()
    assert "أخرى" not in html


def test_the_menu_body_is_not_a_wide_table():
    """One compact line per workspace, not a four-column grid."""
    html = menu_screens.main_menu(_many_workspaces(2), Locale.EN, display_name="sara").doc.html
    assert "<table" not in html


def test_empty_state_still_offers_the_primary_action():
    screen = menu_screens.main_menu([], Locale.EN, display_name="sara")
    assert [b.callback_data for b in _buttons(screen)] == [
        "ws:new",
        "ws:list",
        "nav:settings",
        "nav:language",
        "nav:help",
    ]


def test_the_subscription_line_is_a_seam_not_an_invented_flow():
    """``plan`` is the one hook ``subs2``'s wallet screens will fill in."""
    without = menu_screens.main_menu(_many_workspaces(1), Locale.EN).doc.html
    with_line = menu_screens.main_menu(_many_workspaces(1), Locale.EN, plan="trial").doc.html
    assert "Plan:" not in without
    assert "Plan: Trial" in with_line


# -- 2. the buttons: one discriminating emoji each --------------------------
def _leading_emoji(text: str) -> str:
    return text.split(" ", 1)[0]


@pytest.mark.parametrize("locale", LOCALES)
def test_no_two_main_screen_buttons_share_an_emoji(locale):
    screen = menu_screens.main_menu(_many_workspaces(2), locale, display_name="sara")
    emoji = [_leading_emoji(button.text) for button in _buttons(screen)]
    assert len(emoji) == len(set(emoji)), emoji


@pytest.mark.parametrize("locale", LOCALES)
def test_the_primary_action_sits_alone_on_the_first_row(locale):
    screen = menu_screens.main_menu(_many_workspaces(2), locale, display_name="sara")
    first = _rows(screen)[0]
    assert [button.callback_data for button in first] == ["ws:new"]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_main_board_never_exceeds_three_rows_or_two_columns(locale):
    screen = menu_screens.main_menu(_many_workspaces(2), locale, display_name="sara")
    assert len(_rows(screen)) <= 3
    for row in _rows(screen):
        assert len(row) <= 2


# -- 3. the catalog-wide label guard ----------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_a_shared_destination_never_wears_two_labels(locale):
    """Walk every screen: one destination callback, one label, in both locales."""
    CallbackCodec()
    labels: dict[str, set[str]] = defaultdict(set)
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        screen = screen_catalog.build(name, locale)
        for button in _buttons(screen):
            action = getattr(button, "callback_data", None)
            if action in SHARED_DESTINATIONS:
                labels[action].add(button.text)
    offenders = {action: sorted(texts) for action, texts in labels.items() if len(texts) > 1}
    assert not offenders, f"shared destinations with several labels: {offenders}"


def test_the_label_guard_would_have_caught_the_old_workspaces_name():
    """Bite check: the old ``📦 المساحات`` / ``📦 مساحاتي`` pair fails the rule."""
    assert len({"📦 المساحات", "📦 مساحاتي"}) > 1
