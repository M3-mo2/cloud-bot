"""Read the source tree for the static checks: imports, literals, code shapes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: The Python areas of the product package.
AREAS = (
    "core",
    "domain",
    "services",
    "presentation",
    "adapters",
    "handlers",
    "middleware",
    "bootstrap",
    "states",
)

@dataclass(frozen=True)
class Module:
    path: Path
    relative: str
    area: str
    tree: ast.Module

    @property
    def source(self) -> str:
        return self.path.read_text(encoding="utf-8")

class SourceTree:
    """Every module of one package root, parsed once and inspected by checks."""

    def __init__(self, root: Path, package: str = "cloudbot") -> None:
        self.root = Path(root)
        self.package = package
        self.package_root = self.root / package
        self.modules: list[Module] = []
        self._load()

    def _load(self) -> None:
        if not self.package_root.exists():
            return
        for path in sorted(self.package_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            relative = path.relative_to(self.root).as_posix()
            area = self._area(relative)
            self.modules.append(Module(path=path, relative=relative, area=area, tree=tree))

    def _area(self, relative: str) -> str:
        parts = relative.split("/")
        return parts[1] if len(parts) > 2 and parts[0] == self.package else ""

    def in_area(self, area: str) -> list[Module]:
        return [m for m in self.modules if m.area == area]

    def global_constants(self) -> dict[str, str]:
        """Every module-level string constant across the package, chained resolved.

        ``ADMIN_ACTION_PREFIX = admin_cb.ADMIN_PREFIX`` is resolved through
        ``ADMIN_PREFIX = "adm"`` so a handler's ``f"{ADMIN_ACTION_PREFIX}:"`` route
        is recognised even though the literal lives in another module.
        """
        assignments: list[tuple[str, ast.AST]] = []
        for module in self.modules:
            for node in module.tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                    if isinstance(target, ast.Name):
                        assignments.append((target.id, node.value))
        out: dict[str, str] = {}
        for _ in range(6):
            changed = False
            for name, value in assignments:
                if name in out:
                    continue
                resolved = self._resolve_constant(value, out)
                if resolved is not None:
                    out[name] = resolved
                    changed = True
            if not changed:
                break
        # Final attribute names win for dotted references (``admin_cb.ADMIN_PREFIX``).
        aliases = {
            node.attr: out.get(node.attr)
            for module in self.modules
            for node in ast.walk(module.tree)
            if isinstance(node, ast.Attribute)
        }
        for name, value in aliases.items():
            if value is not None and name not in out:
                out[name] = value
        return out

    @staticmethod
    def _resolve_constant(node: ast.AST, known: dict[str, str]) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return known.get(node.id)
        if isinstance(node, ast.Attribute):
            return known.get(node.attr)
        return None

    def module_named(self, relative: str) -> Module | None:
        return next((m for m in self.modules if m.relative == relative), None)

    # -- import helpers ------------------------------------------------------
    @staticmethod
    def imported_names(tree: ast.Module) -> list[tuple[str, int]]:
        """``(dotted name, lineno)`` for every import, including ``from`` aliases."""
        found: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.append((node.module, node.lineno))
                    for alias in node.names:
                        found.append((f"{node.module}.{alias.name}", node.lineno))
        return found

    def imports_matching(self, area: str, prefixes: tuple[str, ...]) -> list[tuple[Module, str, int]]:
        hits: list[tuple[Module, str, int]] = []
        for module in self.in_area(area):
            for name, lineno in self.imported_names(module.tree):
                if any(name == p or name.startswith(p + ".") for p in prefixes):
                    hits.append((module, name, lineno))
        return hits

    # -- literal helpers -----------------------------------------------------
    @staticmethod
    def string_literals(tree: ast.Module) -> list[tuple[str, int]]:
        """Every non-docstring string literal (docs and comments are not UI text)."""
        docstrings = SourceTree._docstring_nodes(tree)
        out: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                out.append((node.value, getattr(node, "lineno", 0)))
        return out

    @staticmethod
    def _docstring_nodes(tree: ast.Module) -> set[int]:
        found: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    if isinstance(body[0].value.value, str):
                        found.add(id(body[0].value))
        return found

__all__ = ["AREAS", "Module", "SourceTree"]
