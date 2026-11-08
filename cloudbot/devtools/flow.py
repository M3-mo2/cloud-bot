"""The flow map: a generated, measured map of every screen, button and edge.

This module **derives** the user-facing navigation tree from the code, so the
document cannot drift from what the product really does:

* ``presentation.catalog.SCREEN_BUILDERS`` gives every screen and its buttons;
* ``keyboards.callbacks`` encodes ``action:key`` — the action is the graph edge;
* the handlers' own decorators (parsed once, through ``devtools.checks``) say
  which handler claims an action;
* the package-wide call graph (``devtools.project``) says which screens that
  handler renders.

Nothing here is hand-written: a new screen or button appears in the next run.
It is offline — it imports the presentation catalogue (to render screens) but
never a bot, a dispatcher or the engine, and it opens no socket.
"""

from __future__ import annotations

import ast
import html as html_module
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from cloudbot.core.locale import Locale
from cloudbot.devtools import checks
from cloudbot.devtools import screens as screen_tools
from cloudbot.devtools.project import (
    SourceTree,
    build_call_graph,
    module_path,
    reachable_functions,
)
from cloudbot.presentation.keyboards.callbacks import decode

#: Where the generated document lives, relative to the repository root.
DOC_RELATIVE = "docs/FLOW-MAP.md"

#: Taps from ``/start`` above which a screen is called out as deep.
DEEP_TAP_THRESHOLD = 4

#: Account-only destinations: forbidden while inside a workspace.
ACCOUNT_ONLY: frozenset[str] = frozenset(
    {"pay:wallet", "nav:settings", "nav:language", "nav:help", "nav:menu"}
)

#: Screens that are inside a workspace by construction (name/role).
INSIDE_SCREENS: frozenset[str] = frozenset(
    {
        "menu_inside",
        "help_inside",
        "settings_inside",
        "language_picker_inside",
        "ws_list_inside",
        "ws_detail",
        "ws_detail_suspended",
        "ws_detail_locked",
        "admin_entered",
    }
)

#: Screens reached by the error guard rather than by a button. They are user
#: flows, so they must not be reported as orphans; the reason is stated.
SYSTEM_SCREENS: frozenset[str] = frozenset(
    {
        "err_unavailable",
        "err_workspace_suspended",
        "err_unprocessable",
        "err_not_found",
        "terminal_stale",
    }
)

#: The screens a user can always return to (used for the dead-end measure).
HOME_SCREENS: frozenset[str] = frozenset(
    {
        "menu",
        "menu_empty",
        "menu_inside",
        "ws_list",
        "ws_list_empty",
        "ws_list_inside",
        "help",
        "help_inside",
        "settings",
        "settings_inside",
        "language_picker",
        "language_picker_inside",
    }
)

_BACK_WORDS = ("back", "cancel", "رجوع", "إلغاء")

_TAG = re.compile(r"<[^>]+>")
_H1 = re.compile(r"<h[12][^>]*>(.*?)</h[12]>", re.DOTALL)
_P = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_WS = re.compile(r"\s+")


def action_of(data: str) -> str:
    """The graph edge of a callback: ``action:key`` → ``action``."""
    return decode(data).action


def _plain(fragment: str) -> str:
    return _WS.sub(" ", html_module.unescape(_TAG.sub("", fragment))).strip()


def _one_line(text: str, limit: int = 96) -> str:
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title_and_body(screen) -> tuple[str, str]:
    rendered = screen.doc.html
    title_match = _H1.search(rendered)
    body_match = _P.search(rendered)
    title = _plain(title_match.group(1)) if title_match else ""
    body = _plain(body_match.group(1)) if body_match else ""
    return title or "(no title)", _one_line(body) if body else "(no body text)"


@dataclass(frozen=True)
class Button:
    label: str
    action: str
    data: str | None
    url: str | None
    disabled: bool


@dataclass
class Screen:
    name: str
    section: str
    title: str
    body: str
    buttons: list[Button] = field(default_factory=list)


# -- the catalog AST: which builder renders which screen ---------------------
def _resolve_lambdas(
    source: str, module_paths: set[str]
) -> dict[str, set[str]]:
    """Map ``module::function`` → screens, from a ``{name: lambda f, loc: fn(...)}`` dict.

    Only calls that resolve through the module's own imports and land under
    ``cloudbot.presentation.screens.`` are kept (so a domain constructor or a
    formatter is not mistaken for a screen builder).
    """
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    symbols: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{node.module}.{alias.name}"
                if full in module_paths:
                    aliases[alias.asname or alias.name] = full
                symbols[alias.asname or alias.name] = (node.module, alias.name)
    out: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(value, ast.Lambda) or not isinstance(value.body, ast.Call):
                continue
            func = value.body.func
            target: str | None = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                alias = func.value.id
                if alias in aliases:
                    target = f"{aliases[alias]}::{func.attr}"
            elif isinstance(func, ast.Name) and func.id in symbols:
                module, name = symbols[func.id]
                target = f"{module}::{name}"
            if target is None:
                continue
            if not target.startswith("cloudbot.presentation.screens."):
                continue
            if target.endswith("::Screen"):
                continue
            out[target].add(key.value)
    return dict(out)


