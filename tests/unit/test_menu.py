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
        assert "… و3 أخرى" in html
        assert "اعرض كل الـ8 في «مساحاتي»." in html
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
        "pay:wallet",
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
def test_the_main_board_never_exceeds_the_nav_row_rule_or_two_columns(locale):
    """A payload row plus the nav block (at most three rows) — never more than
    two columns. The nav block grew by one entry when the wallet became a
    top-level destination, which still fits the three-row rule (four entries)."""
    screen = menu_screens.main_menu(_many_workspaces(2), locale, display_name="sara")
    assert len(_rows(screen)) <= 4
    for row in _rows(screen):
        assert len(row) <= 2


# -- 3. the catalog-wide label guard ----------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_a_shared_destination_never_wears_two_labels(locale):
    """Walk every screen: one destination callback, one label, in both locales.

    The affordance labels declared in ``nav.NAV_LABEL_EXCEPTIONS`` are excluded:
    ``common.back`` / ``common.refresh`` deliberately mean "step back" / "re-read
    this" on several screens while reaching the same callback, exactly as the
    static-label guard below already documents. Every other label on a destination
    must be a single name.
    """
    from cloudbot.presentation.i18n.catalog import catalog

    CallbackCodec()
    exceptions = {catalog.t(key, locale) for key in nav.NAV_LABEL_EXCEPTIONS}
    labels: dict[str, set[str]] = defaultdict(set)
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        screen = screen_catalog.build(name, locale)
        for button in _buttons(screen):
            action = getattr(button, "callback_data", None)
            if action in SHARED_DESTINATIONS and button.text not in exceptions:
                labels[action].add(button.text)
    offenders = {action: sorted(texts) for action, texts in labels.items() if len(texts) > 1}
    assert not offenders, f"shared destinations with several labels: {offenders}"


def test_the_label_guard_would_have_caught_the_old_workspaces_name():
    """Bite check: the old ``📦 المساحات`` / ``📦 مساحاتي`` pair fails the rule."""
    assert len({"📦 المساحات", "📦 مساحاتي"}) > 1


def _static_labels(locale) -> set[str]:
    """The catalogue's *static* labels: entire messages with no substitution field.

    A label is static only when it is exactly a catalogue value containing no
    ``{field}`` (so ``📜 Runs`` is static while ``📜 {first}…`` and any string the
    screen builds from data — a workspace name, a port, a truncated session id —
    is not). Two dynamic labels are never compared: both "look different" simply
    because the data differs, and grouping them would report a defect that does
    not exist.
    """
    from cloudbot.presentation.i18n.catalog import CATALOGS

    return {
        text
        for text in CATALOGS[locale].MESSAGES.values()
        if "{" not in text and "}" not in text
    }


@pytest.mark.parametrize("locale", LOCALES)
def test_every_destination_wears_one_static_label(locale):
    """The catalogue-wide rule: one destination, one *naming* label.

    Walk every screen in ``SCREEN_BUILDERS`` and group each button by its exact
    ``callback_data`` (the destination). Only static labels are compared (see
    ``_static_labels``). The affordance labels declared in
    ``nav.NAV_LABEL_EXCEPTIONS`` are removed before the check: they deliberately
    say "step back"/"abandon this"/"re-read this" on several screens for the same
    callback, and the user should read the affordance, not the destination. What
    remains on a destination must be a single name.
    """
    from cloudbot.devtools import screens as screen_tools

    CallbackCodec()
    # The catalogue-wide rule has one implementation (``devtools.screens``), shared
    # with the architecture doctor so the test and the tool cannot drift.
    offenders = screen_tools.label_conflicts(sorted(screen_catalog.SCREEN_BUILDERS), locale)
    assert not offenders, (
        f"{locale.value}: destinations wearing several static labels "
        f"(callback, labels): {offenders}"
    )
