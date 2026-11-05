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
    ArchiveEntry,
    ArchiveResult,
    ExecResult,
    FileContent,
    FilePage,
    InstallResult,
    LifecycleResult,
    Limits,
    Package,
    PackageList,
    PortEntry,
    PortReport,
    ProcessEntry,
    ProcessList,
    ProcessStopResult,
    RunPage,
    RunStatus,
    ResourceUsage,
    SummaryState,
    Tunnel,
    TunnelList,
    UnixSocket,
    WorkspaceMetrics,
    WorkspaceStatus,
    WorkspaceSummary,
)
from cloudbot.domain.entities import FileEntry, Run, TerminalSession
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.admin import (
    AdminOverview,
    AdminView,
    AuditPage,
    EngineHealthView,
    EnteredWorkspace,
    UserListPage,
    UserPage,
    UserSummary,
    WorkspaceWithUsage,
)
from cloudbot.domain.events import (
    EventCategory,
    EventRecord,
    NotificationMessage,
    Severity,
)
from cloudbot.presentation.mapping import errors as error_mapping
from cloudbot.presentation.screens.admin import audit as admin_audit
from cloudbot.presentation.screens.admin import notifications as admin_notify
from cloudbot.presentation.screens.admin import overview as admin_overview
from cloudbot.presentation.screens.admin import user_page as admin_user
from cloudbot.presentation.screens.admin import users as admin_users
from cloudbot.presentation.screens.admin import workspace as admin_workspace
from cloudbot.presentation.screens.admin.actions import confirmation_screen, action
from cloudbot.presentation.screens.admin.notification_message import (
    gap_message,
    notification_message,
    suppression_message,
)
from cloudbot.presentation.screens import archives as arc_screens
from cloudbot.presentation.screens import files as file_screens
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import metrics as met_screens
from cloudbot.presentation.screens import packages as pkg_screens
from cloudbot.presentation.screens import ports as port_screens
from cloudbot.presentation.screens import processes as proc_screens
from cloudbot.presentation.screens import run as run_screens
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import tunnels as tnl_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens.language import language_card, language_picker
from cloudbot.presentation.screens.screen import Screen
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.domain.value_objects import SessionStatus
from cloudbot.services.packages import InstallOutcome

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
    running_session = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=ws.id,
        status=SessionStatus.RUNNING,
        cols=80,
        rows=24,
        created_at=NOW,
    )
    ended_session = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=ws.id,
        status=SessionStatus.TIMED_OUT,
        cols=100,
        rows=30,
        created_at=NOW,
        reason="idle_timeout",
    )
    failed_session = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=ws.id,
        status=SessionStatus.FAILED,
        created_at=NOW,
        reason="sandbox_unavailable",
        diagnostic="sandbox unavailable: PTY allocation failed",
    )
    meta = EngineMeta(version="0.0.20", api_version="v1")
    package_list = PackageList(
        packages=(Package(name="six", version="1.16.0"), Package(name="requests", version="2.32.0")),
        count=2,
        truncated=False,
        shared_enabled=True,
        shared_available=False,
        shared_path="",
        private_path="/opt/python/private",
    )
    empty_package_list = PackageList(
        packages=(),
        count=0,
        truncated=False,
        shared_enabled=True,
        shared_available=True,
        shared_path="/opt/python/shared",
        private_path="/opt/python/private",
    )
    install_ok = InstallOutcome(
        result=InstallResult(
            exit_code=0,
            output="Successfully installed six requests\n",
            stdout="Successfully installed six requests\n",
            duration_ms=10_000,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        ),
        allow_net=True,
    )
    install_failed_no_net = InstallOutcome(
        result=InstallResult(
            exit_code=1,
            output="ERROR: Could not find a version that satisfies the requirement requests\n",
            stdout="",
            duration_ms=1200,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        ),
        allow_net=False,
    )
    tunnel_list = TunnelList(
        tunnels=(
            Tunnel(
                id="tnl_01M6HJASCWM9Z1PF755GT89NK4",
                workspace_id=str(ws.id),
                port=8080,
                url="https://example-abc.trycloudflare.com",
                status="active",
                created_at=NOW,
            ),
        )
    )
    empty_tunnel_list = TunnelList(tunnels=())
    port_report = PortReport(
        workspace_id=str(ws.id),
        ports=(
            PortEntry(
                port=8080,
                protocol="tcp",
                address="0.0.0.0",
                scope="wildcard",
                state="LISTEN",
                attributed=True,
                pid=890648,
                kind="run",
                label="python3 -m http.server 8080",
                argv=("python3", "-m", "http.server", "8080"),
                owners=(890648,),
                inode=2726317911,
            ),
            PortEntry(
                port=5353,
                protocol="udp",
                address="0.0.0.0",
                scope="wildcard",
                state="",
                attributed=False,
                owners=(),
            ),
        ),
        unix=(
            UnixSocket(
                path="/tmp/demo.sock",
                type="stream",
                state="unconnected",
                attributed=True,
                pid=890654,
                kind="session",
            ),
        ),
        count=2,
        unix_count=1,
        complete=True,
        notes=(),
        pids_scanned=2,
        fds_scanned=8,
        reachability=(
            "ports are listening inside the workspace's private network namespace; "
            "this does not mean they are reachable from outside. Publishing a port "
            "requires a tunnel (limits.allow_tunnels and tunnels.enabled)."
        ),
    )
    partial_port_report = PortReport(
        workspace_id=str(ws.id),
        ports=port_report.ports[:1],
        unix=(),
        count=1,
        unix_count=0,
        complete=False,
        notes=("pids_scanned reached max_pids=64",),
        pids_scanned=64,
        fds_scanned=512,
        reachability=port_report.reachability,
    )
    metrics = WorkspaceMetrics(
        workspace_id=str(ws.id),
        timestamp="2026-11-04T16:06:07Z",
        interval_seconds=3,
        ram_used_bytes=18_874_368,
        ram_limit_bytes=536_870_912,
        ram_percent=3.5,
        ram_complete=True,
        disk_used_bytes=6089,
        disk_limit_bytes=1_073_741_824,
        disk_percent=0.0006,
        disk_complete=True,
        write_rate_limit_mb_s=0,
        live_runs=2,
        live_cgroups=2,
    )
    metrics_idle = WorkspaceMetrics(
        workspace_id=str(ws.id),
        timestamp="2026-11-04T16:06:07Z",
        interval_seconds=3,
        ram_used_bytes=0,
        ram_limit_bytes=536_870_912,
        ram_percent=0.0,
        ram_complete=True,
        disk_used_bytes=0,
        disk_limit_bytes=1_073_741_824,
        disk_percent=0.0,
        disk_complete=False,
        write_rate_limit_mb_s=0,
        live_runs=0,
        live_cgroups=0,
    )
    process_list = ProcessList(
        processes=(
            ProcessEntry(
                id="proc_0123456789abcdef",
                pid=179173,
                ppid=179172,
                argv=("python3", "bot.py"),
                kind="session",
                source_id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
                started_at=NOW,
            ),
            ProcessEntry(
                id="proc_fedcba9876543210",
                pid=179200,
                ppid=1,
                argv=("sleep", "600"),
                kind="detached",
                source_id="",
                started_at=NOW,
            ),
        ),
        count=2,
    )
    empty_process_list = ProcessList(processes=(), count=0)
    stop_stopped = ProcessStopResult(
        process_id="proc_0123456789abcdef", status="stopped", stopped=True, stopped_at=NOW
    )
    stop_gone = ProcessStopResult(
        process_id="proc_0123456789abcdef", status="gone", stopped=False, stopped_at=NOW
    )
    archive_result = ArchiveResult(
        format="zip",
        destination="/",
        entries_total=3,
        entries_extracted=1,
        entries_skipped=1,
        entries_rejected=1,
        bytes_written=15,
        dry_run=False,
        entries=(
            ArchiveEntry(name="src/main.py", path="/src/main.py", type="file", size=15,
                         mode="-rw-r--r--", rejected=False, skipped=False),
            ArchiveEntry(name="__MACOSX/._x", path="/__MACOSX/._x", type="file", size=0,
                         mode="-rw-r--r--", rejected=False, skipped=True, skip_reason="junk"),
            ArchiveEntry(name="../evil", path="/evil", type="file", size=0,
                         mode="-rw-r--r--", rejected=True, skipped=False,
                         reason="archive_unsafe_entry"),
        ),
    )
    archive_dry_run = ArchiveResult(
        format="zip",
        destination="/dst",
        entries_total=2,
        entries_extracted=0,
        entries_skipped=1,
        entries_rejected=0,
        bytes_written=0,
        dry_run=True,
        entries=(
            ArchiveEntry(name="src/main.py", path="/dst/src/main.py", type="file", size=15,
                         mode="-rw-r--r--", rejected=False, skipped=False),
        ),
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
        running_session=running_session,
        ended_session=ended_session,
        failed_session=failed_session,
        meta=meta,
        package_list=package_list,
        empty_package_list=empty_package_list,
        install_ok=install_ok,
        install_failed_no_net=install_failed_no_net,
        tunnel_list=tunnel_list,
        empty_tunnel_list=empty_tunnel_list,
        port_report=port_report,
        partial_port_report=partial_port_report,
        metrics=metrics,
        metrics_idle=metrics_idle,
        process_list=process_list,
        empty_process_list=empty_process_list,
        stop_stopped=stop_stopped,
        stop_gone=stop_gone,
        archive_result=archive_result,
        archive_dry_run=archive_dry_run,
    )


