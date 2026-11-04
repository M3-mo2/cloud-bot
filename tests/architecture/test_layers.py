"""Architecture tests: the import rule cannot rot.

Dependencies point inward. Inner layers never import outer layers. This is
enforced by walking every module's imports with ``ast``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "cloudbot"

# Forbidden (module-area, imported-package-prefix) pairs.
FORBIDDEN: list[tuple[str, tuple[str, ...]]] = [
    ("core", ("cloudbot.domain", "cloudbot.services", "cloudbot.presentation",
              "cloudbot.adapters", "cloudbot.handlers", "aiogram", "httpx", "aiosqlite")),
    ("domain", ("cloudbot.services", "cloudbot.presentation", "cloudbot.adapters",
                "cloudbot.handlers", "aiogram", "httpx", "aiosqlite")),
    ("services", ("cloudbot.presentation", "cloudbot.adapters", "cloudbot.handlers",
                  "aiogram", "httpx", "aiosqlite")),
    ("presentation", ("cloudbot.adapters", "cloudbot.handlers", "httpx", "aiosqlite")),
]


def _iter_modules(area: str):
    base = ROOT / area
    for path in base.rglob("*.py"):
        yield path


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


@pytest.mark.parametrize("area,forbidden", FORBIDDEN)
def test_forbidden_imports(area, forbidden):
    violations = []
    for path in _iter_modules(area):
        for name in _imported_names(path):
            for forbidden_prefix in forbidden:
                if name == forbidden_prefix or name.startswith(forbidden_prefix + "."):
                    violations.append(f"{path.relative_to(ROOT)} imports {name}")
    assert not violations, "\n".join(violations)


def test_only_sender_touches_telegram_send_methods():
    """No module outside presentation/rich/sender.py calls a Bot.send_*/edit_*."""
    banned = (
        "send_rich_message",
        "send_rich_message_draft",
        "edit_message_text",
        "send_message",
        "send_photo",
    )
    allowed = (ROOT / "presentation" / "rich" / "sender.py").resolve()
    violations = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if f".{token}(" in text or f"bot.{token}" in text:
                # Handlers use sender.*, not bot.*; flag only genuine Bot calls.
                if "bot." + token in text or "_bot." + token in text:
                    violations.append(f"{path.relative_to(ROOT)} calls {token}")
    assert not violations, "\n".join(violations)


def test_no_send_message_with_parse_mode_anywhere():
    """The rich-only rule: never send_message(parse_mode=...)."""
    violations = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "send_message(" in text and "parse_mode" in text:
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, "\n".join(violations)


def test_domain_has_no_httpx_or_aiogram():
    for path in _iter_modules("domain"):
        names = _imported_names(path)
        assert not any(n == "httpx" or n.startswith("httpx") for n in names), path
        assert not any(n == "aiogram" or n.startswith("aiogram") for n in names), path


def test_core_is_dependency_free():
    for path in _iter_modules("core"):
        names = _imported_names(path)
        inner = [n for n in names if n.startswith("cloudbot.")]
        assert all(n.startswith("cloudbot.core.") for n in inner), (path, inner)


#: The files allowed to look at ``is_admin`` — the identity gate (the one seam)
#: and the machinery that carries the flag / renders DEBUG-only raw payloads.
_ADMIN_CHECK_ALLOWED = {
    "middleware/admin_gate.py",
    "middleware/context_mw.py",
    "middleware/context.py",
    "middleware/mode_gate.py",
    "middleware/error_guard.py",
    "core/config.py",
    "presentation/rich/components.py",
    "presentation/mapping/errors.py",
    # Pre-existing DEBUG+admin raw-payload path (Phase 1), not an admin gate.
    "presentation/screens/packages.py",
    "handlers/features.py",
}


def _uses_is_admin(path: Path) -> bool:
    """True when the *code* (not a comment/docstring) reads ``is_admin``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "is_admin":
            return True
        if isinstance(node, ast.Name) and node.id == "is_admin":
            return True
        if isinstance(node, ast.keyword) and node.arg == "is_admin":
            return True
    return False


def test_the_admin_gate_is_the_only_identity_seam():
    """The gate is one seam: no handler re-checks ``is_admin``. A new admin action
    must rely on the router/middleware gate, never repeat the check."""
    found = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if _uses_is_admin(path)
    }
    unexpected = found - _ADMIN_CHECK_ALLOWED
    assert not unexpected, (
        "is_admin used outside the allowed seam files; admin identity must be "
        "checked only in middleware/admin_gate.py — found in: " + sorted(unexpected).__repr__()
    )


def test_no_handler_re_checks_admin():
    assert not _uses_is_admin(ROOT / "handlers" / "admin_panel.py")


def test_admin_ownership_exemption_is_one_named_method():
    """The admin reads other users' data by design; that exemption lives in exactly
    one method and is called from exactly one service."""
    definers = []
    callers = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "def assert_admin_override" in text:
            definers.append(str(path.relative_to(ROOT)))
        if "assert_admin_override(" in text and "def assert_admin_override" not in text:
            callers.append(str(path.relative_to(ROOT)))
    assert definers == ["services/ownership.py"]
    assert callers == ["services/admin.py"]

