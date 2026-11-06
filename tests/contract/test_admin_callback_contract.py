"""Structural contract: no admin screen can produce a callback the panel cannot
handle (Task 07, §3 — the rule that makes a new section safe by construction).

Bug A happened because a producer (a screen f-string) and a consumer (a handler's
``split(":")[n]``) drifted. The codec plus this test make that impossible: every
``adm:`` callback any admin screen emits must parse to a valid action, and that
action must be present in the handler's dispatch table. A handler that splits
callback data by hand is likewise rejected.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from cloudbot.core.locale import Locale
from cloudbot.handlers import admin_panel
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin.callbacks import AdminCallback

ROOT = pathlib.Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "cloudbot" / "handlers"

#: Check every locale so a locale-specific branch cannot smuggle a bad shape.
LOCALES = (Locale.EN, Locale.AR)


def _admin_screen_builders():
    from cloudbot.presentation.catalog import SCREEN_BUILDERS

    return {name: b for name, b in SCREEN_BUILDERS.items() if name.startswith("admin_")}


def _buttons(screen):
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [b for row in rows for b in row]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_admin_screen_callback_parses_and_is_routable(locale):
    builders = _admin_screen_builders()
    assert builders, "no admin screens found — the contract test would be vacuous"
    from cloudbot.presentation.catalog import fixtures

    fixtures = fixtures()
    checked = 0
    for name, build in builders.items():
        screen = build(fixtures, locale)
        for button in _buttons(screen):
            data = getattr(button, "callback_data", None)
            if not data or not data.startswith("adm:"):
                continue
            checked += 1
            parsed = admin_cb.parse(data)
            assert isinstance(parsed, AdminCallback), (
                f"{name}/{locale.value} produced {data!r}, which the codec rejects: {parsed}"
            )
            assert parsed.action in admin_panel._ROUTES, (
                f"{name}/{locale.value} produced {data!r} (action {parsed.action!r}) "
                f"that no handler route can dispatch — a future Bug A"
            )
            # And the handler can read every argument it will need.
            for i in (0, 1, 2):
                if i < len(parsed.args):
                    parsed.args[i]
    assert checked > 0, "no adm: callbacks were checked — the test is vacuous"


def test_the_dispatch_table_covers_every_codec_action():
    """Every action the codec can validate has a route, and vice-versa: the two
    halves of the panel cannot drift apart silently."""
    routes = set(admin_panel._ROUTES)
    expected = {
        admin_cb.MENU,
        admin_cb.SECTION,
        admin_cb.USERS_PAGE,
        admin_cb.USERS_SEARCH,
        admin_cb.USER,
        admin_cb.ASK,
        admin_cb.CANCEL,
        admin_cb.LEAVE,
        admin_cb.NOTIFY_TOGGLE,
        admin_cb.NOTIFY_ALL,
        admin_cb.NOTIFY_PAUSE,
        admin_cb.NOTIFY_SCOPE,
        admin_cb.AUDIT_CAT,
        admin_cb.AUDIT_ALL,
        admin_cb.AUDIT,
        admin_cb.AUDIT_USER,
        admin_cb.WALLET,
        admin_cb.WALLET_OP,
        admin_cb.WALLET_PLAN,
    } | set(admin_cb.ACTION_KEYS)
    assert routes == expected, routes.symmetric_difference(expected)


#: Handlers still allowed to split callback data by hand, each with the reason.
#: The rule (Task 07 §1.1) is that ``adm:`` callback data is parsed only by the
#: admin codec; the language picker is a two-token ``lang:<code>`` and is the sole
#: documented exception.
_HAND_SPLIT_ALLOWED = {
    "cloudbot/handlers/common.py": "lang:<code> is a two-token non-admin callback",
}


def _hand_splits(path: pathlib.Path) -> list[int]:
    """Line numbers where the module calls ``X.data.split(...)`` (AST, not text:
    a comment mentioning the pattern is not a call site)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "split":
            continue
        value = func.value
        if isinstance(value, ast.Attribute) and value.attr == "data":
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize(
    "path",
    sorted(p for p in HANDLERS.rglob("*.py")),
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_handler_splits_admin_callback_data_by_hand(path):
    """``cb.data.split(":")[n]`` is exactly how Bug A was born. The codec is the
    only parser for ``adm:`` data now; the only hand-split left is documented."""
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    offenders = _hand_splits(path)
    if not offenders:
        return
    assert rel in _HAND_SPLIT_ALLOWED, (
        f"{rel}:{offenders} parses callback data by hand; route admin callbacks "
        f"through presentation/screens/admin/callbacks.py"
    )