def _admin_view(locale, *, entered=False) -> AdminView:
    ws = _fixtures()["ws"]
    user = UserSummary(
        tg_id=6508129575,
        display_name="Mohamed",
        locale=locale,
        plan="free",
        created_at=NOW,
        last_seen_at=NOW,
        workspace_count=2,
    )
    page = UserPage(
        user=user,
        workspaces=[
            WorkspaceWithUsage(workspace=ws, usage=ResourceUsage(
                disk_bytes=2_400_000, disk_limit_bytes=268_435_456,
                disk_percent=0.9, runs_total=3, active_runs=1, max_runs=8)),
        ],
    )
    overview = AdminOverview(
        total_users=3,
        active_24h=2,
        workspaces_by_status={"active": 2, "suspended": 1},
        live_runs=2,
        live_sessions=1,
        engine=EngineHealthView(
            reachable=True, health_status="ok", ready_status="ready",
            version="0.0.20", api_version="v1",
        ),
        uptime_seconds=3725.0,
        queue_state="ok",
    )
    event = EventRecord(
        id=7,
        ts=NOW,
        category=EventCategory.ADMIN_ACTION,
        severity=Severity.INFO,
        tg_id=6508129575,
        ws_id=str(ws.id),
        summary_key="event.summary.admin.action",
        params={"action": "suspend"},
        actor="admin:999",
    )
    return AdminView(
        locale=locale,
        now=NOW,
        overview=overview,
        users_page=UserListPage(users=[user], total=1, offset=0, limit=10),
        user_page=page,
        audit_page=AuditPage(events=[event], total=1, offset=0, limit=20),
        recent_events=[event],
        subscriptions={EventCategory.RUN_STARTED.value: True, EventCategory.RUN_FAILED.value: False},
        notify_paused=False,
        notification_cursor=42,
        entered=EnteredWorkspace(
            ws_id=str(ws.id), owner_tg_id=6508129575, workspace=ws, owner_name="Mohamed"
        )
        if entered
        else None,
    )


