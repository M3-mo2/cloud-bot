"""Terminal presentation: reason mapping, timeout derivation, no raw leakage."""

from __future__ import annotations

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.meta import EngineMeta, SessionConfig
from cloudbot.domain.value_objects import SessionStatus, WorkspaceId
from cloudbot.presentation.mapping.statuses import (
    session_reason_label,
    session_status_label,
)
from cloudbot.presentation.screens import terminal as term_screens

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
SES = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"

#: Every reason the engine can emit (API.md §"Session statuses").
REASONS = [
    "workspace_stopped",
    "workspace_deleted",
    "idle_timeout",
    "lifetime",
    "shell_exit",
    "user_closed",
    "workspace_suspended",
    "service_shutdown",
    "interrupted",
    "sandbox_unavailable",
]


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_every_reason_renders_and_never_shows_raw_token(locale):
    # ``lifetime`` is an ordinary English word used in the sentence itself.
    word_reasons = {"lifetime"}
    for reason in REASONS:
        label = session_reason_label(reason, locale)
        assert label
        if reason not in word_reasons:
            assert reason not in label
    # An unknown reason falls back safely, never the raw token.
    fallback = session_reason_label("some_future_reason", locale)
    assert "some_future_reason" not in fallback


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_failed_diagnostic_never_reaches_a_user(locale):
    session = TerminalSession(
        id=SES,
        workspace_id=WS,
        status=SessionStatus.FAILED,
        reason="sandbox_unavailable",
        diagnostic="sandbox unavailable: PTY allocation failed: sandboxd-pty-error: out of pty devices",
    )
    html = term_screens.terminal_view(session, WS, locale).doc.html
    assert "PTY allocation failed" not in html
    assert "sandboxd-pty-error" not in html
    assert "out of pty devices" not in html
    # The user sees the plain line instead.
    assert "Could not start" in html or "تعذّر" in html


def test_timeout_notice_comes_from_engine_meta():
    meta = EngineMeta(version="0.0.20", api_version="v1")
    assert meta.sessions.idle_timeout_sec == 900
    assert meta.sessions.lifetime_sec == 3600
    notice_en = term_screens.timeout_notice(meta, Locale.EN)
    assert "15m" in notice_en and "1h" in notice_en
    # A configured value flows through (never a hardcoded constant).
    custom = EngineMeta(
        version="0.0.20",
        api_version="v1",
        sessions=SessionConfig(idle_timeout_sec=300, lifetime_sec=7200),
    )
    notice = term_screens.timeout_notice(custom, Locale.EN)
    assert "05m" in notice and "2h" in notice


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_ended_screen_shows_reason_sentence(locale):
    session = TerminalSession(
        id=SES,
        workspace_id=WS,
        status=SessionStatus.TIMED_OUT,
        reason="idle_timeout",
        cols=80,
        rows=24,
    )
    html = term_screens.ended_screen(session, WS, locale).doc.html
    assert "idle_timeout" not in html
    assert "inactivity" in html or "خمول" in html


def test_terminal_has_a_visible_way_out_on_every_screen():
    """The FSM is only entered by an explicit action, and buttons are always shown."""
    session = TerminalSession(id=SES, workspace_id=WS, status=SessionStatus.RUNNING)
    view = term_screens.terminal_view(session, WS, Locale.EN)
    labels = [b.text for row in view.markup.inline_keyboard for b in row]
    assert any("Close" in label for label in labels)
    assert any("My terminals" in label for label in labels)
    assert any("Back" in label for label in labels)
