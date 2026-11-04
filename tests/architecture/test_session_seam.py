"""Architecture guard: the workspace session is read only through the service.

The session column (``current_ws_id``) and the repository accessor
(``current_workspace``) are an implementation detail of ``UserRepository`` and
``SqliteUserRepository``, reached only through ``services/session.py``. A handler
that read the column directly would bypass the ownership check and the
self-healing rule, so it is forbidden structurally (AST-based, like the repo's
other seam guards).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "cloudbot"

#: Areas that must never name the session storage detail.
FORBIDDEN_AREAS = ("handlers", "presentation", "middleware")

#: Names that count as reaching for the storage detail directly.
FORBIDDEN_NAMES = {"current_workspace", "set_current_workspace", "current_ws_id"}


def _named_identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


def test_no_handler_reads_the_session_column_directly():
    offenders: dict[str, set[str]] = {}
    for area in FORBIDDEN_AREAS:
        for path in (ROOT / area).rglob("*.py"):
            hit = _named_identifiers(path) & FORBIDDEN_NAMES
            if hit:
                offenders[str(path.relative_to(ROOT))] = hit
    assert not offenders, (
        "the session column is read outside services/session.py; go through "
        f"the SessionService: {offenders}"
    )


def test_the_session_service_is_the_only_owner_of_the_rules():
    """Only ``services/session.py`` calls the repository's session accessors."""
    callers = []
    for path in ROOT.rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if rel in ("services/session.py", "adapters/persistence/sqlite_users.py",
                   "domain/ports/users.py"):
            continue
        named = _named_identifiers(path)
        if named & {"current_workspace", "set_current_workspace"}:
            callers.append(rel)
    assert callers == [], (
        "the session accessors are used outside services/session.py: " + repr(callers)
    )