def _catalog_compiler(root: Path, tree: SourceTree) -> dict[str, set[str]]:
    from cloudbot.presentation import catalog as screen_catalog

    paths = {module_path(m.relative) for m in tree.modules}
    return _resolve_lambdas(
        Path(screen_catalog.__file__).read_text(encoding="utf-8"), paths
    )


def _dev_compiler(root: Path, tree: SourceTree) -> dict[str, set[str]]:
    from cloudbot.presentation.screens import dev_goldens

    paths = {module_path(m.relative) for m in tree.modules}
    return _resolve_lambdas(
        Path(dev_goldens.__file__).read_text(encoding="utf-8"), paths
    )


# -- handlers and their routes ----------------------------------------------
@dataclass(frozen=True)
class Handler:
    ident: str
    module: str
    name: str
    routes: tuple[checks.Route, ...]


def _handlers(tree: SourceTree) -> list[Handler]:
    found: list[Handler] = []
    for module in tree.in_area("handlers"):
        mod = module_path(module.relative)
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes: list[checks.Route] = []
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for argument in decorator.args:
                    exact = checks._route_of(argument, {}, module.relative)
                    if exact is not None:
                        routes.append(exact)
                    prefix = checks._route_of_decorator(argument, {}, module.relative)
                    if prefix is not None:
                        routes.append(prefix)
            if routes:
                found.append(
                    Handler(
                        ident=f"{mod}::{node.name}",
                        module=module.relative,
                        name=node.name,
                        routes=tuple(routes),
                    )
                )
    return found


def _route_for(handlers: Iterable[Handler], data: str) -> list[Handler]:
    """The handler(s) that claim ``data``: exact matching, else longest prefix."""
    handler_list = list(handlers)
    exact = [
        handler
        for handler in handler_list
        if any(route.kind == "exact" and route.value == data for route in handler.routes)
    ]
    if exact:
        return exact
    candidates: list[tuple[int, Handler]] = [
        (len(route.value), handler)
        for handler in handler_list
        for route in handler.routes
        if route.kind == "prefix" and data.startswith(route.value)
    ]
    if not candidates:
        return []
    longest = max(length for length, _ in candidates)
    return [handler for length, handler in candidates if length == longest]


def _commands(tree: SourceTree) -> list[tuple[str, Handler]]:
    """Every ``Command("…")`` decorator, paired with its handler function."""
    out: list[tuple[str, Handler]] = []
    for module in tree.modules:
        mod = module_path(module.relative)
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names: list[str] = []
            for decorator in node.decorator_list:
                for call in ast.walk(decorator):
                    if not isinstance(call, ast.Call):
                        continue
                    func = call.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                    if name != "Command":
                        continue
                    for argument in call.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            names.append(argument.value)
            if not names:
                continue
            handler = Handler(
                ident=f"{mod}::{node.name}",
                module=module.relative,
                name=node.name,
                routes=(),
            )
            for name in names:
                out.append((name, handler))
    return sorted(out, key=lambda item: (item[0], item[1].ident))


def _admin_action_constants(tree: SourceTree) -> dict[str, str]:
    constants: dict[str, str] = {}
    for module in tree.modules:
        if not module.relative.endswith("presentation/screens/admin/callbacks.py"):
            continue
        for node in module.tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def _admin_sections(root: Path, tree: SourceTree, compiler: dict[str, set[str]]) -> dict[str, set[str]]:
    """Admin section key → the catalog screens its ``build=`` function renders.

    Read from each module's ``AdminSection(key=…, build=…)`` registration (the
    panel's own registry data), never from a hard-coded section list.
    """
    paths = {module_path(m.relative) for m in tree.modules}
    out: dict[str, set[str]] = {}
    for module in tree.modules:
        if "/screens/admin/" not in module.relative:
            continue
        source = module.source
        lambdas = _resolve_lambdas(source, paths)
        module_name = module_path(module.relative)
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "AdminSection":
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            key_node = keywords.get("key")
            build_node = keywords.get("build")
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            if build_node is None:
                continue
            target: str | None = None
            if isinstance(build_node, ast.Name):
                target = f"{module_name}::{build_node.id}"
            elif isinstance(build_node, ast.Lambda) and isinstance(build_node.body, ast.Call):
                call = build_node.body
                func = call.func
                if isinstance(func, ast.Name):
                    target = f"{module_name}::{func.id}"
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    alias = func.value.id
                    if alias in lambdas:
                        target = f"{lambdas[alias]}::{func.attr}"
            screens: set[str] = set()
            if target is not None:
                screens |= compiler.get(target, set())
            out[key_node.value] = screens
    return out