def _admin_entered_view(locale) -> AdminView:
    return _admin_view(locale, entered=True)


SCREEN_BUILDERS = {
    "language_card": lambda f, loc: language_card(loc),
    "language_picker": lambda f, loc: language_picker(loc),
    "menu": lambda f, loc: menu_screens.main_menu([f["ws"], f["suspended"]], loc, display_name="sara"),
    "menu_empty": lambda f, loc: menu_screens.main_menu([], loc, display_name="sara"),
    "menu_inside": lambda f, loc: menu_screens.main_menu(
        [f["ws"], f["suspended"]], loc, display_name="sara", inside=f["ws"]
    ),
    "help": lambda f, loc: menu_screens.help_screen(loc),
    "help_inside": lambda f, loc: menu_screens.help_screen(loc, inside=f["ws"]),
    "settings": lambda f, loc: menu_screens.settings_screen(loc),
    "settings_inside": lambda f, loc: menu_screens.settings_screen(loc, inside=f["ws"]),
    "language_picker_inside": lambda f, loc: language_picker(loc, inside=f["ws"]),
    "ws_list": lambda f, loc: ws_screens.workspace_list([f["ws"], f["suspended"]], loc),
    "ws_list_empty": lambda f, loc: ws_screens.workspace_list([], loc),
    "ws_list_inside": lambda f, loc: ws_screens.workspace_list(
        [f["ws"], f["suspended"]], loc, inside=f["ws"]
    ),
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
    "terminal_list": lambda f, loc: term_screens.my_terminals([f["running_session"], f["ended_session"]], f["ws"].id, loc, now=NOW),
    "terminal_list_empty": lambda f, loc: term_screens.my_terminals([], f["ws"].id, loc),
    "terminal_view_running": lambda f, loc: term_screens.terminal_view(
        f["running_session"], f["ws"].id, loc, output="$ ls\nmain.py  README.md\n$ ", meta=f["meta"]
    ),
    "terminal_view_ended": lambda f, loc: term_screens.terminal_view(f["ended_session"], f["ws"].id, loc, output="$ echo hi\nhi\n$ "),
    "terminal_view_failed": lambda f, loc: term_screens.terminal_view(f["failed_session"], f["ws"].id, loc),
    "terminal_resize": lambda f, loc: term_screens.resize_screen(f["running_session"], f["ws"].id, loc),
    "terminal_ended": lambda f, loc: term_screens.ended_screen(f["ended_session"], f["ws"].id, loc),
    "terminal_command": lambda f, loc: term_screens.command_prompt(f["ws"].id, f["running_session"].id, loc),
    "err_unavailable": lambda f, loc: error_mapping.error_code_screen("unavailable", loc),
    "err_workspace_suspended": lambda f, loc: error_mapping.error_code_screen("workspace_suspended", loc),
    "err_unprocessable": lambda f, loc: error_mapping.error_code_screen("unprocessable", loc),
    "err_not_found": lambda f, loc: error_mapping.error_code_screen("not_found", loc),
    # -- Phase 2a: packages --------------------------------------------------
    "package_list": lambda f, loc: pkg_screens.packages_screen(f["package_list"], f["ws"].id, loc),
    "package_list_empty": lambda f, loc: pkg_screens.packages_screen(f["empty_package_list"], f["ws"].id, loc),
    "package_install_prompt": lambda f, loc: pkg_screens.install_prompt(f["ws"].id, loc),
    "package_install_working": lambda f, loc: pkg_screens.installing_screen(f["ws"].id, loc),
    "package_install_ok": lambda f, loc: pkg_screens.install_result_screen(f["install_ok"], "six requests", f["ws"].id, loc),
    "package_install_failed_no_net": lambda f, loc: pkg_screens.install_result_screen(
        f["install_failed_no_net"], "requests", f["ws"].id, loc
    ),
    "package_remove_prompt": lambda f, loc: pkg_screens.remove_prompt(f["ws"].id, loc),
    "package_remove_all_confirm": lambda f, loc: pkg_screens.remove_all_confirm("demo", f["ws"].id, loc),
    # -- Phase 2b: tunnels ---------------------------------------------------
    "tunnel_list": lambda f, loc: tnl_screens.tunnels_screen(f["tunnel_list"], f["ws"].id, loc),
    "tunnel_list_empty": lambda f, loc: tnl_screens.tunnels_screen(f["empty_tunnel_list"], f["ws"].id, loc),
    "tunnel_gate_allow_tunnels": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, allow_tunnels=False
    ),
    "tunnel_gate_allow_net": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, allow_net=False
    ),
    "tunnel_gate_server": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, server_enabled=False
    ),
    "tunnel_publish": lambda f, loc: tnl_screens.publish_port_screen(f["ws"].id, loc, detected=[8080]),
    "tunnel_publish_custom": lambda f, loc: tnl_screens.publish_custom_prompt(f["ws"].id, loc),
    "tunnel_remove_confirm": lambda f, loc: tnl_screens.remove_confirm(f["tunnel_list"].tunnels[0], f["ws"].id, loc),
    # -- Phase 2c: ports -----------------------------------------------------
    "port_list": lambda f, loc: port_screens.ports_screen(f["port_report"], f["ws"].id, loc),
    "port_list_partial": lambda f, loc: port_screens.ports_screen(f["partial_port_report"], f["ws"].id, loc),
    # -- Phase 2d: metrics ---------------------------------------------------
    "metrics": lambda f, loc: met_screens.metrics_screen(f["metrics"], f["ws"].id, loc),
    "metrics_idle_incomplete": lambda f, loc: met_screens.metrics_screen(f["metrics_idle"], f["ws"].id, loc),
    # -- Phase 2e: processes -------------------------------------------------
    "process_list": lambda f, loc: proc_screens.processes_screen(f["process_list"], f["ws"].id, loc),
    "process_list_empty": lambda f, loc: proc_screens.processes_screen(f["empty_process_list"], f["ws"].id, loc),
    "process_stop_confirm": lambda f, loc: proc_screens.stop_confirm(f["process_list"].processes[0], f["ws"].id, loc),
    "process_stopped": lambda f, loc: proc_screens.stop_result_screen(f["stop_stopped"], f["ws"].id, loc),
    "process_gone": lambda f, loc: proc_screens.stop_result_screen(f["stop_gone"], f["ws"].id, loc),
    # -- Phase 2g: archives --------------------------------------------------
    "archive_menu": lambda f, loc: arc_screens.archive_menu(f["ws"].id, loc),
    "archive_upload_prompt": lambda f, loc: arc_screens.upload_prompt(f["ws"].id, 50, loc),
    "archive_extract_prompt": lambda f, loc: arc_screens.extract_prompt(f["ws"].id, loc),
    "archive_export_prompt": lambda f, loc: arc_screens.export_prompt(f["ws"].id, loc),
    "archive_result": lambda f, loc: arc_screens.archive_result_screen(f["archive_result"], f["ws"].id, loc),
    "archive_dry_run": lambda f, loc: arc_screens.dry_run_prompt(
        f["archive_dry_run"], f["ws"].id, loc, action="extract", target="/bundle.zip"
    ),
    "archive_export_done": lambda f, loc: arc_screens.export_done_screen(
        "project.zip", 4096, f["ws"].id, loc
    ),
    # -- admin panel ---------------------------------------------------------
    "admin_overview": lambda f, loc: admin_overview.overview_screen(_admin_view(loc)),
    "admin_overview_unreachable": lambda f, loc: admin_overview.overview_screen(
        AdminView(
            locale=loc,
            now=NOW,
            overview=AdminOverview(
                total_users=1, active_24h=0, engine=EngineHealthView(reachable=False)
            ),
        )
    ),
    "admin_users": lambda f, loc: admin_users.users_screen(_admin_view(loc)),
    "admin_users_empty": lambda f, loc: admin_users.users_screen(
        AdminView(
            locale=loc, users_page=UserListPage(users=[], total=0)
        )
    ),
    "admin_user": lambda f, loc: admin_user.user_screen(_admin_view(loc)),
    "admin_notifications": lambda f, loc: admin_notify.notifications_screen(_admin_view(loc)),
    "admin_audit": lambda f, loc: admin_audit.audit_screen(_admin_view(loc)),
    "admin_entered": lambda f, loc: admin_workspace.entered_workspace_screen(
        _admin_entered_view(loc)
    ),
    "admin_confirm_suspend": lambda f, loc: confirmation_screen(
        action("suspend"), "demo (ws_01M6HJASCWM9Z1PF755GT89NK4)",
        "ws_01M6HJASCWM9Z1PF755GT89NK4", loc
    ),
    "admin_confirm_delete": lambda f, loc: confirmation_screen(
        action("delete"), "demo (ws_01M6HJASCWM9Z1PF755GT89NK4)",
        "ws_01M6HJASCWM9Z1PF755GT89NK4", loc
    ),
    "admin_notify_message": lambda f, loc: Screen(doc=notification_message(
        NotificationMessage(
            category=EventCategory.WORKSPACE_CREATED,
            severity=Severity.INFO,
            count=3,
            first_event_id=1,
            last_event_id=3,
            summary_key="event.summary.workspace.created",
            params={"name": "demo"},
            event_ids=(1, 2, 3),
        ),
        loc,
    )),
    "admin_notify_gap": lambda f, loc: Screen(doc=gap_message(250, loc)),
    "admin_notify_capped": lambda f, loc: Screen(doc=suppression_message(27, loc)),
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


