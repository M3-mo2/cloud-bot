"""The doctor's checks: pure functions over the source tree, catalogues and screens.

Every check returns ``Finding`` objects; none of them raises on a broken tree.
The functions take their inputs explicitly (a ``SourceTree``, catalogue dicts, a
builder map) so a test can prove each detector with a tiny synthetic package and
never depend on today's real counts.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from cloudbot.devtools import screens as screen_tools
from cloudbot.devtools.model import Finding, Severity
from cloudbot.devtools.project import SourceTree

#: Catalog call names whose first argument is a key.
_KEY_CALLS = frozenset({"t", "has", "plural", "format_count", "pluralize"})

#: A plausible catalog key: lowercase dotted segments.
_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")

#: Arabic script range, for "hard-coded user-facing text".
_ARABIC = re.compile(r"[\u0600-\u06FF]")

#: Placeholder fields in a catalog template.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# -- helpers -----------------------------------------------------------------
def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""

def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and all(
        isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values
    ):
        return "".join(v.value for v in node.values)  # type: ignore[union-attr]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _const_str(node.left)
        right = _const_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None

def _literal_prefix(node: ast.AST) -> str | None:
    """The constant prefix of a dynamic key expression (``f"a.{x}"`` → ``"a."``)."""
    if isinstance(node, ast.JoinedStr):
        out = ""
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out += value.value
            else:
                break
        return out or None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _const_str(node.left)
        if left is not None:
            return left
        return _literal_prefix(node.left)
    return None

def _is_fstring_with_dynamic(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) and not all(
        isinstance(v, ast.Constant) for v in node.values
    )

@dataclass(frozen=True)
class Reference:
    key: str
    lineno: int
    kwargs: frozenset[str]

def catalog_references(tree: ast.Module) -> tuple[set[str], set[str], list[Reference]]:
    """Return ``(literal keys, dynamic prefixes, call references)`` for one module.

    ``prefixes`` includes every dynamic key expression anywhere in the module
    (``f"plan.{code}"`` → ``"plan."``), not only a catalog call's first argument:
    a key namespace built from data is still referenced.
    """
    keys: set[str] = set()
    prefixes: set[str] = set()
    refs: list[Reference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            prefix = _literal_prefix(node)
            if prefix and (prefix.endswith(".") or prefix.endswith("_") or prefix.endswith(":")):
                prefixes.add(prefix)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _call_name(node) not in _KEY_CALLS:
            continue
        first = node.args[0]
        if _is_fstring_with_dynamic(first):
            prefix = _literal_prefix(first)
            if prefix:
                prefixes.add(prefix)
            continue
        const = _const_str(first)
        if const is None:
            prefix = _literal_prefix(first)
            if prefix:
                prefixes.add(prefix)
            continue
        kwargs = frozenset(kw.arg for kw in node.keywords if kw.arg)
        keys.add(const)
        refs.append(Reference(key=const, lineno=getattr(node, "lineno", 0), kwargs=kwargs))
    return keys, prefixes, refs

def _module_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                value = _const_str(node.value)
                if value is not None:
                    out[target.id] = value
    return out

# -- 1. import direction -----------------------------------------------------
def check_import_direction(tree: SourceTree) -> list[Finding]:
    # ``cloudbot.adapters`` is a genuine inversion (presentation must not know
    # I/O). ``aiogram`` is reported too, but as a warning: the docs *require*
    # presentation to own rich HTML and keyboards, and the only rich-message and
    # keyboard types are aiogram's (``InputRichMessage``, ``InlineKeyboardButton``).
    # That is a documentation contradiction to raise, not a silent pass.
    findings = []
    for area, severity in (
        (("cloudbot.adapters", "cloudbot.infrastructure"), Severity.ERROR),
        (("aiogram",), Severity.WARNING),
    ):
        grouped: dict[tuple[str, int], list[str]] = {}
        for module, name, lineno in tree.imports_matching("presentation", area):
            grouped.setdefault((module.relative, lineno), []).append(name)
        for (relative, lineno), names in sorted(grouped.items()):
            findings.append(
                Finding(
                    check="layer.import_direction",
                    severity=severity,
                    message=f"presentation imports {', '.join(sorted(set(names)))}",
                    location=f"{relative}:{lineno}",
                )
            )
    return findings

# -- 2. handlers must not build screens inline -------------------------------
def check_handlers_inline_screens(tree: SourceTree) -> list[Finding]:
    findings = []
    for module in tree.in_area("handlers"):
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in ("RichDoc", "Screen"):
                    findings.append(
                        Finding(
                            check="layer.handler_builds_screen",
                            severity=Severity.WARNING,
                            message=f"handler assembles a screen inline ({name})",
                            location=f"{module.relative}:{getattr(node, 'lineno', 0)}",
                        )
                    )
    return findings

# -- 3. domain purity --------------------------------------------------------
_DOMAIN_FORBIDDEN_IMPORTS = ("aiogram", "httpx", "aiosqlite", "sqlite3", "socket", "requests")
_DOMAIN_FORBIDDEN_TEXT = (
    ("os.environ", "reads the environment"),
    ("os.getenv", "reads the environment"),
    ("SELECT ", "contains SQL"),
    ("INSERT ", "contains SQL"),
    ("UPDATE ", "contains SQL"),
    ("DELETE ", "contains SQL"),
    (".execute(", "executes SQL"),
)

def check_domain_purity(tree: SourceTree) -> list[Finding]:
    findings = []
    for module in tree.in_area("domain"):
        for name, lineno in tree.imported_names(module.tree):
            if any(name == p or name.startswith(p + ".") for p in _DOMAIN_FORBIDDEN_IMPORTS):
                findings.append(
                    Finding(
                        check="layer.domain_impure",
                        severity=Severity.ERROR,
                        message=f"domain imports {name}",
                        location=f"{module.relative}:{lineno}",
                    )
                )
        source = module.source
        for token, what in _DOMAIN_FORBIDDEN_TEXT:
            if token in source:
                findings.append(
                    Finding(
                        check="layer.domain_impure",
                        severity=Severity.ERROR,
                        message=f"domain {what} ({token.strip()})",
                        location=module.relative,
                    )
                )
    return findings

# -- 4. user-facing text outside the presentation layer ----------------------
def check_hardcoded_text(tree: SourceTree) -> list[Finding]:
    findings = []
    for module in tree.modules:
        if module.area == "presentation" or not module.area:
            continue
        if "i18n/catalogs" in module.relative or "devtools/" in module.relative:
            continue
        for value, lineno in tree.string_literals(module.tree):
            if not value.strip() or len(value) < 3:
                continue
            if _ARABIC.search(value):
                if _SELF_SCRIPT_LABEL in module.source:
                    continue  # a language shown in its own script (see note below)
                findings.append(
                    Finding(
                        check="layer.hardcoded_text",
                        severity=Severity.WARNING,
                        message=f"hard-coded Arabic text {value[:48]!r}",
                        location=f"{module.relative}:{lineno}",
                    )
                )
            elif module.area in ("handlers", "services") and _looks_like_sentence(value):
                findings.append(
                    Finding(
                        check="layer.hardcoded_text",
                        severity=Severity.WARNING,
                        message=f"hard-coded English sentence {value[:56]!r}",
                        location=f"{module.relative}:{lineno}",
                    )
                )
    return findings

#: A language written in its own script is data about the language, not UI copy
#: translated per locale (``LOCALE_LABELS``: "العربية" is Arabic in every locale).
_SELF_SCRIPT_LABEL = "Each language shown in its own script"

def _looks_like_sentence(value: str) -> bool:
    """A human sentence, not a key, identifier, format, path or log event."""
    if " " not in value or len(value) < 16:
        return False
    if value.startswith(("/", "http", "{", "SELECT", "INSERT", "UPDATE", "DELETE")):
        return False
    if _KEY_SHAPE.match(value):
        return False
    words = value.split()
    if len(words) < 3:
        return False
    # A log event name has no spaces; a format string is mostly placeholders.
    if value.count("%s") or value.count("{}"):
        return False
    return value[0].isupper() and value.rstrip()[-1:] in ".!?…"

def check_engine_codes_reach_screens(tree: SourceTree) -> list[Finding]:
    """A screen must never interpolate the engine's own diagnostic/reason text."""
    findings = []
    for module in tree.in_area("presentation"):
        if "/screens/" not in module.relative:
            continue
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            text = ast.get_source_segment(module.source, node) or ""
            if ".diagnostic" in text:
                findings.append(
                    Finding(
                        check="presentation.raw_engine_text",
                        severity=Severity.ERROR,
                        message="a screen interpolates the engine diagnostic",
                        location=f"{module.relative}:{getattr(node, 'lineno', 0)}",
                    )
                )
    return findings