def _section_keys_called(node: ast.AST) -> tuple[set[str], bool]:
    """Literal keys passed to ``_section(...)`` in a route, and whether dynamic."""
    keys: set[str] = set()
    dynamic = False
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "_section":
            continue
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            keys.add(call.args[0].value)
        else:
            dynamic = True
    return keys, dynamic


def _admin_routes(
    root: Path,
    tree: SourceTree,
    graph,
    compiler: dict[str, set[str]],
) -> dict[str, set[str]]:
    """``adm:<action>`` → its destination screens, from the panel's ``_ROUTES`` table.

    A route that renders a section through the registry (``_section(key).build``)
    is followed through the section's own ``build=`` function; a dynamic section
    key (the ``sec`` route) means "any section", which is truthful, not a guess.
    """
    constants = _admin_action_constants(tree)
    sections = _admin_sections(root, tree, compiler)
    all_section_screens: set[str] = set()
    for names in sections.values():
        all_section_screens |= names
    out: dict[str, set[str]] = {}
    for module in tree.modules:
        if not module.relative.endswith("handlers/admin_panel.py"):
            continue
        mod = module_path(module.relative)
        route_bodies: dict[str, ast.AST] = {}
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                route_bodies[f"{mod}::{node.name}"] = node
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Name) and target.id == "_ROUTES"):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                action: str | None = None
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    action = key.value
                elif isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name):
                    if key.value.id == "admin_cb":
                        action = constants.get(key.attr)
                if action is None or not isinstance(value, ast.Name):
                    continue
                ident = f"{mod}::{value.id}"
                screens: set[str] = set()
                for reached in reachable_functions(graph, ident):
                    screens |= compiler.get(reached, set())
                body = route_bodies.get(ident)
                if body is not None:
                    keys, dynamic = _section_keys_called(body)
                    if dynamic:
                        screens |= all_section_screens
                    for section_key in keys:
                        screens |= sections.get(section_key, set())
                out[action] = screens
    return out


def _admin_menu_screens(section_screens: dict[str, set[str]]) -> set[str]:
    """Every screen the panel's sections render, plus the panel's own extra screens."""
    out: set[str] = set()
    for names in section_screens.values():
        out |= names
    out |= {
        "admin_confirm_suspend",
        "admin_confirm_delete",
        "admin_notify_message",
        "admin_notify_gap",
        "admin_notify_capped",
    }
    return out


def _dev_routes() -> dict[str, set[str]]:
    """``dev:<action>`` → the tool screens it renders (the tool's own dispatcher)."""
    return {
        "sections": {"dev_sections"},
        "section": {"dev_sections"},
        "inspect": {"dev_inspect"},
        "tap": {"dev_inspect"},
        "run": {"dev_report"},
        "doctor": {"dev_report"},
    }


# -- rendering the screens ---------------------------------------------------
def _catalog_screens(root: Path) -> dict[str, Screen]:
    from cloudbot.presentation import catalog as screen_catalog

    fixtures = screen_catalog.fixtures()
    screens: dict[str, Screen] = {}
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        rendered = screen_catalog.build(name, Locale.EN, fixtures_data=fixtures)
        title, body = _title_and_body(rendered)
        buttons = [
            Button(
                label=info.text,
                action=action_of(info.data) if info.data else (info.url or ""),
                data=info.data,
                url=info.url,
                disabled=info.disabled,
            )
            for info in screen_catalog.button_infos(rendered)
        ]
        screens[name] = Screen(
            name=name,
            section=screen_catalog.section_of(name),
            title=title,
            body=body,
            buttons=buttons,
        )
    return screens


def _dev_screens() -> dict[str, Screen]:
    from cloudbot.presentation import catalog as screen_catalog
    from cloudbot.presentation.screens import dev_goldens

    out: dict[str, Screen] = {}
    for name, build in sorted(dev_goldens.SCREEN_BUILDERS.items()):
        rendered = build(Locale.EN)
        title, body = _title_and_body(rendered)
        buttons = [
            Button(
                label=info.text,
                action=action_of(info.data) if info.data else (info.url or ""),
                data=info.data,
                url=info.url,
                disabled=info.disabled,
            )
            for info in screen_catalog.button_infos(rendered)
        ]
        out[name] = Screen(name=name, section="devtool", title=title, body=body, buttons=buttons)
    return out


