"""TASK no-more-less — the workspace home has no overflow toggle.

His words: *"remove the «المزيد» / «إظهار أقل» button and these things — I don't
want them on the main page."* A hosting product's home screen shows the tools; a
user should not have to discover a disclosure control to find them.

The page is now one flat grid: every tool in a single render, two per row, in a
deliberate order (a user's day first, then what the workspace is doing, then
observation), and Settings alone on the last row — the destructive screen is
never adjacent to a button a hand taps by habit.

These guards make the removal stick:

* the rendered keyboard's callback **set** equals the expected set, and neither
  ``ws:more`` nor ``ws:less`` appears anywhere (the bite below re-adds the toggle
  and shows this test goes red);
* every keyboard row is at most two buttons, the last row is not padded, and
  Settings is the only button on its row;
* no router registration anywhere in the repository claims ``ws:more`` /
  ``ws:less`` — the handlers are deleted, not merely hidden, so a leftover route
  is caught rather than sitting unused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import CallbackCodec, cb, decode
from cloudbot.presentation.screens import workspaces as ws_screens

WS = _ws("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=0)
LOCALES = (Locale.EN, Locale.AR)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every tool the page must carry, by the action each destination already had
#: before this task (no destination changed). The order is asserted separately.
EXPECTED_TOOL_ACTIONS = {
    "file:ls",
    "run:choose",
    "arc:menu",
    "term:open",
    "run:history",
    "tpl:list",
    "pkg:list",
    "tnl:list",
    "port:list",
    "proc:list",
    "met:view",
}

#: ``ws:settings`` is the administration destination; ``ws:logout`` is the one
#: exit. Neither is a "tool" in the grid, and both are still required.
EXPECTED_OTHER_ACTIONS = {"ws:settings", "ws:logout"}

#: The reading order, as actions, Settings last. A user's day, not a list.
EXPECTED_ORDER = [
    "file:ls",
    "run:choose",
    "arc:menu",
    "term:open",
    "run:history",
    "tpl:list",
    "pkg:list",
    "tnl:list",
    "port:list",
    "proc:list",
    "met:view",
    "ws:settings",
]


def _callbacks(screen) -> list[str]:
    markup = screen.markup
    assert markup is not None, "the workspace home has no keyboard"
    out: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data:
                out.append(button.callback_data)
    return out


def _actions(screen) -> list[str]:
    return [decode(data).action for data in _callbacks(screen)]


def _assert_flat_home(callbacks: list[str], locale: Locale) -> None:
    """The single assertion the golden guard and the bite share.

    It fails when the toggle is present *or* when any tool is missing — one
    render must carry the whole set, so the two cannot drift.
    """
    assert callbacks, "the workspace home renders no buttons"
    assert not any(d.startswith(("ws:more", "ws:less")) for d in callbacks), (
        f"{locale}: the overflow toggle is back on the home: {callbacks}"
    )
    tool_actions = {decode(d).action for d in callbacks} - EXPECTED_OTHER_ACTIONS
    assert tool_actions == EXPECTED_TOOL_ACTIONS, (
        f"{locale}: the home does not offer every tool in one render: "
        f"{sorted(tool_actions ^ EXPECTED_TOOL_ACTIONS)}"
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_the_home_carries_every_tool_in_one_render(locale):
    """One render, every destination — no disclosure, no second state.

    The callback *set* is compared, not a count: a toggle that hid half the tools
    would change the set, and a duplicated button would not be caught by "at
    least". Nothing here is reached by expanding anything.
    """
    CallbackCodec()
    _assert_flat_home(_callbacks(ws_screens.workspace_detail(WS, locale)), locale)


@pytest.mark.parametrize("locale", LOCALES)
def test_the_grid_is_two_per_row_settings_alone_and_unpadded(locale):
    """The layout rule: ≤2 columns, no padded last row, Settings alone last.

    An odd tool count leaves Settings alone on its own row. Padding that row with
    a disabled placeholder would violate the "no placeholder" rule *and* put a
    dead button beside a destructive screen, so it is asserted explicitly.

    "Last" is measured on the tool grid, not on the whole keyboard: the log-out
    row under it is the session's single exit, not a grid cell, and it is
    asserted to be one button on a row of its own.
    """
    CallbackCodec()
    screen = ws_screens.workspace_detail(WS, locale)
    rows = [list(row) for row in screen.markup.inline_keyboard if row]
    assert rows, "the workspace home has no rows"
    for index, row in enumerate(rows):
        assert 1 <= len(row) <= 2, f"{locale}: row {index} has {len(row)} buttons"

    settings_rows = [
        (index, row)
        for index, row in enumerate(rows)
        if any((b.callback_data or "").startswith("ws:settings") for b in row)
    ]
    assert len(settings_rows) == 1, settings_rows
    settings_index, settings_row = settings_rows[0]
    assert len(settings_row) == 1, f"{locale}: Settings shares its row: {settings_row}"

    # Every row after Settings is the single log-out exit (one button, one row).
    trailing = rows[settings_index + 1 :]
    assert len(trailing) == 1, f"{locale}: more than the log-out follows Settings: {trailing}"
    assert len(trailing[0]) == 1, f"{locale}: the exit row is padded: {trailing[0]}"
    assert (trailing[0][0].callback_data or "").startswith("ws:logout"), trailing[0]

    for row in rows:
        for button in row:
            assert button.callback_data, f"{locale}: a row contains a dead button: {row}"


@pytest.mark.parametrize("locale", LOCALES)
def test_the_reading_order_is_a_user_day(locale):
    """Everyday work, then what the workspace is doing, then observation.

    Asserted on the action sequence, not the label text, so a wording change
    cannot silently reorder the day.
    """
    CallbackCodec()
    screen = ws_screens.workspace_detail(WS, locale)
    order = [a for a in _actions(screen) if a != "ws:logout"]
    assert order == EXPECTED_ORDER, f"{locale}: the grid does not read as a day: {order}"


def test_the_bite_re_adding_the_toggle_fails(monkeypatch):
    """The bite: a ``ws:more`` button on the markup must make the guard red.

    This re-adds exactly the disclosure control the task removed — a ``ws:more``
    button on the rendered home — and shows ``_assert_flat_home`` (the same
    assertion the guard above makes) rejects it. That proves the guard measures
    the absence of the toggle instead of passing vacuously.
    """
    original = ws_screens.workspace_detail_markup

    def with_toggle(ws, locale, **kwargs):
        markup = original(ws, locale, **kwargs)
        markup.inline_keyboard.append([inline("⋯ More", cb("ws:more", ws.id))])
        return markup

    monkeypatch.setattr(ws_screens, "workspace_detail_markup", with_toggle)
    screen = ws_screens.workspace_detail(WS, Locale.EN)
    callbacks = _callbacks(screen)
    assert any(d.startswith("ws:more") for d in callbacks), "the bite did not apply"
    with pytest.raises(AssertionError, match="overflow toggle"):
        _assert_flat_home(callbacks, Locale.EN)


def test_the_set_guard_also_bites_when_a_tool_is_dropped(monkeypatch):
    """The other half: dropping a tool (what a collapsed state did) fails too."""
    original = ws_screens.workspace_detail_markup

    def drop_one(ws, locale, **kwargs):
        markup = original(ws, locale, **kwargs)
        markup.inline_keyboard = [
            [b for b in row if not (b.callback_data or "").startswith("met:view")]
            for row in markup.inline_keyboard
        ]
        return markup

    monkeypatch.setattr(ws_screens, "workspace_detail_markup", drop_one)
    callbacks = _callbacks(ws_screens.workspace_detail(WS, Locale.EN))
    with pytest.raises(AssertionError, match="every tool"):
        _assert_flat_home(callbacks, Locale.EN)


def test_no_router_registers_the_toggle_callbacks():
    """A leftover ``ws:more``/``ws:less`` handler is caught, not merely unused.

    The task requires the *routes* deleted, not hidden: a dead route that still
    renders comes back. This walks every handler module's ``ast`` and collects
    the string literals their decorators filter on, so a re-added route fails
    here even if no screen emits it.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "cloudbot" / "handlers").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                for child in ast.walk(decorator):
                    if (
                        isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                        and ("ws:more" in child.value or "ws:less" in child.value)
                    ):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.name}:{child.value!r}"
                        )
    assert not offenders, f"a toggle route still exists: {offenders}"


def test_no_screen_or_catalog_still_names_the_toggle():
    """A last sweep: the strings, the screen key and the labels are gone.

    This catches the failure mode the task names — a dead route that still
    renders — in the other places it could linger: a screen emitting the
    callback, the catalog still documenting ``ws_detail_more`` as a screen, or a
    catalog string key left behind.
    """
    offenders: list[str] = []
    needles = ("ws:more", "ws:less", "ws_detail_more", '"ws.more"', '"ws.less"')
    for path in sorted((REPO_ROOT / "cloudbot").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
    assert not offenders, f"the toggle still exists somewhere: {offenders}"