# -- i18n --------------------------------------------------------------------
def check_missing_keys(
    en: Mapping[str, str],
    ar: Mapping[str, str],
    referenced: Iterable[str],
    known_extra: Iterable[str] = (),
) -> list[Finding]:
    findings = []
    known = set(en) | set(ar) | set(known_extra)
    for key in sorted(set(referenced) - known):
        if not _KEY_SHAPE.match(key):
            continue
        findings.append(
            Finding(
                check="i18n.missing_key",
                severity=Severity.ERROR,
                message=f"key {key!r} is referenced but exists in neither catalog",
                location="catalog",
            )
        )
    return findings

def check_key_sets(en: Mapping[str, str], ar: Mapping[str, str]) -> list[Finding]:
    findings = []
    for key in sorted(set(en) - set(ar)):
        findings.append(
            Finding(
                check="i18n.key_set",
                severity=Severity.ERROR,
                message=f"key {key!r} exists in en but not ar",
                location="ar",
            )
        )
    for key in sorted(set(ar) - set(en)):
        findings.append(
            Finding(
                check="i18n.key_set",
                severity=Severity.ERROR,
                message=f"key {key!r} exists in ar but not en",
                location="en",
            )
        )
    return findings

def check_dead_keys(
    en: Mapping[str, str],
    ar: Mapping[str, str],
    referenced: Iterable[str],
    prefixes: Iterable[str] = (),
) -> list[Finding]:
    refs = set(referenced)
    prefix_list = tuple(p for p in prefixes if p)
    findings = []
    for key in sorted(set(en) & set(ar)):
        if key in refs or any(key.startswith(p) for p in prefix_list):
            continue
        findings.append(
            Finding(
                check="i18n.dead_key",
                severity=Severity.INFO,
                message=f"key {key!r} is defined in both catalogs but referenced nowhere",
                location="catalog",
            )
        )
    return findings

