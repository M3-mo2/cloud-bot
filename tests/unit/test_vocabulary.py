"""The exhaustiveness + no-raw-output guarantee (UI 05 §8).

Every engine error code and every status renders, in both locales, a human
sentence that contains **no** raw engine token, **no** JSON brace, **no** HTTP
status, and (for Arabic) no ASCII-only sentence.
"""

from __future__ import annotations

import re

import pytest

from cloudbot.adapters.cloud_service.errors import KNOWN_CODES
from cloudbot.core.errors import EngineRefused, EngineUnavailable, InvalidInput, NotOwned, RateLimited, WorkspaceMissing
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.presentation.mapping import errors as error_mapping
from cloudbot.presentation.mapping import statuses
from cloudbot.domain.value_objects import RunStatus, SessionStatus, SummaryState, WorkspaceStatus

_ASCII_ONLY = re.compile(r"^[\x00-\x7f]+$")
_HTTP_STATUS = re.compile(r"\b(4\d\d|5\d\d)\b")

#: Codes whose token is also an ordinary English word used in the human
#: sentence; the substring check would be a false positive for these.
_WORD_CODES = {
    "validation",
    "unauthorized",
    "forbidden",
    "conflict",
    "timeout",
    "internal",
    "unavailable",
}


def _catalog_and_render(code: str, locale: Locale) -> str:
    screen = error_mapping.error_code_screen(code, locale)
    return screen.doc.html


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("code", sorted(KNOWN_CODES))
def test_every_code_maps_and_leaks_nothing(code, locale):
    html = _catalog_and_render(code, locale)
    assert html
    if locale is Locale.EN and code not in _WORD_CODES:
        assert code not in html, f"{code} leaked into EN output"
    assert "{" not in html and "}" not in html, "JSON leaked"
    assert '"code"' not in html
    assert _HTTP_STATUS.search(html) is None, "HTTP status leaked"
    if locale is Locale.AR:
        # The sentence must actually be Arabic, not a fallback English line.
        assert not _ASCII_ONLY.match(html)


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_unknown_code_falls_back_safely(locale):
    html = _catalog_and_render("some_future_code", locale)
    assert "some_future_code" not in html
    assert "{" not in html


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_every_run_status_renders(locale):
    for status in RunStatus:
        label = statuses.run_status_label(status, locale)
        heading = statuses.run_status_heading(status, locale, exit_code=1)
        assert label and heading
        assert status.value not in label, f"{status.value} leaked"
    assert not _ASCII_ONLY.match(statuses.run_status_label(RunStatus.EXITED, Locale.AR))


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_every_workspace_status_and_state_renders(locale):
    for status in WorkspaceStatus:
        label = statuses.workspace_status_label(status, locale)
        assert label
        assert status.value not in label
    for state in SummaryState:
        label = statuses.summary_state_label(state, locale)
        assert label
        assert state.value not in label


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_every_session_status_renders(locale):
    for status in SessionStatus:
        label = statuses.session_status_label(status, locale)
        assert label
        assert status.value not in label


def test_workspace_suspended_and_not_found_are_identical_users_wording():
    # 403 forbidden and 404 not_found render identically.
    en_forbidden = error_mapping.error_code_screen("forbidden", Locale.EN).doc.html
    en_notfound = error_mapping.error_code_screen("not_found", Locale.EN).doc.html
    assert en_forbidden == en_notfound


def test_domain_errors_render_localized():
    for err in (
        NotOwned("ws_x"),
        WorkspaceMissing("ws_x"),
        InvalidInput("name", "too_long"),
        RateLimited(2.0),
        EngineUnavailable(retryable=False),
        EngineUnavailable(retryable=True),
    ):
        for locale in (Locale.EN, Locale.AR):
            screen = error_mapping.error_screen(err, locale)
            assert screen.doc.html
            assert "{" not in screen.doc.html


def test_raw_payload_only_in_debug_admin():
    err = EngineRefused("unavailable", message='{"error":{"code":"unavailable"}}', details={})
    prod = error_mapping.error_screen(err, Locale.EN, mode=RunMode.PRODUCTION, is_admin=True)
    assert "Raw engine response" not in prod.doc.html
    debug_admin = error_mapping.error_screen(
        err, Locale.EN, mode=RunMode.DEBUG, is_admin=True, raw_payload='{"error":"x"}'
    )
    assert "Raw engine response" in debug_admin.doc.html
    debug_nonadmin = error_mapping.error_screen(
        err, Locale.EN, mode=RunMode.DEBUG, is_admin=False, raw_payload='{"error":"x"}'
    )
    assert "Raw engine response" not in debug_nonadmin.doc.html