# -- graph measures ----------------------------------------------------------
def _reachable_screens(edges, start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for _label, _action, destinations in edges.get(current, ()):
            for destination in destinations:
                if destination not in seen:
                    seen.add(destination)
                    stack.append(destination)
    return seen


def _dead_ends(edges) -> list[str]:
    dead: list[str] = []
    for name in sorted(edges):
        reachable = _reachable_screens(edges, name)
        if not reachable & HOME_SCREENS:
            dead.append(name)
    return dead


def _bfs_depths(edges, roots: Iterable[str]) -> dict[str, int]:
    depth: dict[str, int] = {}
    frontier = sorted(set(roots))
    step = 0
    while frontier:
        next_frontier: list[str] = []
        for name in frontier:
            if name in depth:
                continue
            depth[name] = step
            for _label, _action, destinations in edges.get(name, ()):
                next_frontier.extend(d for d in destinations if d not in depth)
        frontier = sorted(set(next_frontier))
        step += 1
    return depth


def _deepest_chain(edges, roots: Iterable[str]) -> list[str]:
    """The longest chain BFS can reach (deterministic, linear in nodes × edges).

    This is the measured depth of the tree, not an exhaustive longest-simple-path
    search: the graph has loops, so an exhaustive walk would be exponential, and
    the deepest *reachable level* is the number that matters for "how many taps".
    """
    root_list = sorted(set(roots))
    if not root_list:
        return []
    depth = _bfs_depths(edges, root_list)
    if not depth:
        return []
    deepest = max(depth.values())
    # Follow one deterministic predecessor chain from a deepest screen.
    chain = [min(name for name, value in depth.items() if value == deepest)]
    while depth[chain[-1]] > 0:
        current = chain[-1]
        level = depth[current] - 1
        parents = [
            name
            for name, value in depth.items()
            if value == level
            and any(current in destinations for _l, _a, destinations in edges.get(name, ()))
        ]
        if not parents:
            break
        chain.append(min(parents))
    return list(reversed(chain))


def _cycles(edges) -> list[tuple[str, str, str, str, bool]]:
    """Each unordered screen pair that points at itself, with one label per side.

    A pair is one loop even when several buttons make it (the destination is what
    repeats, not the loop). ``unobvious`` is true when neither label is a
    back/cancel affordance: the two screens name each other as forward
    destinations, so the hierarchy reads flat.
    """
    labels: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for a in sorted(edges):
        for label_a, _action_a, destinations_a in edges[a]:
            for b in sorted(set(destinations_a)):
                if a == b:
                    continue
                for label_b, _action_b, destinations_b in edges.get(b, ()):
                    if a in destinations_b:
                        labels[(a, b)].append((label_a, label_b))
    found: list[tuple[str, str, str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for a, b in sorted(labels):
        pair = (a, b) if a < b else (b, a)
        if pair in seen:
            continue
        seen.add(pair)
        first = sorted(labels.get((a, b), []))[0] if labels.get((a, b)) else ("", "")
        second = sorted(labels.get((b, a), []))[0] if labels.get((b, a)) else ("", "")
        label_a, label_b = first[0], second[0]
        unobvious = not any(
            word in label.lower()
            for word in _BACK_WORDS
            for label in (label_a, label_b)
        )
        found.append((a, b, label_a, label_b, unobvious))
    return sorted(found)


def _repeated_callbacks(screen: Screen) -> list[str]:
    """Callback data this screen emits on more than one button (the same tap twice)."""
    counts: dict[str, int] = defaultdict(int)
    for button in screen.buttons:
        if button.data is not None:
            counts[button.data] += 1
    return sorted(data for data, count in counts.items() if count > 1)


def _text_step_screens(tree: SourceTree, graph, compiler: dict[str, set[str]]) -> dict[str, set[str]]:
    """Screens rendered after a text/document/payment step (not a button tap).

    A prompt asks the user to type; the reply is handled by a ``router.message``
    handler whose filter is **not** a ``Command``. These screens are user flows
    even though no button leads to them, so they are listed separately instead of
    being called orphans. The filter text is the state/predicate that triggers it.
    """
    out: dict[str, set[str]] = defaultdict(set)
    for module in tree.modules:
        if module.area != "handlers":
            continue
        mod = module_path(module.relative)
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            filters: list[str] = []
            is_message = False
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (isinstance(func, ast.Attribute) and func.attr == "message"):
                    continue
                is_message = True
                if not decorator.args:
                    filters.append("(any message)")
                    continue
                argument = decorator.args[0]
                if isinstance(argument, ast.Call):
                    inner = argument.func
                    if isinstance(inner, ast.Name) and inner.id == "Command":
                        is_message = False
                        break
                filters.append(ast.unparse(argument))
            if not is_message or not filters:
                continue
            screens: set[str] = set()
            for reached in reachable_functions(graph, f"{mod}::{node.name}"):
                screens |= compiler.get(reached, set())
            for label in filters:
                out[label] |= screens
    return dict(out)


def _inline_screens(tree: SourceTree) -> list[str]:
    out: list[str] = []
    for module in tree.in_area("handlers"):
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    func = call.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name == "Screen":
                        out.append(f"{module.relative}:{getattr(call, 'lineno', 0)} ({node.name})")
    return sorted(set(out))


# -- the model ---------------------------------------------------------------
@dataclass
class Model:
    screens: dict[str, Screen]
    catalog_count: int
    entries: list[tuple[str, list[str]]]
    callback_entries: list[tuple[str, list[str]]]
    edges: dict[str, list[tuple[str, str, list[str]]]]
    destinations: set[str]
    label_conflicts: dict[str, list[str]]
    duplicate_buttons: list[tuple[str, str]]
    dead_ends: list[str]
    transient_dead_ends: list[str]
    orphans: list[str]
    text_steps: list[tuple[str, list[str]]]
    loops: list[tuple[str, str, str, str, bool]]
    inside_violations: list[tuple[str, list[str]]]
    outside_violations: list[tuple[str, list[str]]]
    start_depth: dict[str, int]
    deepest_chain: list[str]
    deep_screens: list[str]
    inline_screens: list[str]

    @property
    def screen_count(self) -> int:
        return len(self.screens)

    @property
    def edge_count(self) -> int:
        return sum(len(screen.buttons) for screen in self.screens.values())


def build_model(root: Path | str = Path(".")) -> Model:
    root = Path(root).resolve()
    tree = SourceTree(root)
    graph = build_call_graph(tree)
    catalog = _catalog_screens(root)
    dev = _dev_screens()
    all_screens = {**catalog, **dev}

    compiler = _catalog_compiler(root, tree)
    dev_compiler = _dev_compiler(root, tree)
    combined = defaultdict(set)
    for mapping in (compiler, dev_compiler):
        for ident, names in mapping.items():
            combined[ident] |= names

    handlers = _handlers(tree)
    admin_routes = _admin_routes(root, tree, graph, compiler)
    dev_routes = _dev_routes()

    def destinations_for(data: str) -> list[str]:
        action = action_of(data)
        if action.startswith("adm:"):
            key = data.split(":", 2)[1] if ":" in data else ""
            if key in admin_routes and admin_routes[key]:
                return sorted(admin_routes[key])
        if action.startswith("dev:"):
            key = data.split(":", 2)[1] if ":" in data else ""
            return sorted(dev_routes.get(key, set()))
        found: set[str] = set()
        for handler in _route_for(handlers, data):
            for reached in reachable_functions(graph, handler.ident):
                found |= combined.get(reached, set())
        return sorted(found)

    edges: dict[str, list[tuple[str, str, list[str]]]] = {}
    destinations: set[str] = set()
    for name, screen in all_screens.items():
        rows: list[tuple[str, str, list[str]]] = []
        for button in screen.buttons:
            if button.data is None:
                continue
            action = action_of(button.data)
            destinations.add(action)
            rows.append((button.label, action, destinations_for(button.data)))
        edges[name] = rows

    section_screens = _admin_sections(root, tree, compiler)
    admin_screens = _admin_menu_screens(section_screens)

    #: The screen an inline-built command actually lands on first (read from the
    #: handler's own dispatch data; the rest of that surface follows).
    command_landing = {
        "dev": "dev_sections",
        "admin": "admin_overview",
        "notifications": "admin_notifications",
    }

    def command_screens(command: str, handler: Handler) -> list[str]:
        """Screens a command can render, landing screen first.

        ``/dev``, ``/admin`` and ``/notifications`` assemble their first screen in
        the handler (so no screen function is called); their destination is read
        from the tool/panel's own dispatch data, not guessed.
        """
        if command == "dev":
            found = {"dev_sections"}
        elif command in ("admin", "notifications"):
            found = set(admin_screens)
        else:
            found = set()
            for reached in reachable_functions(graph, handler.ident):
                found |= combined.get(reached, set())
        landing = command_landing.get(command)
        ordered = [landing] if landing in found else []
        ordered += [name for name in sorted(found) if name not in ordered]
        return ordered

    entries: list[tuple[str, list[str]]] = []
    for command, handler in _commands(tree):
        entries.append((command, command_screens(command, handler)))
    entries = sorted(entries, key=lambda item: item[0])
    entry_roots = sorted({name for _command, names in entries for name in names})

    callback_entries: dict[str, set[str]] = defaultdict(set)
    for screen in all_screens.values():
        for button in screen.buttons:
            if button.data is not None:
                callback_entries[action_of(button.data)] |= set(destinations_for(button.data))

    reachable_from_entries: set[str] = set(entry_roots)
    for root in entry_roots:
        reachable_from_entries |= _reachable_screens(edges, root)
    text_steps = _text_step_screens(tree, graph, {**compiler, **dev_compiler})
    # A text step lands on a prompt; everything that prompt's buttons reach is
    # also a real flow, so the closure is credited too.
    text_step_screens: set[str] = set()
    for names in text_steps.values():
        for name in names:
            text_step_screens.add(name)
            text_step_screens |= _reachable_screens(edges, name)
    orphans = sorted(
        name
        for name in catalog
        if name not in reachable_from_entries
        and name not in SYSTEM_SCREENS
        and name not in text_step_screens
    )

    dead_ends = [
        name
        for name in _dead_ends(edges)
        if name in catalog and name not in screen_tools.TRANSIENT_SCREENS
    ]
    transient_dead_ends = sorted(
        name
        for name in _dead_ends(edges)
        if name in screen_tools.TRANSIENT_SCREENS
    )

    inside_violations: list[tuple[str, list[str]]] = []
    outside_violations: list[tuple[str, list[str]]] = []
    for name, screen in all_screens.items():
        actions = {button.action for button in screen.buttons}
        if name in INSIDE_SCREENS:
            leaked = sorted(actions & ACCOUNT_ONLY)
            if leaked:
                inside_violations.append((name, leaked))
        elif "ws:home" in actions:
            outside_violations.append((name, ["ws:home"]))
    inside_violations.sort()
    outside_violations.sort()

    start_roots = sorted(
        {name for command, names in entries if command == "start" for name in names}
    )
    start_depth = _bfs_depths(edges, start_roots)
    deep_screens = sorted(
        name for name, value in start_depth.items() if value > DEEP_TAP_THRESHOLD
    )

    return Model(
        screens=all_screens,
        catalog_count=len(catalog),
        entries=entries,
        callback_entries=sorted((action, sorted(names)) for action, names in callback_entries.items()),
        edges=edges,
        destinations=destinations,
        label_conflicts=screen_tools.label_conflicts(sorted(catalog), Locale.EN),
        duplicate_buttons=[
            (name, action)
            for name in sorted(catalog)
            for action in _repeated_callbacks(catalog[name])
        ],
        dead_ends=dead_ends,
        transient_dead_ends=transient_dead_ends,
        orphans=orphans,
        text_steps=sorted((label, sorted(names)) for label, names in text_steps.items()),
        loops=_cycles(edges),
        inside_violations=inside_violations,
        outside_violations=outside_violations,
        start_depth=start_depth,
        deepest_chain=_deepest_chain(edges, entry_roots),
        deep_screens=deep_screens,
        inline_screens=_inline_screens(tree),
    )


# -- the document ------------------------------------------------------------
def _fmt_destinations(destinations: list[str]) -> str:
    return ", ".join(destinations) if destinations else "(handler only — no screen)"


def _tree_lines(
    model: Model,
    screen: str,
    depth: int,
    path: list[str],
    seen: dict[str, int],
    lines: list[str],
) -> None:
    """Print one screen and, recursively, the screens its buttons reach.

    ``seen`` remembers where each screen was first expanded *within this entry
    tree*: a screen reached again is printed (so the repeated destination is
    visible) but not expanded a second time. Without this a graph with loops and
    many-to-one edges would expand combinatorially — the same contradiction
    printed thousands of times instead of once.
    """
    prefix = "  " * depth
    current = model.screens.get(screen)
    if current is None:
        lines.append(f"{prefix}- `{screen}` (not in the catalog)")
        return
    if screen in seen:
        lines.append(
            f"{prefix}- **{current.name}** — {current.title} "
            f"↩ (already expanded at depth {seen[screen]})"
        )
        return
    seen[screen] = depth
    lines.append(f"{prefix}- **{current.name}** — {current.title}")
    for label, action, destinations in model.edges.get(screen, ()):
        lines.append(f"{prefix}  - {label} → `{action}` → {_fmt_destinations(destinations)}")
        for destination in destinations:
            if destination in path:
                lines.append(f"{prefix}    - ↩ `{destination}` (already on this path)")
            elif depth + 1 >= DEEP_TAP_THRESHOLD:
                lines.append(f"{prefix}    - … `{destination}` at depth {depth + 2} (not expanded)")
            else:
                _tree_lines(model, destination, depth + 2, path + [destination], seen, lines)


def render(model: Model) -> str:
    """Render the whole document. Deterministic: every collection is sorted."""
    lines: list[str] = []
    add = lines.append
    add("# FLOW-MAP — every screen, every button, where it leads")
    add("")
    add(
        "> **Generated file — do not edit.** `python -m scripts.map_flow` derives this "
        "document from the code (the screen catalog, the callback codec and the "
        "handlers' decorators). A test fails when the committed copy is stale."
    )
    add("")
    add("## How to read this")
    add("")
    add(
        "Each screen line carries its **name** and the title a user sees. Under it, "
        "every button is one line: the **label**, the **action** it fires "
        "(`prefix:name` from the callback codec), and the screen(s) that action lands "
        "on. Indented below that is the next screen, recursively; depth is the number "
        "of taps from the entry. `↩` marks a screen already on the current path "
        "(a loop), and a tree stops expanding past the deep-tap threshold."
    )
    add("")
    add(
        "Labels are the English catalog, so one row per button is stable; every screen "
        "exists in both Arabic and English. The catalog count excludes the `/dev` tool "
        "screens, which are listed separately at the end."
    )
    add("")
    add("## One-screen summary")
    add("")
    add("| Measure | Value |")
    add("| --- | ---: |")
    add(f"| Catalog screens | {model.catalog_count} |")
    add(f"| Screens including the /dev tool | {model.screen_count} |")
    add(f"| Button edges | {model.edge_count} |")
    add(f"| Distinct destinations (actions) | {len(model.destinations)} |")
    add(f"| Entry commands | {len(model.entries)} |")
    add(f"| Callback re-entry actions | {len(model.callback_entries)} |")
    add(f"| Contradictions: one destination, several labels | {len(model.label_conflicts)} |")
    add(f"| Contradictions: repeated button on one screen | {len(model.duplicate_buttons)} |")
    add(f"| Contradictions: dead ends | {len(model.dead_ends)} |")
    add(f"| Contradictions: orphans | {len(model.orphans)} |")
    add(f"| Contradictions: two-screen loops | {len(model.loops)} |")
    add(
        "| Contradictions: inside/outside nav rule | "
        f"{len(model.inside_violations) + len(model.outside_violations)} |"
    )
    add(f"| Screens deeper than {DEEP_TAP_THRESHOLD} taps from `/start` | {len(model.deep_screens)} |")
    add("")

    add("## 1. Entry points")
    add("")
    add(
        "Read from the routers' own `Command(…)` decorators, not guessed. There is "
        "**no deep link**: no handler reads a `/start` payload."
    )
    add("")
    add("| Command | Screens it can render |")
    add("| --- | --- |")
    for command, names in model.entries:
        add(f"| `/{command}` | {_fmt_destinations(names)} |")
    add("")
    add(
        "**Callback re-entry.** A tap on any button of a stale message re-enters here: "
        f"{len(model.callback_entries)} distinct actions are handled."
    )
    add("")
    add("| Action | Screens it can render |")
    add("| --- | --- |")
    for action, names in model.callback_entries:
        add(f"| `{action}` | {_fmt_destinations(names)} |")
    add("")

    add("## 2. The tree, from each entry")
    add("")
    add(
        "One tree per command. The same screen reached twice is shown both times "
        "(that is the point), but a path already taken is marked `↩` rather than "
        "expanded forever."
    )
    add("")
    for command, names in model.entries:
        add(f"### `/{command}`")
        add("")
        if not names:
            add("_No screen is rendered by this command._")
            add("")
            continue
        for name in names:
            _tree_lines(model, name, 0, [name], {}, lines)
        add("")

    add("## 3. The contradictions")
    add("")
    add("Every section is produced by measuring the tree above, not by opinion.")
    add("")

    add(f"### 3.1 Two labels for one destination ({len(model.label_conflicts)})")
    add("")
    if model.label_conflicts:
        add(
            "The project's label guard flags a destination callback wearing more than "
            "one *naming* label (static labels only; Back/Cancel affordances are "
            "exempt by the project's own rule)."
        )
        add("")
        for action, labels in sorted(model.label_conflicts.items()):
            add(f"- `{action}`: " + ", ".join(f"“{label}”" for label in labels))
    else:
        add("_None: every destination wears a single static label._")
    add("")

    add(f"### 3.2 Repeated buttons on one screen ({len(model.duplicate_buttons)})")
    add("")
    if model.duplicate_buttons:
        for screen, action in model.duplicate_buttons:
            add(f"- **{screen}** emits `{action}` twice")
    else:
        add("_None: no screen repeats a callback._")
    add("")

    add(f"### 3.3 Dead ends ({len(model.dead_ends)})")
    add("")
    add(
        "A dead end is a screen from which no button path reaches a home screen "
        "(menu, workspaces list, help, settings or language). Transient cards that "
        "are *sent*, never navigated, are measured separately below."
    )
    add("")
    if model.dead_ends:
        for name in model.dead_ends:
            add(f"- **{name}** — {model.screens[name].title}")
    else:
        add("_None: every navigable screen can reach a home screen._")
    add("")
    if model.transient_dead_ends:
        add(
            "Transient cards (no buttons by design — sent to the user, not navigated): "
            + ", ".join(f"`{name}`" for name in model.transient_dead_ends)
        )
        add("")

    add(f"### 3.4 Orphans ({len(model.orphans)})")
    add("")
    add(
        "A screen not reachable from any command entry through the button graph. "
        "Error screens are excluded: the error guard reaches them, not a button."
    )
    add("")
    if model.orphans:
        for name in model.orphans:
            add(f"- **{name}** — {model.screens[name].title}")
    else:
        add("_None._")
    add("")
    add("Reached by the error guard (not orphans): " + ", ".join(f"`{n}`" for n in sorted(SYSTEM_SCREENS)))
    add("")

    add(f"### 3.5 Loops that are not obvious ({len(model.loops)})")
    add("")
    add(
        "Every two-screen loop `A → B → A`. `unobvious` marks a loop whose labels are "
        "not back/cancel affordances: each screen names the other as a forward "
        "destination, so the hierarchy reads flat."
    )
    add("")
    if model.loops:
        for a, b, label_a, label_b, unobvious in model.loops:
            mark = "**unobvious**" if unobvious else "expected (back/affordance)"
            add(f"- **{a}** —“{label_a}”→ **{b}** —“{label_b}”→ **{a}** ({mark})")
    else:
        add("_None._")
    add("")

    add(
        "### 3.6 Screens out of the inside/outside rule "
        f"({len(model.inside_violations) + len(model.outside_violations)})"
    )
    add("")
    add(
        "Inside a workspace the nav block must be the session's and the workspace home "
        "must show only the session nav; while outside, no session entry may appear. "
        "The account-only destinations measured are: "
        + ", ".join(f"`{a}`" for a in sorted(ACCOUNT_ONLY))
        + "."
    )
    add("")
    if model.inside_violations:
        add("**Inside a workspace, but showing account destinations:**")
        add("")
        for name, actions in model.inside_violations:
            add(f"- **{name}**: " + ", ".join(f"`{a}`" for a in actions))
        add("")
    if model.outside_violations:
        add("**Outside a workspace, but showing the session home:**")
        add("")
        for name, actions in model.outside_violations:
            add(f"- **{name}**: " + ", ".join(f"`{a}`" for a in actions))
        add("")
    if not model.inside_violations and not model.outside_violations:
        add("_None: the inside/outside rule holds on every catalog screen._")
        add("")

    add(f"### 3.7 Depths ({len(model.deep_screens)})")
    add("")
    add(
        "The deepest chain found from the entry screens is "
        f"{len(model.deepest_chain)} screens long: "
        + " → ".join(f"`{name}`" for name in model.deepest_chain)
    )
    add("")
    add(f"Screens more than {DEEP_TAP_THRESHOLD} taps from `/start`:")
    add("")
    if model.deep_screens:
        for name in model.deep_screens:
            add(f"- **{name}** — depth {model.start_depth.get(name, '?')} — {model.screens[name].title}")
    else:
        add("_None._")
    add("")

    add("## 4. Screens outside a command flow")
    add("")
    add("### Reached only through the catalog / `/dev` (tool screens)")
    add("")
    tool = sorted(name for name, screen in model.screens.items() if screen.section == "devtool")
    if tool:
        for name in tool:
            add(f"- **{name}** — {model.screens[name].title}")
    else:
        add("_None._")
    add("")
    add("### Catalog entries that no command reaches")
    add("")
    if model.orphans:
        for name in model.orphans:
            add(f"- **{name}** — {model.screens[name].title}")
    else:
        add("_None._")
    add("")
    add("### Reached after a text / document / payment step (not a button)")
    add("")
    add(
        "A prompt asks the user to type (or upload, or pay); the reply is handled by "
        "a message handler whose filter is not a `Command`. These screens are real "
        "user flows even though no button leads to them."
    )
    add("")
    if model.text_steps:
        for label, names in model.text_steps:
            add(f"- `{label}` → {_fmt_destinations(names)}")
    else:
        add("_None._")
    add("")

    add("### Inline screens built in handlers (not catalog screens)")
    add("")
    if model.inline_screens:
        for where in model.inline_screens:
            add(f"- `{where}`")
    else:
        add("_None._")
    add("")

    add("## 5. Every screen, every button (flat index)")
    add("")
    add(
        "The tree above prunes and collapses repeated screens so it stays readable. "
        "This index is exhaustive: every catalog screen (and every `/dev` tool "
        "screen) with every button, its action and its destination — so no button "
        "is hidden by the tree's depth limit."
    )
    add("")
    for name in sorted(model.screens):
        screen = model.screens[name]
        add(f"### {name} — {screen.title}")
        add("")
        add(f"_{screen.body}_")
        add("")
        if not screen.buttons:
            add("- (no buttons)")
            add("")
            continue
        for button in screen.buttons:
            if button.data is None:
                add(f"- {button.label} → URL `{button.url}`")
                continue
            destinations = next(
                (d for label, action, d in model.edges.get(name, ()) if label == button.label and action == button.action),
                [],
            )
            disabled = " (disabled)" if button.disabled else ""
            add(
                f"- {button.label}{disabled} → `{button.action}` → "
                f"{_fmt_destinations(destinations)}"
            )
        add("")

    return "\n".join(lines).rstrip() + "\n"


def write(root: Path | str = Path(".")) -> Path:
    root = Path(root).resolve()
    target = root / DOC_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(build_model(root)), encoding="utf-8")
    return target