def check_placeholder_drift(
    en: Mapping[str, str],
    ar: Mapping[str, str],
    refs: Iterable[Reference] = (),
) -> list[Finding]:
    findings = []
    for key in sorted(set(en) & set(ar)):
        en_fields = set(_PLACEHOLDER.findall(en[key]))
        ar_fields = set(_PLACEHOLDER.findall(ar[key]))
        if en_fields != ar_fields:
            findings.append(
                Finding(
                    check="i18n.placeholder_drift",
                    severity=Severity.WARNING,
                    message=(
                        f"key {key!r} placeholders differ: en={sorted(en_fields)} "
                        f"ar={sorted(ar_fields)}"
                    ),
                    location="catalog",
                )
            )
    for ref in refs:
        if not ref.kwargs or ref.key not in en:
            continue
        expected = set(_PLACEHOLDER.findall(en[ref.key]))
        stray = set(ref.kwargs) - expected
        if stray:
            findings.append(
                Finding(
                    check="i18n.placeholder_drift",
                    severity=Severity.WARNING,
                    message=(
                        f"caller passes unused placeholder(s) {sorted(stray)} to "
                        f"{ref.key!r}"
                    ),
                    location=f"line {ref.lineno}",
                )
            )
    return findings

# -- callbacks and routes ----------------------------------------------------
@dataclass(frozen=True)
class Route:
    kind: str  # "exact" | "prefix"
    value: str
    location: str

def extract_routes(tree: SourceTree) -> list[Route]:
    """Every exact ``F.data ==`` route a callback handler declares."""
    globals_ = tree.global_constants()
    routes: list[Route] = []
    for module in tree.in_area("handlers"):
        constants = {**globals_, **_module_constants(module.tree)}
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for arg in decorator.args:
                    route = _route_of(arg, constants, module.relative)
                    if route is not None:
                        routes.append(route)
    return routes

