"""Architecture guard: the reply keyboard cannot creep back in.

Structural (AST-based), not a comment. No module anywhere in ``cloudbot/`` may
name ``ReplyKeyboardMarkup`` or ``KeyboardButton``. ``ReplyKeyboardRemove`` is
allowed in exactly one place — ``presentation/keyboards/markup.py``'s
``remove_reply`` factory — the single documented one-time migration off a legacy
persistent reply keyboard (``docs/DECISIONS.md`` CB-25). Anywhere else, a reply
keyboard is a regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "cloudbot"

#: Names that must never appear: a reply markup or a reply button.
FORBIDDEN_NAMES = {"ReplyKeyboardMarkup", "KeyboardButton"}

#: The one file allowed to name ``ReplyKeyboardRemove`` (the factory).
REMOVE_ALLOWED = {"presentation/keyboards/markup.py"}

#: Names that count as naming a reply-keyboard construct (import or use).
_REPLY_REMOVE = "ReplyKeyboardRemove"


def _named_identifiers(path: Path) -> set[str]:
    """Every identifier the module *mentions*: names, attribute names, imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                found.add(node.asname)
    return found


def _scan() -> dict[str, set[str]]:
    hits: dict[str, set[str]] = {}
    for path in ROOT.rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        named = _named_identifiers(path)
        marked = named & (FORBIDDEN_NAMES | {_REPLY_REMOVE})
        if marked:
            hits[rel] = marked
    return hits


def test_no_reply_keyboard_markup_or_button_anywhere():
    offenders = {
        rel: sorted(names & FORBIDDEN_NAMES)
        for rel, names in _scan().items()
        if names & FORBIDDEN_NAMES
    }
    assert not offenders, (
        "a reply keyboard construct reappeared (no reply keyboard is allowed): "
        f"{offenders}"
    )


def test_reply_keyboard_remove_only_in_the_one_documented_path():
    offenders = {
        rel: sorted(names & {_REPLY_REMOVE})
        for rel, names in _scan().items()
        if names & {_REPLY_REMOVE} and rel not in REMOVE_ALLOWED
    }
    assert not offenders, (
        "ReplyKeyboardRemove used outside presentation/keyboards/markup.py "
        f"(the single documented migration path): {offenders}"
    )


def test_the_documented_removal_path_still_exists_and_is_reachable():
    """The one allowed exception must be the real factory, not a stray name."""
    markup = ROOT / "presentation" / "keyboards" / "markup.py"
    tree = ast.parse(markup.read_text(encoding="utf-8"), filename=str(markup))
    factories = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "remove_reply"
    ]
    assert factories == ["remove_reply"], "the remove_reply factory is gone"