# -- keyboard goldens (TASK 09) ----------------------------------------------
#: The top-level screens whose inline navigation block is layout-load-bearing.
KB_AFFECTED = {
    "menu",
    "menu_empty",
    "help",
    "settings",
    "ws_list",
    "ws_list_empty",
    "ws_list_inside",
    "menu_inside",
    "language_picker",
    # Task 13: while inside, the nav block is the session's (ws:home + ws:list);
    # the account destinations are gone. Lock the inside variants of the screens
    # that are reachable while inside so the rule is visible in the goldens.
    "help_inside",
    "settings_inside",
    "language_picker_inside",
    # Task 10: the workspace home carries the log-out row in every status branch.
    # Task 11: it lost the redundant bottom Back row and ends in the shared nav block.
    "ws_detail",
    "ws_detail_suspended",
    "ws_detail_locked",
    # The admin's entered screen reuses the user workspace home (session=False), so
    # its keyboard changed with it; lock it here too.
    "admin_entered",
}


def _keyboard_text(screen) -> str:
    """A stable, human-readable dump of a screen's keyboard (texts, callbacks,
    disabled flags). Rows are one per line; a reply keyboard is spelled out so its
    reappearance is a visible golden diff."""
    markup = screen.markup
    if markup is None:
        return "<no keyboard>\n"
    inline_rows = getattr(markup, "inline_keyboard", None)
    if inline_rows is None:
        return f"REPLY_KEYBOARD {markup!r}\n"
    lines = []
    for row in inline_rows:
        cells = []
        for button in row:
            action = button.callback_data or button.url or ""
            flag = "disabled" if getattr(button, "disabled", None) is not None else "enabled"
            cells.append(f"{button.text} [{action}] {flag}")
        lines.append(" | ".join(cells))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("name", sorted(KB_AFFECTED))
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_golden_keyboard(name, locale):
    CallbackCodec()
    screen = SCREEN_BUILDERS[name](_fixtures(), locale)
    text = _keyboard_text(screen)
    assert "REPLY_KEYBOARD" not in text, f"{name}/{locale} returned a reply keyboard"
    expected_file = EXPECTED / f"{name}.{locale.value}.keyboard.txt"
    if UPDATE or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(text, encoding="utf-8")
        pytest.skip(f"wrote keyboard golden {expected_file.name}")
    assert text == expected_file.read_text(encoding="utf-8"), (
        f"{name}/{locale} keyboard differs from golden; run UPDATE_GOLDEN=1 to refresh"
    )

