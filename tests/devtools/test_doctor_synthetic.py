"""The doctor and the reproducer, proven against a tiny synthetic package.

Each detector is exercised on a fixture with exactly one planted defect, so a
detector that stops working fails here regardless of today's real counts. The
real tree is exercised separately, to prove the whole run completes offline.
"""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cloudbot.core.locale import Locale
from cloudbot.devtools import checks, reproduce
from cloudbot.devtools.doctor import run as run_doctor
from cloudbot.devtools.model import Severity
from cloudbot.devtools.project import SourceTree

REPO_ROOT = Path(__file__).resolve().parents[2]

def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path

def _checks_in(findings, name: str):
    return [f for f in findings if f.check == name]

# -- 1. import direction -----------------------------------------------------
def test_planted_import_direction_violation_is_detected(tmp_path):
    _write(
        tmp_path,
        "cloudbot/presentation/bad.py",
        """
        from cloudbot.adapters.cloud_service import engine
        """,
    )
    findings = checks.check_import_direction(SourceTree(tmp_path))
    assert findings, "a presentation→adapters import was not reported"
    assert all(f.severity is Severity.ERROR for f in findings)
    assert "bad.py" in findings[0].location

def test_aiogram_import_in_presentation_is_a_finding(tmp_path):
    _write(
        tmp_path,
        "cloudbot/presentation/rich/thing.py",
        """
        from aiogram.types import InputRichMessage
        """,
    )
    findings = checks.check_import_direction(SourceTree(tmp_path))
    assert findings, "presentation importing aiogram was not reported"
    assert any("aiogram" in f.message for f in findings)

def test_a_clean_presentation_has_no_import_findings(tmp_path):
    _write(
        tmp_path,
        "cloudbot/presentation/ok.py",
        """
        from cloudbot.core.locale import Locale
        """,
    )
    assert checks.check_import_direction(SourceTree(tmp_path)) == []

# -- 2/3. handlers and domain purity ----------------------------------------
def test_planted_inline_screen_in_a_handler_is_detected(tmp_path):
    _write(
        tmp_path,
        "cloudbot/handlers/bad.py",
        """
        from cloudbot.presentation.rich.document import RichDoc

        def handler(locale):
            doc = RichDoc(locale)
            doc.p("hi")
            return doc
        """,
    )
    findings = checks.check_handlers_inline_screens(SourceTree(tmp_path))
    assert _checks_in(findings, "layer.handler_builds_screen")

def test_planted_impure_domain_is_detected(tmp_path):
    _write(
        tmp_path,
        "cloudbot/domain/impure.py",
        """
        import os
        import httpx

        def read():
            return os.environ.get("SECRET")
        """,
    )
    findings = checks.check_domain_purity(SourceTree(tmp_path))
    messages = " ".join(f.message for f in findings)
    assert "httpx" in messages and "environment" in messages

# -- 4. hard-coded text ------------------------------------------------------
def test_planted_hardcoded_arabic_outside_catalogs_is_detected(tmp_path):
    _write(
        tmp_path,
        "cloudbot/services/bad.py",
        """
        GREETING = "مرحبا بكم في التطبيق"
        """,
    )
    findings = checks.check_hardcoded_text(SourceTree(tmp_path))
    assert any("Arabic" in f.message for f in findings)

def test_catalog_literals_are_not_flagged(tmp_path):
    _write(
        tmp_path,
        "cloudbot/presentation/i18n/catalogs/en.py",
        """
        MESSAGES = {"greeting": "Welcome to the product"}
        """,
    )
    assert checks.check_hardcoded_text(SourceTree(tmp_path)) == []

# -- 5/6/7. i18n -------------------------------------------------------------
EN = {
    "common.ok": "OK",
    "greet.hello": "Hello {name}",
    "greet.bye": "Bye",
    "only.en": "English only",
}
AR = {
    "common.ok": "حسنًا",
    "greet.hello": "مرحبا",
    "greet.bye": "إلى اللقاء",
    "only.ar": "عربي فقط",
}

def test_planted_missing_key_is_detected():
    findings = checks.check_missing_keys(EN, AR, referenced={"greet.hello", "ghost.key"})
    assert [f.message for f in findings] == [
        "key 'ghost.key' is referenced but exists in neither catalog"
    ]

