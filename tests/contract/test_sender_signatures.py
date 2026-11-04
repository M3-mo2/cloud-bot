"""Contract: every keyword argument the render layer passes to a Bot method must EXIST on that
method, measured against the installed aiogram.

This test exists because a live bot raised

    TypeError: Bot.send_rich_message() got an unexpected keyword argument 'reply_to_message_id'

while the whole suite was green: the unit tests injected a fake bot that accepted anything, so a
wrong parameter name sailed through and only failed in production, on the user's screen.

Method: parse the module with the AST and compare each call's keyword arguments against
`inspect.signature` of the real method. Assertions are made on the parsed TREE, never on raw source
text — a substring check would trip over comments that merely mention the wrong name (which is
exactly what happened on the first attempt at this test).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from aiogram import Bot

RICH_DIR = Path(__file__).resolve().parents[2] / "cloudbot" / "presentation" / "rich"

BOT_METHODS = (
    "send_rich_message",
    "send_rich_message_draft",
    "edit_message_text",
    "send_message",
)


def _calls(path: Path) -> list[tuple[str, list[str], int]]:
    """(method, keyword-argument names, line) for every Bot method call in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in BOT_METHODS:
            found.append(
                (node.func.attr, [k.arg for k in node.keywords if k.arg], node.lineno)
            )
    return found


def _modules() -> list[Path]:
    return sorted(p for p in RICH_DIR.rglob("*.py"))


def test_there_is_something_to_check() -> None:
    """A check that finds no calls would pass vacuously, so assert it found work to do."""
    assert [c for p in _modules() for c in _calls(p)], "no Bot calls found — test is vacuous"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_keyword_arguments_exist_on_the_real_bot_method(path: Path) -> None:
    for method, kwargs, lineno in _calls(path):
        params = set(inspect.signature(getattr(Bot, method)).parameters)
        unknown = [k for k in kwargs if k not in params]
        assert not unknown, (
            f"{path.name}:{lineno} passes {unknown} to Bot.{method}(), which accepts "
            f"{sorted(p for p in params if p != 'self')}"
        )


def test_no_call_uses_reply_to_message_id() -> None:
    """The exact regression, pinned at the call sites (comments are not call sites)."""
    offenders = [
        (p.name, lineno)
        for p in _modules()
        for method, kwargs, lineno in _calls(p)
        if "reply_to_message_id" in kwargs
    ]
    assert not offenders, (
        "reply_to_message_id is not a parameter of the rich send API; use "
        "reply_parameters=ReplyParameters(message_id=...) — found at " + repr(offenders)
    )


def test_a_reply_is_expressed_as_reply_parameters() -> None:
    """The positive half: something in the render layer must actually build ReplyParameters."""
    used = any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ReplyParameters"
        for p in _modules()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
    )
    assert used, "no ReplyParameters construction found — replies would be silently dropped"