def _resolve(node: ast.AST, constants: dict[str, str]) -> str | None:
    const = _const_str(node)
    if const is not None:
        return const
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, constants)
        right = _resolve(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        out = ""
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out += value.value
            elif isinstance(value, ast.FormattedValue):
                resolved = _resolve(value.value, constants)
                if resolved is None:
                    return out or None
                out += resolved
            else:
                return out or None
        return out
    return None

def _route_of(node: ast.AST, constants: dict[str, str], location: str) -> Route | None:
    if not isinstance(node, ast.Compare):
        return None
    if not isinstance(node.left, ast.Attribute) or node.left.attr != "data":
        return None
    if len(node.comparators) != 1 or len(node.ops) != 1:
        return None
    right = node.comparators[0]
    if isinstance(node.ops[0], ast.Eq):
        value = _resolve(right, constants)
        return Route("exact", value, location) if value is not None else None
    return None

def _route_of_decorator(node: ast.AST, constants: dict[str, str], location: str) -> Route | None:
    # ``F.data.startswith(...)``, wherever it sits inside the filter call.
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "startswith" and call.args:
            value = _resolve(call.args[0], constants)
            if value is not None:
                return Route("prefix", value, location)
    return None

def extract_all_routes(tree: SourceTree) -> list[Route]:
    globals_ = tree.global_constants()
    routes: list[Route] = []
    for module in tree.in_area("handlers"):
        constants = {**globals_, **_module_constants(module.tree)}
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for arg in decorator.args:
                    exact = _route_of(arg, constants, module.relative)
                    if exact is not None:
                        routes.append(exact)
                    prefix = _route_of_decorator(arg, constants, module.relative)
                    if prefix is not None:
                        routes.append(prefix)
    return routes

def route_claims(routes: Iterable[Route]) -> Callable[[str], bool]:
    route_list = list(routes)

    def claims(data: str) -> bool:
        for route in route_list:
            if route.kind == "exact" and data == route.value:
                return True
            if route.kind == "prefix" and data.startswith(route.value):
                return True
        return False

    return claims

def unreachable_routes(routes: Iterable[Route], emitted: Iterable[str]) -> list[Finding]:
    emitted_list = list(emitted)
    findings = []
    for route in routes:
        if route.kind == "exact":
            hit = route.value in emitted_list
        else:
            hit = any(data.startswith(route.value) for data in emitted_list)
        if not hit:
            findings.append(
                Finding(
                    check="callbacks.unreachable_handler",
                    severity=Severity.WARNING,
                    message=f"no screen emits a callback for route {route.value!r}",
                    location=route.location,
                )
            )
    return findings

# -- screen design rules -----------------------------------------------------
def check_screen_design(
    builders: Mapping[str, Callable[[], Any]], *, locale: Any, tag: str = ""
) -> list[Finding]:
    findings = []
    where = f"{tag}" if tag else getattr(locale, "value", "")

    def location(name: str) -> str:
        return f"{name}[{where}]" if where else name

    for name in sorted(builders):
        try:
            screen = builders[name]()
        except Exception as exc:  # noqa: BLE001 - a broken screen is a finding
            findings.append(
                Finding(
                    check="screens.render_error",
                    severity=Severity.ERROR,
                    message=f"{name} failed to render: {type(exc).__name__}: {exc}",
                    location=location(name),
                )
            )
            continue
        for index in screen_tools.wide_rows(screen):
            findings.append(
                Finding(
                    check="design.row_width",
                    severity=Severity.ERROR,
                    message=f"{name} row {index} has more than two buttons",
                    location=location(name),
                )
            )
        nav_rows = screen_tools.nav_block_rows(screen)
        if nav_rows > 3:
            findings.append(
                Finding(
                    check="design.nav_rows",
                    severity=Severity.ERROR,
                    message=f"{name} nav block has {nav_rows} rows (max 3)",
                    location=location(name),
                )
            )
        width = screen_tools.table_columns(screen.doc.html)
        if width > 4:
            findings.append(
                Finding(
                    check="design.table_width",
                    severity=Severity.ERROR,
                    message=f"{name} table has {width} columns (max 4)",
                    location=location(name),
                )
            )
        if screen_tools.no_exit(screen) and name not in screen_tools.TRANSIENT_SCREENS:
            findings.append(
                Finding(
                    check="design.no_exit",
                    severity=Severity.INFO,
                    message=f"{name} has no button and no way out",
                    location=location(name),
                )
            )
        growing = (
            0
            if name in screen_tools.FIXED_ROW_SCREENS
            else screen_tools.oversized_without_pager(screen)
        )
        if growing:
            findings.append(
                Finding(
                    check="design.no_pager",
                    severity=Severity.ERROR,
                    message=f"{name} renders {growing} rows with no pager",
                    location=location(name),
                )
            )
        for emoji, labels in screen_tools.emoji_duplicates_on_screen(screen):
            findings.append(
                Finding(
                    check="design.emoji_collision",
                    severity=Severity.INFO,
                    message=f"{name} repeats emoji {emoji!r} on {labels}",
                    location=location(name),
                )
            )
    return findings

def check_duplicate_destinations(
    builders: Mapping[str, Callable[[], Any]],
) -> list[Finding]:
    findings = []
    for name in sorted(builders):
        try:
            screen = builders[name]()
        except Exception:  # noqa: BLE001 - already reported by the render check
            continue
        for action in screen_tools.duplicate_destinations(screen):
            findings.append(
                Finding(
                    check="callbacks.duplicate_destination",
                    severity=Severity.ERROR,
                    message=f"{name} names {action!r} on more than one button",
                    location=name,
                )
            )
    return findings

def check_label_conflicts(names: Iterable[str], locale: Any) -> list[Finding]:
    findings = []
    for action, labels in screen_tools.label_conflicts(names, locale).items():
        findings.append(
            Finding(
                check="callbacks.label_conflict",
                severity=Severity.WARNING,
                message=f"destination {action!r} wears several labels: {labels}",
                location="catalog",
            )
        )
    return findings

def check_emoji_meaning(datasets: Iterable[tuple[str, str]]) -> list[Finding]:
    """One emoji meaning three different things across the catalogue."""
    from collections import defaultdict

    meanings: dict[str, set[str]] = defaultdict(set)
    for emoji, label in datasets:
        meanings[emoji].add(label)
    findings = []
    for emoji, labels in sorted(meanings.items()):
        if emoji and len(labels) >= 3:
            findings.append(
                Finding(
                    check="design.emoji_meaning",
                    severity=Severity.INFO,
                    message=f"emoji {emoji!r} means {len(labels)} different things: "
                    f"{sorted(labels)[:6]}",
                    location="catalog",
                )
            )
    return findings

def check_orphan_screens(
    builders: Mapping[str, Callable[..., Any]],
    used_names: Iterable[str],
) -> list[Finding]:
    used = set(used_names)
    findings = []
    for name in sorted(builders):
        if name in used:
            continue
        findings.append(
            Finding(
                check="screens.orphan",
                severity=Severity.INFO,
                message=f"screen {name!r} is reachable from nowhere and used by no handler",
                location=name,
            )
        )
    return findings

def check_text_sizes(
    sizes: Mapping[str, int], *, cap: int = 32768, top: int = 5
) -> list[Finding]:
    findings = []
    for name, size in sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:top]:
        severity = Severity.WARNING if size >= cap * 0.8 else Severity.INFO
        findings.append(
            Finding(
                check="screens.text_size",
                severity=severity,
                message=f"{name} renders {size} chars ({size / cap:.0%} of the cap)",
                location=name,
            )
        )
    return findings

__all__ = [
    "Reference",
    "Route",
    "catalog_references",
    "check_dead_keys",
    "check_domain_purity",
    "check_duplicate_destinations",
    "check_emoji_meaning",
    "check_engine_codes_reach_screens",
    "check_hardcoded_text",
    "check_handlers_inline_screens",
    "check_import_direction",
    "check_key_sets",
    "check_label_conflicts",
    "check_missing_keys",
    "check_orphan_screens",
    "check_placeholder_drift",
    "check_screen_design",
    "check_text_sizes",
    "extract_all_routes",
    "extract_routes",
    "route_claims",
    "unreachable_routes",
]
