"""``python -m cloudbot.devtools.doctor`` — the architecture and design doctor.

It reads the source tree and the screen catalogue and reports problems the test
suite does not: layer breaks, i18n drift, callback routes no screen emits, design
rule violations, orphan screens and oversized bodies.

It is **offline and fast**: it imports the presentation catalogue (to render the
screens) but never a bot, a dispatcher or an engine, and it opens no socket.
Exit code is ``1`` when any finding is at ``error`` severity, so it can be a
pre-commit gate. ``--json`` prints the same report as machine-readable JSON.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from cloudbot.devtools import checks
from cloudbot.devtools.model import Finding, Report, Severity
from cloudbot.devtools.project import SourceTree

#: The repository root (this file is ``<root>/cloudbot/devtools/doctor.py``).
DEFAULT_ROOT = Path(__file__).resolve().parents[2]

def _en_ar():
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.i18n.catalog import CATALOGS

    en = dict(CATALOGS[Locale.EN].MESSAGES)
    ar = dict(CATALOGS[Locale.AR].MESSAGES)
    return en, ar

def _plural_keys():
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.i18n.catalog import plural_keys_for

    return plural_keys_for(Locale.EN) | plural_keys_for(Locale.AR)

def _all_literals(tree: SourceTree) -> set[str]:
    """String literals from product code, excluding the catalogs themselves."""
    out: set[str] = set()
    for module in tree.modules:
        if "i18n/catalogs" in module.relative:
            continue
        for value, _ in tree.string_literals(module.tree):
            out.add(value)
    return out

def _catalog_references(tree: SourceTree):
    keys: set[str] = set()
    prefixes: set[str] = set()
    refs: list[checks.Reference] = []
    for module in tree.modules:
        if module.area == "":
            continue
        module_keys, module_prefixes, module_refs = checks.catalog_references(module.tree)
        keys |= module_keys
        prefixes |= module_prefixes
        for ref in module_refs:
            refs.append(
                checks.Reference(
                    key=ref.key,
                    lineno=ref.lineno,
                    kwargs=ref.kwargs,
                )
            )
    return keys, prefixes, refs

def _builders(locale):
    from cloudbot.presentation import catalog as screen_catalog

    fixtures = screen_catalog.fixtures()
    return {name: (lambda n=name: screen_catalog.build(n, locale, fixtures_data=fixtures)) for name in screen_catalog.screen_names()}

def _emitted_callbacks(builders) -> list[str]:
    emitted: list[str] = []
    for build in builders.values():
        try:
            screen = build()
        except Exception:  # noqa: BLE001 - already reported
            continue
        emitted.extend(checks_callbacks(screen))
    return emitted

def checks_callbacks(screen) -> list[str]:
    from cloudbot.devtools import screens as screen_tools

    return screen_tools.callbacks(screen)

def _builder_targets(builders_source: str) -> dict[str, set[str]]:
    """screen name → the builder functions its lambda calls (best effort)."""
    tree = ast.parse(builders_source)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            targets: set[str] = set()
            for call in ast.walk(value):
                if isinstance(call, ast.Call):
                    func = call.func
                    if isinstance(func, ast.Attribute):
                        targets.add(func.attr)
                    elif isinstance(func, ast.Name):
                        targets.add(func.id)
            out[key.value] = targets
    return out

def _handler_names(tree: SourceTree) -> set[str]:
    names: set[str] = set()
    for module in tree.in_area("handlers"):
        for identity, _ in tree.string_literals(module.tree):
            names.add(identity)
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    names.add(func.attr)
                elif isinstance(func, ast.Name):
                    names.add(func.id)
    return names

def _orphan_findings(root: Path, builders) -> list[Finding]:
    from cloudbot.presentation import catalog as screen_catalog

    catalog_module = screen_catalog.__file__
    if catalog_module is None:
        return []
    source = Path(catalog_module).read_text(encoding="utf-8")
    targets = _builder_targets(source)
    tree = SourceTree(root)
    used = _handler_names(tree)
    findings = []
    for name in sorted(builders):
        names = targets.get(name, {name})
        if names & used or name in used:
            continue
        findings.append(
            Finding(
                check="screens.orphan",
                severity=Severity.INFO,
                message=(
                    f"screen {name!r} is reached only through the catalog/`/dev`, "
                    f"not by a handler"
                ),
                location=name,
            )
        )
    return findings

def run(root: Path | str = DEFAULT_ROOT) -> Report:
    root = Path(root)
    report = Report(root=str(root))
    tree = SourceTree(root)

    en, ar = _en_ar()
    plural = _plural_keys()
    referenced, prefixes, refs = _catalog_references(tree)
    # A key named anywhere in the code — including inside a mapping such as
    # ``ERROR_KEYS`` or a plural table — is referenced; only a key that appears
    # nowhere is dead. (Dynamic ``f"plan.{code}"`` forms are covered by prefixes.)
    literals = _all_literals(tree)
    known = set(en) | set(ar) | set(plural)
    referenced |= literals & known

    # -- layers ------------------------------------------------------------
    report.extend(checks.check_import_direction(tree))
    report.extend(checks.check_handlers_inline_screens(tree))
    report.extend(checks.check_domain_purity(tree))
    report.extend(checks.check_hardcoded_text(tree))
    report.extend(checks.check_engine_codes_reach_screens(tree))

    # -- i18n --------------------------------------------------------------
    report.extend(checks.check_missing_keys(en, ar, referenced | plural, known_extra=plural))
    report.extend(checks.check_key_sets(en, ar))
    report.extend(checks.check_dead_keys(en, ar, referenced | plural, prefixes))
    report.extend(checks.check_placeholder_drift(en, ar, refs))

    # -- callbacks and routes ---------------------------------------------
    from cloudbot.core.locale import Locale

    builders = _builders(Locale.EN)
    routes = checks.extract_all_routes(tree)
    claims = checks.route_claims(routes)
    emitted = _emitted_callbacks(builders)
    for screen_name, build in builders.items():
        try:
            screen = build()
        except Exception:  # noqa: BLE001 - design check reports it
            continue
        for action in checks_callbacks(screen):
            if action and not claims(action):
                report.add(
                    Finding(
                        check="callbacks.no_handler",
                        severity=Severity.ERROR,
                        message=f"{screen_name} emits {action!r}, which no handler routes",
                        location=screen_name,
                    )
                )
    report.extend(checks.unreachable_routes(routes, emitted))
    report.extend(checks.check_duplicate_destinations(builders))

    names = list(builders)
    report.extend(checks.check_label_conflicts(names, Locale.EN))
    report.extend(checks.check_label_conflicts(names, Locale.AR))

    # -- screens -----------------------------------------------------------
    report.extend(checks.check_screen_design(builders, locale=Locale.EN, tag="en"))
    report.extend(checks.check_screen_design(_builders(Locale.AR), locale=Locale.AR, tag="ar"))
    report.extend(_orphan_findings(root, builders))
    report.extend(_emoji_findings(builders))
    report.extend(_text_size_findings(builders))
    return report

def _emoji_findings(builders) -> list[Finding]:
    from cloudbot.devtools import screens as screen_tools

    pairs: list[tuple[str, str]] = []
    for build in builders.values():
        try:
            screen = build()
        except Exception:  # noqa: BLE001
            continue
        for button in screen_tools.buttons(screen):
            label = button.text
            pairs.append((screen_tools.leading_emoji(label), label.split(" ", 1)[-1]))
    return checks.check_emoji_meaning(pairs)

def _text_size_findings(builders) -> list[Finding]:
    sizes: dict[str, int] = {}
    for name, build in builders.items():
        try:
            sizes[name] = len(build().doc.html)
        except Exception:  # noqa: BLE001
            continue
    return checks.check_text_sizes(sizes)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cloudbot.devtools.doctor",
        description="Find architectural and design problems in the tree.",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--root", default=str(DEFAULT_ROOT), help="repository root to inspect"
    )
    args = parser.parse_args(argv)

    report = run(Path(args.root))
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 1 if report.has_errors else 0

if __name__ == "__main__":
    sys.exit(main())