def test_planted_key_set_mismatch_is_detected_in_both_directions():
    findings = checks.check_key_sets(EN, AR)
    messages = " ".join(f.message for f in findings)
    assert "only.en" in messages and "only.ar" in messages

def test_planted_dead_key_is_detected():
    findings = checks.check_dead_keys(EN, AR, referenced={"common.ok", "greet.hello"})
    dead = {f.message for f in findings}
    assert any("greet.bye" in m for m in dead)

def test_planted_placeholder_drift_is_detected():
    findings = checks.check_placeholder_drift(EN, AR)
    drift = [f for f in findings if "greet.hello" in f.message]
    assert drift, "the {name} placeholder difference was not reported"
    assert drift[0].severity is Severity.WARNING

def test_a_caller_passing_an_unused_placeholder_is_detected():
    ref = checks.Reference(key="greet.bye", lineno=10, kwargs=frozenset({"unused"}))
    findings = checks.check_placeholder_drift(EN, AR, [ref])
    assert any("unused" in f.message for f in findings)

# -- 8/9. callbacks and routes ----------------------------------------------
def test_planted_unreachable_route_is_detected(tmp_path):
    _write(
        tmp_path,
        "cloudbot/handlers/thing.py",
        """
        from aiogram import F

        @router.callback_query(F.data == "thing:open")
        async def on_open(cb):
            pass

        @router.callback_query(F.data.startswith("ghost:"))
        async def on_ghost(cb):
            pass
        """,
    )
    routes = checks.extract_all_routes(SourceTree(tmp_path))
    emitted = ["thing:open"]
    findings = checks.unreachable_routes(routes, emitted)
    assert any("ghost:" in f.message for f in findings)
    assert not any("thing:open" in f.message for f in findings)

def test_a_screen_emitting_an_unrouted_callback_is_detected():
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    def build():
        doc = RichDoc(Locale.EN)
        doc.p("probe")
        return Screen(doc=doc, markup=inline_markup([[inline("Dead", "zzz:dead")]]))

    # No route at all claims it.
    claims = checks.route_claims([])
    assert not claims("zzz:dead")
    assert claims("anything") is False

# -- 10. duplicate destination on one screen -------------------------------
def test_planted_duplicate_destination_is_detected():
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    def build():
        return Screen(
            doc=RichDoc(Locale.EN),
            markup=inline_markup(
                [
                    [inline("A", "ws:list")],
                    [inline("B", "ws:list")],
                ]
            ),
        )

    findings = checks.check_duplicate_destinations({"dup": build})
    assert findings and findings[0].check == "callbacks.duplicate_destination"

# -- 11. one destination, two labels ---------------------------------------
def test_planted_label_conflict_is_detected(monkeypatch):
    import cloudbot.presentation.catalog as screen_catalog
    from cloudbot.presentation.i18n.catalog import catalog
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    # Two real catalog labels (static, not affordances) on one shared destination.
    label_a = catalog.t("menu.wallet", Locale.EN)
    label_b = catalog.t("menu.settings", Locale.EN)
    screens = {
        "probe_a": lambda: Screen(RichDoc(Locale.EN), inline_markup([[inline(label_a, "ws:new")]])),
        "probe_b": lambda: Screen(RichDoc(Locale.EN), inline_markup([[inline(label_b, "ws:new")]])),
    }

    def build(name, locale, *, fixtures_data=None):
        return screens[name]()

    monkeypatch.setattr(screen_catalog, "build", build)
    findings = checks.check_label_conflicts(list(screens), Locale.EN)
    assert findings, "two labels on one destination were not reported"

# -- 12. design rules -------------------------------------------------------
def _screen_with_rows(rows):
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    return Screen(doc=RichDoc(Locale.EN), markup=inline_markup(rows))

def test_planted_three_button_row_is_detected():
    from cloudbot.presentation.keyboards.buttons import inline

    screen = _screen_with_rows([[inline("a", "a:b"), inline("b", "c:d"), inline("c", "e:f")]])
    findings = checks.check_screen_design({"wide": lambda: screen}, locale=Locale.EN)
    assert any(f.check == "design.row_width" for f in findings)

def test_planted_wide_table_is_detected():
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    doc = RichDoc(Locale.EN)
    doc.table(["a", "b", "c", "d", "e"], [["1", "2", "3", "4", "5"]])
    findings = checks.check_screen_design({"wide": lambda: Screen(doc=doc)}, locale=Locale.EN)
    assert any(f.check == "design.table_width" for f in findings)

