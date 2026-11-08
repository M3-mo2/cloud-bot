"""Golden snapshots: every screen rendered in both locales, compared byte-for-byte.

Run with ``UPDATE_GOLDEN=1`` to (re)write the expected files. A change in wording
becomes a visible diff. Arabic snapshots must be ``is_rtl=True`` and contain
Arabic text; no snapshot may contain a raw engine token.

The screen list and its fixtures no longer live here — they live in the
**product** (``cloudbot/presentation/catalog.py``). This module keeps only the
goldens and the ``UPDATE_GOLDEN`` behaviour; adding a screen is one entry in the
catalog and it is automatically covered here, by the button walk, and by ``/dev``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec

EXPECTED = Path(__file__).resolve().parent / "expected"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

RAW_TOKENS = ("rate_limited", "unprocessable", "workspace_suspended")
RAW_JSON_MARKERS = ('{"error"', '"code":')

#: Screens that are deliberately not RTL even in Arabic (bilingual by design).
NEVER_RTL = screen_catalog.NEVER_RTL


@pytest.mark.parametrize("name", sorted(screen_catalog.SCREEN_BUILDERS))
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_golden_screen(name, locale):
    CallbackCodec()  # deterministic content-addressed tokens per render
    screen = screen_catalog.build(name, locale)
    built = screen.doc.build()
    # A screen may build in **blocks** rather than html (in-body buttons, Bot API
    # 10.3) — Telegram allows either form, never both. Snapshot whichever was
    # built, so the golden still locks what the user will actually see instead of
    # locking an empty string.
    html = built.html if built.html is not None else built.model_dump_json(exclude_none=True)
    if locale is Locale.AR and name not in NEVER_RTL:
        assert built.is_rtl is True
    elif locale is Locale.EN:
        assert built.is_rtl is False
    # No raw engine output may appear in any golden snapshot.
    for token in RAW_TOKENS:
        assert token not in html, f"raw token {token!r} in {name}/{locale}"
    for marker in RAW_JSON_MARKERS:
        assert marker not in html, f"raw JSON marker {marker!r} in {name}/{locale}"

    expected_file = EXPECTED / f"{name}.{locale.value}.html"
    if UPDATE or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(html, encoding="utf-8")
        pytest.skip(f"wrote golden {expected_file.name}")
    assert html == expected_file.read_text(encoding="utf-8"), (
        f"{name}/{locale} differs from golden; run UPDATE_GOLDEN=1 to refresh"
    )


def test_arabic_goldens_contain_arabic():
    ar_files = list(EXPECTED.glob("*.ar.html"))
    if not ar_files:
        pytest.skip("no goldens yet")
    for path in ar_files:
        text = path.read_text(encoding="utf-8")
        assert any("\u0600" <= ch <= "\u06ff" for ch in text), f"{path.name} has no Arabic"


# -- developer-tools screens (TASK 24) -----------------------------------------
#: The dev screens are rendered from *live* sandbox data by ``/dev``; for the
#: goldens we use fixed builders (``screens/dev_goldens.py``) so the snapshot is
#: deterministic. They are locked here exactly like every other screen.
def _dev_builders():
    from cloudbot.presentation.screens import dev_goldens

    return dev_goldens.SCREEN_BUILDERS


@pytest.mark.parametrize("name", sorted(_dev_builders()))
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_golden_dev_screen(name, locale):
    screen = _dev_builders()[name](locale)
    html = screen.doc.html
    built = screen.doc.build()
    if locale is Locale.AR:
        assert built.is_rtl is True
    else:
        assert built.is_rtl is False
    for token in RAW_TOKENS:
        assert token not in html, f"raw token {token!r} in {name}/{locale}"
    for marker in RAW_JSON_MARKERS:
        assert marker not in html, f"raw JSON marker {marker!r} in {name}/{locale}"

    expected_file = EXPECTED / f"{name}.{locale.value}.html"
    if UPDATE or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(html, encoding="utf-8")
        pytest.skip(f"wrote golden {expected_file.name}")
    assert html == expected_file.read_text(encoding="utf-8"), (
        f"{name}/{locale} differs from golden; run UPDATE_GOLDEN=1 to refresh"
    )


# -- keyboard goldens (TASK 09) ----------------------------------------------
#: The top-level screens whose inline navigation block is layout-load-bearing.
KB_AFFECTED = {
    "menu",
    "menu_empty",
    "help",
    "settings",
    "ws_list",
    "ws_list_empty",
    "ws_list_inside",
    "menu_inside",
    "language_picker",
    # Task 13: while inside, the nav block is the session's (ws:home + ws:list);
    # the account destinations are gone. Lock the inside variants of the screens
    # that are reachable while inside so the rule is visible in the goldens.
    "help_inside",
    "settings_inside",
    "language_picker_inside",
    # Task 10: the workspace home carries the log-out row in every status branch.
    # Task 11: it lost the redundant bottom Back row and ends in the shared nav block.
    "ws_detail",
    "ws_detail_suspended",
    "ws_detail_locked",
    # The admin's entered screen reuses the user workspace home (session=False), so
    # its keyboard changed with it; lock it here too.
    "admin_entered",
    # Task 16/17: the sticky terminal view (typing=True) is the changed keyboard —
    # it keeps every terminal button and adds the one-tap "stop typing" exit (R2/R3).
    "terminal_view_typing",
    # TASK package-return: the install result's keyboard is layout-load-bearing —
    # back-to-origin first, retry only on failure, and the no-origin fallback.
    "package_install_ok",
    "package_install_failed_no_net",
    "package_install_ok_no_origin",
}


def _keyboard_text(screen) -> str:
    """A stable, human-readable dump of a screen's keyboard (texts, callbacks,
    disabled flags). Rows are one per line; a reply keyboard is spelled out so its
    reappearance is a visible golden diff."""
    markup = screen.markup
    if markup is None:
        return "<no keyboard>\n"
    inline_rows = getattr(markup, "inline_keyboard", None)
    if inline_rows is None:
        return f"REPLY_KEYBOARD {markup!r}\n"
    lines = []
    for row in inline_rows:
        cells = []
        for button in row:
            action = button.callback_data or button.url or ""
            flag = "disabled" if getattr(button, "disabled", None) is not None else "enabled"
            cells.append(f"{button.text} [{action}] {flag}")
        lines.append(" | ".join(cells))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("name", sorted(KB_AFFECTED))
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_golden_keyboard(name, locale):
    CallbackCodec()
    screen = screen_catalog.build(name, locale)
    text = _keyboard_text(screen)
    assert "REPLY_KEYBOARD" not in text, f"{name}/{locale} returned a reply keyboard"
    expected_file = EXPECTED / f"{name}.{locale.value}.keyboard.txt"
    if UPDATE or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(text, encoding="utf-8")
        pytest.skip(f"wrote keyboard golden {expected_file.name}")
    assert text == expected_file.read_text(encoding="utf-8"), (
        f"{name}/{locale} keyboard differs from golden; run UPDATE_GOLDEN=1 to refresh"
    )
