"""Golden snapshots: every screen rendered in both locales, compared byte-for-byte.

Run with ``UPDATE_GOLDEN=1`` to (re)write the expected files. A change in wording
becomes a visible diff. Arabic snapshots must be ``is_rtl=True`` and contain
Arabic text; no snapshot may contain a raw engine token.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloudbot.core.locale import Locale
from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.domain.value_objects import (
    ExecResult,
    FileContent,
    FilePage,
    LifecycleResult,
    Limits,
    RunPage,
    RunStatus,
    SummaryState,
    WorkspaceStatus,
    WorkspaceSummary,
)
from cloudbot.domain.entities import FileEntry, Run
from cloudbot.presentation.mapping import errors as error_mapping
from cloudbot.presentation.screens import files as file_screens
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import run as run_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens.language import language_card, language_picker
from cloudbot.presentation.keyboards.callbacks import CallbackCodec

EXPECTED = Path(__file__).resolve().parent / "expected"
NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

RAW_TOKENS = ("rate_limited", "unprocessable", "workspace_suspended")
RAW_JSON_MARKERS = ('{"error"', '"code":')

#: Screens that are deliberately not RTL even in Arabic (bilingual by design).
NEVER_RTL = {"language_card"}


def _fixtures():
    ws = _ws("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=1)
    suspended = _ws("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED)
    locked = _ws("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED)
    run = Run(
        id="run_01M6HJASCWM9Z1PF755GT89NK4",  # type: ignore[arg-type]
        workspace_id=ws.id,
        kind="command",
        argv=["python3", "main.py"],
        cwd="/",
        status=RunStatus.RUNNING,
        exit_code=None,
        interpreter="python3",
        timeout_sec=None,
        started_at=NOW,
        ended_at=None,
        unlimited=True,
    )
    finished_run = Run(
        id="run_01M6HJASCWM9Z1PF755GT89NK4",  # type: ignore[arg-type]
        workspace_id=ws.id,
        kind="command",
        argv=["python3", "main.py"],
        cwd="/",
        status=RunStatus.EXITED,
        exit_code=0,
        interpreter="python3",
        timeout_sec=300,
        started_at=NOW,
        ended_at=NOW,
        stdout_bytes=312,
        stderr_bytes=0,
    )
    file_page = FilePage(
        path="/",
        entries=(
            FileEntry("/src", "src", "dir", None, "drwxr-xr-x", NOW, None),
            FileEntry("/main.py", "main.py", "file", 812, "-rw-r--r--", NOW, "python3"),
            FileEntry("/README.md", "README.md", "file", 1200, "-rw-r--r--", NOW, None),
        ),
        count=3,
        total=3,
        truncated=False,
    )
    empty_page = FilePage(path="/src", entries=(), count=0, total=0, truncated=False, offset=0, limit=20)
    content = FileContent(
        path="/main.py",
        content='def greet(name):\n    print(f"Hello, {name}")\n',
        encoding="utf-8",
        size=812,
        total_size=812,
        whole_file=True,
        sha256="9f2c4e7a1b3d5f6089a1",
        content_type="text/x-python",
        truncated=False,
        binary=False,
        start_line=1,
        end_line=5,
        total_lines=5,
    )
    binary_content = FileContent(
        path="/archive.zip",
        content="",
        encoding="binary",
        size=4_100_000,
        total_size=4_100_000,
        whole_file=True,
        sha256="",
        content_type="application/zip",
        truncated=False,
        binary=True,
    )
    exec_ok = ExecResult(exit_code=0, stdout="hello from main.py\nargs: ['a', 'b']\n", stderr="", duration_ms=68, truncated=False)
    run_page = RunPage(
        runs=(finished_run,),
        total=12,
        limit=10,
        offset=0,
    )
    return dict(
        ws=ws,
        suspended=suspended,
        locked=locked,
        run=run,
        finished_run=finished_run,
        file_page=file_page,
        empty_page=empty_page,
        content=content,
        binary_content=binary_content,
        exec_ok=exec_ok,
        run_page=run_page,
    )


SCREEN_BUILDERS = {
    "language_card": lambda f, loc: language_card(loc),
    "language_picker": lambda f, loc: language_picker(loc),
    "menu": lambda f, loc: menu_screens.main_menu([f["ws"], f["suspended"]], loc, display_name="sara"),
    "menu_empty": lambda f, loc: menu_screens.main_menu([], loc, display_name="sara"),
    "help": lambda f, loc: menu_screens.help_screen(loc),
    "settings": lambda f, loc: menu_screens.settings_screen(loc),
    "ws_list": lambda f, loc: ws_screens.workspace_list([f["ws"], f["suspended"]], loc),
    "ws_list_empty": lambda f, loc: ws_screens.workspace_list([], loc),
    "ws_detail": lambda f, loc: ws_screens.workspace_detail(f["ws"], loc, now=NOW, active_run=True),
    "ws_detail_suspended": lambda f, loc: ws_screens.workspace_detail(f["suspended"], loc, now=NOW),
    "ws_detail_locked": lambda f, loc: ws_screens.workspace_detail(f["locked"], loc, now=NOW),
    "ws_create_name": lambda f, loc: ws_screens.create_name_prompt(loc),
    "ws_create_profile": lambda f, loc: ws_screens.create_profile([("python", True), ("node", False)], loc, "python"),
    "ws_create_advanced": lambda f, loc: ws_screens.create_advanced(Limits(), Limits(cpu_cores=8, memory_mb=8192, disk_mb=51200), loc, selected_profile="python"),
    "ws_delete_confirm": lambda f, loc: ws_screens.delete_confirm(f["ws"], loc),
    "ws_stop_confirm": lambda f, loc: ws_screens.stop_confirm(f["ws"], loc),
    "ws_suspend_confirm": lambda f, loc: ws_screens.suspend_confirm(f["ws"], loc),
    "file_browser": lambda f, loc: file_screens.file_browser(f["file_page"], f["ws"].id, loc),
    "file_browser_empty": lambda f, loc: file_screens.file_browser(f["empty_page"], f["ws"].id, loc),
    "file_view": lambda f, loc: file_screens.file_view(f["content"], f["ws"].id, loc),
    "file_view_binary": lambda f, loc: file_screens.file_view(f["binary_content"], f["ws"].id, loc),
    "file_mkdir": lambda f, loc: file_screens.mkdir_prompt(f["ws"].id, loc),
    "file_rename": lambda f, loc: file_screens.rename_prompt("/old.txt", f["ws"].id, loc),
    "file_delete_confirm": lambda f, loc: file_screens.delete_confirm("/main.py", f["ws"].id, loc),
    "file_upload": lambda f, loc: file_screens.upload_prompt("/src", 25, f["ws"].id, loc),
    "run_mode": lambda f, loc: run_screens.run_mode(f["ws"], loc, has_files=True),
    "exec_prompt": lambda f, loc: run_screens.exec_prompt(f["ws"].id, loc),
    "exec_result_ok": lambda f, loc: run_screens.exec_result(f["exec_ok"], f["ws"].id, loc),
    "exec_result_fail": lambda f, loc: run_screens.exec_result(ExecResult(1, "", "Traceback\n  File \"/workspace/main.py\", line 3, in <module>\n", 120, False), f["ws"].id, loc),
    "job_prompt": lambda f, loc: run_screens.job_prompt(f["ws"].id, loc),
    "job_started": lambda f, loc: run_screens.run_started(f["run"], loc),
    "live_logs_running": lambda f, loc: run_screens.live_logs(f["run"], loc, stdout="Loading data...\nprocessed 100 records\n"),
    "live_logs_finished": lambda f, loc: run_screens.live_logs(f["finished_run"], loc, stdout="done: 500 records\n"),
    "run_history": lambda f, loc: history_screens.run_history(f["run_page"], f["ws"].id, loc, now=NOW),
    "purge_conflict": lambda f, loc: history_screens.purge_conflict(f["ws"].id, loc),
    "err_unavailable": lambda f, loc: error_mapping.error_code_screen("unavailable", loc),
    "err_workspace_suspended": lambda f, loc: error_mapping.error_code_screen("workspace_suspended", loc),
    "err_unprocessable": lambda f, loc: error_mapping.error_code_screen("unprocessable", loc),
    "err_not_found": lambda f, loc: error_mapping.error_code_screen("not_found", loc),
}


@pytest.mark.parametrize("name", sorted(SCREEN_BUILDERS))
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_golden_screen(name, locale):
    CallbackCodec()  # deterministic content-addressed tokens per render
    screen = SCREEN_BUILDERS[name](_fixtures(), locale)
    html = screen.doc.html
    built = screen.doc.build()
    if locale is Locale.AR and name not in NEVER_RTL:
        assert built.is_rtl is True
    elif locale is Locale.EN:
        assert built.is_rtl is False
    # No raw engine output may appear in any golden snapshot.
    for token in RAW_TOKENS:
        assert token not in html, f"raw token {token!r} in {name}/{locale}"
    for marker in RAW_JSON_MARKERS:
        assert marker not in html, f"raw JSON marker {marker!r} in {name}/{locale}"

    expected_file = EXPECTED / f"{name}.{locale.value}.html"
    if UPDATE or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(html, encoding="utf-8")
        pytest.skip(f"wrote golden {expected_file.name}")
    assert html == expected_file.read_text(encoding="utf-8"), (
        f"{name}/{locale} differs from golden; run UPDATE_GOLDEN=1 to refresh"
    )


def test_arabic_goldens_contain_arabic():
    ar_files = list(EXPECTED.glob("*.ar.html"))
    if not ar_files:
        pytest.skip("no goldens yet")
    for path in ar_files:
        text = path.read_text(encoding="utf-8")
        assert any("\u0600" <= ch <= "\u06ff" for ch in text), f"{path.name} has no Arabic"