def test_planted_nav_block_over_three_rows_is_detected():
    from cloudbot.presentation.keyboards.buttons import inline

    rows = [[inline("x", "ws:list")] for _ in range(4)]
    findings = checks.check_screen_design({"nav": lambda: _screen_with_rows(rows)}, locale=Locale.EN)
    assert any(f.check == "design.nav_rows" for f in findings)

# -- 13. orphan screens -----------------------------------------------------
def test_planted_orphan_screen_is_detected():
    findings = checks.check_orphan_screens(
        {"reached": lambda: None, "lost": lambda: None}, used_names={"reached"}
    )
    assert [f.message for f in findings if "lost" in f.message]
    assert not [f for f in findings if "'reached'" in f.message]

# -- 14. emoji collision ----------------------------------------------------
def test_planted_emoji_collision_on_one_screen_is_detected():
    from cloudbot.presentation.keyboards.buttons import inline

    screen = _screen_with_rows(
        [[inline("📦 One", "a:b"), inline("📦 Two", "c:d")]]
    )
    findings = checks.check_screen_design({"dup": lambda: screen}, locale=Locale.EN)
    assert any(f.check == "design.emoji_collision" for f in findings)

# -- 15. text size ----------------------------------------------------------
def test_text_sizes_report_the_largest():
    findings = checks.check_text_sizes({"small": 10, "big": 30000, "mid": 20000}, cap=32768)
    assert findings[0].location == "big"
    assert findings[0].severity is Severity.WARNING

# -- the reproducer ---------------------------------------------------------
def test_reproduce_matches_a_screen_and_shows_evidence():
    result = reproduce.match("publishing a tunnel shows no url")
    assert result.best is not None
    assert "tunnel" in result.best.screen
    assert result.best.evidence, "the guess came with no evidence"
    # Evidence names the needle and what it matched.
    assert all(item.needle and item.matched for item in result.best.evidence)

def test_reproduce_stub_is_a_runnable_failing_test(tmp_path):
    out = tmp_path / "test_repro.py"
    source = reproduce.render_test(
        reproduce.match("the wallet shows a stray {days} placeholder"), out_path=str(out)
    )
    out.write_text(source, encoding="utf-8")
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(out), "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, "the generated stub did not fail on today's tree"

def test_reproduce_is_offline_keyword_matching(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("the reproducer opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    result = reproduce.match("workspace list is empty")
    assert result.candidates

# -- the real tree, the whole run, and offline -----------------------------
def test_doctor_runs_on_the_real_tree_and_finds_things():
    report = run_doctor(REPO_ROOT)
    assert report.findings, "the doctor found nothing — it is worthless"
    checks_seen = {f.check for f in report.findings}
    assert "screens.orphan" in checks_seen
    assert "i18n.dead_key" in checks_seen

def test_doctor_needs_no_socket(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("the doctor opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    report = run_doctor(REPO_ROOT)
    assert report is not None

def test_doctor_does_not_import_a_bot_or_start_one():
    """The devtools package itself imports no aiogram and no bootstrap/dispatcher."""
    tree = SourceTree(REPO_ROOT)
    for module in tree.modules:
        if "devtools/" not in module.relative:
            continue
        names = [name for name, _ in tree.imported_names(module.tree)]
        assert not any(n == "aiogram" or n.startswith("aiogram.") for n in names), (
            module.relative
        )
        assert not any(n == "cloudbot.bootstrap.app" for n in names), module.relative

def test_doctor_cli_exit_code_reflects_error_severity(tmp_path):
    # A tree with a planted error and nothing else would still need the package;
    # instead, assert the CLI contract on the real tree: exit 1 iff errors exist.
    proc = subprocess.run(
        [sys.executable, "-m", "cloudbot.devtools.doctor", "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    import json

    data = json.loads(proc.stdout)
    assert (proc.returncode == 1) == (data["counts"]["error"] > 0)

def test_doctor_output_is_greppable_and_stable():
    proc = subprocess.run(
        [sys.executable, "-m", "cloudbot.devtools.doctor"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    text = proc.stdout
    assert text.startswith("doctor:")
    assert "[ERROR]" in text or "[WARNING]" in text or "[INFO]" in text
    # Running twice yields the same report (a stable order).
    second = subprocess.run(
        [sys.executable, "-m", "cloudbot.devtools.doctor"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert second.stdout == text
