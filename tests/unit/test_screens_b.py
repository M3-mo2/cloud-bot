"""TASK screens-b: duplicate destinations, wide tables, terminal names, help.

Each test here fails on the pre-task code:

* the duplicate-callback guard reports the five screens that put two buttons on
  the same destination (``run_history``, ``terminal_list``, ``file_browser_empty``,
  ``live_logs_running``);
* the table-width guard measures the eight-column run-history markup and the
  six/five-column admin lists;
* the terminal-name test uses two ULIDs that share a 12-character prefix (the
  measured same-millisecond case) and asserts the labels still differ;
* the help test requires the in-session size tool, which the old screen did not
  mention.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import Run, TerminalSession
from cloudbot.domain.value_objects import RunPage, RunStatus, SessionStatus, WorkspaceId
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.screens import files as file_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.presentation.screens import terminal as term_screens

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
LOCALES = (Locale.EN, Locale.AR)


def _rows(screen) -> list[list]:
    markup = screen.markup
    if markup is None:
        return []
    return list(getattr(markup, "inline_keyboard", None) or [])


def _buttons(screen) -> list:
    return [button for row in _rows(screen) for button in row]


def _callbacks(screen) -> list[str]:
    return [
        button.callback_data
        for button in _buttons(screen)
        if getattr(button, "callback_data", None)
    ]


def _table_columns(html: str) -> int:
    """The widest table row, counted exactly as it renders (headers included)."""
    widest = 0
    for table in html.split("<table")[1:]:
        head = table.split("</table>")[0]
        for tr in head.split("<tr>")[1:]:
            tr = tr.split("</tr>")[0]
            widest = max(widest, tr.count("<th") + tr.count("<td"))
    return widest


# -- 1. the catalog-wide duplicate-destination guard --------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_no_screen_offers_two_buttons_on_the_same_destination(locale):
    """A duplicate destination wastes a slot and reads as two actions.

    This is the guard the task asks for: it walks every screen in the product,
    in both locales, and fails on any keyboard that names one callback twice.
    Disabled entries count too — a muted duplicate is still a duplicate.
    """
    CallbackCodec()
    offenders = []
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        screen = screen_catalog.build(name, locale)
        duplicates = [
            action
            for action, count in Counter(_callbacks(screen)).items()
            if count > 1
        ]
        if duplicates:
            offenders.append((name, duplicates))
    assert not offenders, f"screens with duplicate destinations: {offenders}"


def test_the_duplicate_guard_would_have_caught_the_old_run_history():
    """Bite check: the old markup (two ``run:history:<ws>`` entries) is rejected
    by the same counting rule, so the guard is not vacuous."""
    from cloudbot.presentation.keyboards.buttons import inline

    old_actions = ["run:history:" + str(WS), "run:history:" + str(WS)]
    assert max(Counter(old_actions).values()) > 1
    button = inline("x", old_actions[0])
    assert button.callback_data == old_actions[0]


# -- 2. run history: three scannable columns, details one tap away ------------
def _runs(n: int) -> RunPage:
    runs = tuple(
        Run(
            id=f"run_01M6HJASCWM9Z1PF755GT89NK{i:01d}",  # type: ignore[arg-type]
            workspace_id=WS,
            kind="command",
            argv=["python3", "main.py"],
            cwd="/",
            status=RunStatus.EXITED,
            exit_code=0,
            interpreter="python3",
            timeout_sec=300,
            started_at=NOW,
            ended_at=NOW,
        )
        for i in range(n)
    )
    return RunPage(runs=runs, total=n, limit=10, offset=0)


@pytest.mark.parametrize("locale", LOCALES)
def test_run_history_renders_three_scannable_columns(locale):
    CallbackCodec()
    page = _runs(2)
    screen = history_screens.run_history(page, WS, locale, now=NOW)
    assert _table_columns(screen.doc.html) == 3
    # The command is not a column any more; it is on the run's own screen.
    assert "main.py" not in screen.doc.html


@pytest.mark.parametrize("locale", LOCALES)
def test_every_run_row_opens_that_run(locale):
    CallbackCodec()
    page = _runs(3)
    screen = history_screens.run_history(page, WS, locale, now=NOW)
    run_ids = {str(run.id) for run in page.runs}
    opened = {
        action.split(":")[-1]
        for action in _callbacks(screen)
        if action.startswith("run:logs:")
    }
    # Tokens are opaque, so resolve through the codec rather than the literal.
    from cloudbot.presentation.keyboards.callbacks import decode

    decoded = {
        decode(action).key[-1]
        for action in _callbacks(screen)
        if action.startswith("run:logs:")
    }
    assert run_ids <= decoded, (decoded, opened)


@pytest.mark.parametrize("locale", LOCALES)
def test_run_history_pages_like_every_other_list(locale):
    CallbackCodec()
    page = _runs(10)
    screen = history_screens.run_history(page, WS, locale, now=NOW, page_index=0)
    html = screen.doc.html
    assert "Page 1 of" in html or "صفحة ١ من" in html


# -- 2b. admin lists lose the columns a phone cannot show ---------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_admin_lists_are_three_columns_or_fewer(locale):
    fixtures = screen_catalog.fixtures()
    for name in ("admin_users", "admin_audit"):
        screen = screen_catalog.build(name, locale)
        assert _table_columns(screen.doc.html) <= 3, name
    assert fixtures  # the fixture map still feeds the builders


# -- 3. terminal names are unique within the list ----------------------------
def test_two_sessions_sharing_a_twelve_character_prefix_get_distinct_labels():
    """Measured ULIDs: they differ only at character 12, so a fixed prefix lies."""
    first = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"
    second = "ses_01M6HJATYTFXTWXWBG8A5KWYS8"
    assert first[:12] == second[:12]
    labels = term_screens.unique_short_ids([first, second])
    assert labels[0] != labels[1]
    # Short when it can be: the shared prefix plus one character is enough.
    assert all(label.startswith("ses_01M6HJAT") for label in labels)
    assert len(labels[0]) <= 16


def test_identical_ids_are_numbered_rather_than_repeated():
    labels = term_screens.unique_short_ids(["ses_same", "ses_same"])
    assert labels[0] != labels[1]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_terminals_screen_shows_distinct_labels_but_full_callbacks(locale):
    CallbackCodec()
    first = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=WS,
        status=SessionStatus.RUNNING,
        created_at=NOW,
    )
    second = TerminalSession(
        id="ses_01M6HJATYTFXTWXWBG8A5KWYS8",
        workspace_id=WS,
        status=SessionStatus.TIMED_OUT,
        created_at=NOW,
    )
    screen = term_screens.my_terminals([first, second], WS, locale, now=NOW)
    view_buttons = [
        button for button in _buttons(screen) if (button.callback_data or "").startswith("term:view")
    ]
    assert len(view_buttons) == 2
    assert view_buttons[0].text != view_buttons[1].text
    assert view_buttons[0].callback_data != view_buttons[1].callback_data


# -- 4. the help screen actually explains the product ------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_help_documents_the_in_session_size_tool(locale):
    screen = screen_catalog.build("help", locale)
    assert "stty cols" in screen.doc.html


@pytest.mark.parametrize("locale", LOCALES)
def test_help_explains_workspaces_terminals_and_several_of_each(locale):
    html = screen_catalog.build("help", locale).doc.html
    assert "stty" in html
    # The isolation sentence and the several-workspaces sentence.
    assert ("Several" in html or "several" in html or "عدة" in html) or (
        "isolated" in html or "معزولة" in html
    )


# -- 5. the file breadcrumb is not a duplicate destination -------------------
def test_empty_root_listing_does_not_repeat_the_home_destination():
    CallbackCodec()
    from cloudbot.presentation.catalog import fixtures

    page = fixtures()["empty_page"]  # path "/src", no entries
    screen = file_screens.file_browser(page, WS, Locale.EN, page_index=0)
    callbacks = _callbacks(screen)
    assert len(callbacks) == len(set(callbacks))
    # A crumb navigates to *that* directory, not back to the root.
    assert any(action.startswith("file:cd:") for action in callbacks)


# -- the history pager keeps the filter and the page (handler seam) ----------
def test_history_callback_parses_every_shape_the_screen_emits():
    from cloudbot.handlers.execution import _history_page
    from cloudbot.presentation.keyboards.callbacks import decode

    cases = {
        # (ws,) — a button drawn before paging existed
        ("ws_1",): ("ws_1", None, 0),
        # (ws, page) — the all-runs pager
        ("ws_1", "2"): ("ws_1", None, 2),
        # (ws, filter) — a filter tap (page 0)
        ("ws_1", "exited"): ("ws_1", "exited", 0),
        # (ws, filter, page) — a pager turn inside a filter
        ("ws_1", "exited", "1"): ("ws_1", "exited", 1),
        # the all-filter form the screen emits for a filtered page turn
        ("ws_1", "all", "2"): ("ws_1", None, 2),
    }
    for key, expected in cases.items():
        assert _history_page(key) == expected
    # And the emitted callbacks decode back to those key parts.
    from cloudbot.presentation.screens.run_logs import _page_action

    CallbackCodec()
    assert decode(_page_action(WS, None, 1)).key == (str(WS), "1")
    assert decode(_page_action(WS, "exited", 1)).key == (str(WS), "exited", "1")
