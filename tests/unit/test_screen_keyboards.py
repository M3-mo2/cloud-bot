"""Keyboard conventions across screens: styles allowed, callbacks ≤ 64 bytes,
and every destructive action is behind a confirm."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.keyboards.styles import ALLOWED_STYLES
from cloudbot.presentation.screens import workspaces as ws_screens

WS = _ws("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=0)
NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)


def _buttons(markup):
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None)
    if rows is None:
        rows = getattr(markup, "keyboard", None)
    if rows is None:
        return []
    return [b for row in rows for b in row]


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_all_button_styles_are_allowed(locale):
    for screen in (
        ws_screens.workspace_detail(WS, locale, now=NOW),
        ws_screens.delete_confirm(WS, locale),
        ws_screens.stop_confirm(WS, locale),
        ws_screens.suspend_confirm(WS, locale),
    ):
        for button in _buttons(screen.markup):
            style = getattr(button, "style", None)
            if style is not None:
                assert style in ALLOWED_STYLES, f"bad style {style!r}"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_callbacks_within_64_bytes(locale):
    for screen in (
        ws_screens.workspace_detail(WS, locale, now=NOW),
        ws_screens.delete_confirm(WS, locale),
    ):
        for button in _buttons(screen.markup):
            data = getattr(button, "callback_data", None)
            if data:
                assert len(data.encode("utf-8")) <= 64, data


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_danger_buttons_point_to_confirms(locale):
    # On the detail screen, Stop All and Delete are danger and open a confirm.
    screen = ws_screens.workspace_detail(WS, locale, now=NOW)
    danger = [b for b in _buttons(screen.markup) if getattr(b, "style", None) == "danger"]
    assert danger, "detail screen must expose destructive actions"
    for button in danger:
        assert "confirm" in (button.callback_data or "")


def test_active_detail_has_run_button():
    screen = ws_screens.workspace_detail(WS, Locale.EN, now=NOW)
    callbacks = [b.callback_data for b in _buttons(screen.markup)]
    assert any(c and c.startswith("run:choose") for c in callbacks)


def test_suspended_detail_disables_run():
    suspended = _ws("ws_susp", "scraper", WorkspaceStatus.SUSPENDED)
    screen = ws_screens.workspace_detail(suspended, Locale.EN, now=NOW)
    run_buttons = [b for b in _buttons(screen.markup) if b.callback_data and b.callback_data.startswith("run:choose")]
    assert run_buttons
    assert all(b.disabled is not None for b in run_buttons)


def test_locked_detail_shows_renew_and_banner():
    from cloudbot.adapters.cloud_service.fake import _ws as mk

    locked = mk("ws_lock", "legacy", WorkspaceStatus.LOCKED)
    screen = ws_screens.workspace_detail(locked, Locale.AR, now=NOW)
    assert "plan has expired" in screen.doc.html or "انتهت صلاحية خطتك" in screen.doc.html
