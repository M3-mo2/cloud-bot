"""The screen catalog: the product's own registry of every screen.

This module is the **architectural core** of TASK 24. Before it, the 111-screen
catalog existed only inside a test (``tests/golden/test_golden_screens.py``), so:

* the product could not render a screen it did not know a name for;
* the only thing that walked every screen was a golden test, and anything wanting
  a catalog view had to import a test module.

Here the catalog is **data**: a ``ScreenSpec`` (name → section → builder) plus the
pure, fixed fixtures those builders need. A section is a **first-class value**, not
a prefix convention:

* ``sections()`` lists the sections (ordered);
* ``screens_in(section)`` lists a section's screens;
* ``build(section, name, locale)`` renders one screen;
* ``fixtures()`` returns the frozen domain state the builders consume.

Adding a screen is **one registry entry**, and it is then automatically covered by
the goldens, by the button walk, and by the ``/dev`` inspector.

The verdict logic below is the **one** implementation shared by the dev tool and
the contract tests (the maintainability requirement: one implementation, two
callers). It classifies every button without importing ``tests/`` or a dispatcher;
the caller supplies a ``claims`` predicate (the real dispatcher's own routing) so
this module stays pure presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from cloudbot.core.locale import Locale
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
from cloudbot.domain.entities import FileEntry, Run, TerminalSession, Workspace
from cloudbot.domain.forcejoin import ForceJoinChat, GateStatus
from cloudbot.domain.moderation import UserBan, WorkspaceBan
from cloudbot.domain.backup import (
    BackupConfig,
    BackupFile,
    BackupView,
)
from cloudbot.domain.broadcast import (
    AudienceOption,
    BroadcastResult,
    BroadcastRun,
    BroadcastSource,
    BroadcastView,
    FailureGroup,
)
from cloudbot.domain.events import (
    EventCategory,
    EventRecord,
    NotificationMessage,
    Severity,
)
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import (
    ArchiveEntry,
    ArchiveResult,
    ExecResult,
    FileContent,
    FilePage,
    InstallResult,
    Limits,
    Package,
    PackageList,
    PortEntry,
    PortReport,
    ProcessEntry,
    ProcessList,
    ProcessStopResult,
    ResourceUsage,
    RunPage,
    RunStatus,
    SessionStatus,
    SummaryState,
    Tunnel,
    TunnelList,
    UnixSocket,
    WorkspaceId,
    WorkspaceMetrics,
    WorkspaceStatus,
    WorkspaceSummary,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.callbacks import TOKEN_MARKER, codec
from cloudbot.presentation.mapping import errors as error_mapping
from cloudbot.presentation.screens import archives as arc_screens
from cloudbot.presentation.screens import files as file_screens
from cloudbot.presentation.screens import menu as menu_screens
from cloudbot.presentation.screens import metrics as met_screens
from cloudbot.presentation.screens import packages as pkg_screens
from cloudbot.presentation.screens import ports as port_screens
from cloudbot.presentation.screens import processes as proc_screens
from cloudbot.presentation.screens import run as run_screens
from cloudbot.presentation.screens import run_logs as history_screens
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import tunnels as tnl_screens
from cloudbot.presentation.screens import wallet as wallet_screens
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens import templates as tpl_screens
from cloudbot.domain import templates as tpl_templates
from cloudbot.presentation.screens.forcejoin import gate_screen
from cloudbot.domain.billing import STAR_PACKS
from cloudbot.presentation.screens.admin import audit as admin_audit
from cloudbot.presentation.screens.admin import backup as admin_backup
from cloudbot.presentation.screens.admin import forcejoin as admin_forcejoin
from cloudbot.presentation.screens.admin import notifications as admin_notify
from cloudbot.presentation.screens.admin import overview as admin_overview
from cloudbot.presentation.screens.admin import user_page as admin_user
from cloudbot.presentation.screens.admin import users as admin_users
from cloudbot.presentation.screens.admin import moderation as admin_moderation
from cloudbot.presentation.screens.admin import broadcast as admin_broadcast
from cloudbot.presentation.screens.admin import workspace as admin_workspace
from cloudbot.presentation.screens.admin.actions import action, confirmation_screen
from cloudbot.presentation.screens.admin.notification_message import (
    gap_message,
    notification_message,
    suppression_message,
)
from cloudbot.presentation.screens.language import language_card, language_picker
from cloudbot.presentation.screens.screen import Screen
from cloudbot.services.packages import InstallOutcome

#: The fixed clock every fixture uses — a *constant*, never ``datetime.now()``, so
#: a screen rendered today is byte-identical to one rendered a year from now.
NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)

#: The workspace seed time. Deliberately distinct from ``NOW`` (exactly as the
#: fake engine's ``_NOW`` was): the workspace screens render "created at" from the
#: workspace and "before X min" against ``NOW``, so the two clocks must differ for
#: the golden snapshots to stay byte-identical to the pre-catalog ones.
_WS_NOW = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)
_WS_LAST_ACTIVITY = _WS_NOW - timedelta(minutes=2)

#: Screens that are deliberately not RTL even in Arabic (bilingual by design).
NEVER_RTL = {"language_card"}


def _template(key: str) -> tpl_templates.Template:
    """A fixed starter template for the goldens (the catalog is static)."""
    template = tpl_templates.by_key(key)
    assert template is not None, key
    return template

#: A builder receives the fixture dict and a locale, and returns a pure ``Screen``.
ScreenBuilder = Callable[[dict[str, Any], Locale], Screen]


# -- fixtures (moved out of the golden test; pure, fixed, no I/O) --------------
def _workspace(
    ws_id: str, name: str, status: WorkspaceStatus, active: int = 0
) -> Workspace:
    """A fixed workspace fixture, built straight from the domain entities.

    Reimplemented here (instead of importing ``adapters.cloud_service.fake``)
    because ``presentation`` may not import ``adapters``: the catalog is product
    code and must obey the layer rule.
    """
    return Workspace(
        id=WorkspaceId(ws_id),
        owner_tg_id=0,
        name=name,
        profile="python",
        status=status,
        limits=Limits(),
        summary=WorkspaceSummary(
            state=SummaryState.ACTIVE
            if status is WorkspaceStatus.ACTIVE
            else SummaryState.LOCKED
            if status is WorkspaceStatus.LOCKED
            else SummaryState.ACTIVE,
            age_sec=0,
            created_at=_WS_NOW.isoformat(),
            runs_total=3,
            runs_active=active,
            disk_used_bytes=2_400_000,
            disk_limit_bytes=268_435_456,
            profile="python",
        ),
        created_at=_WS_NOW,
        last_activity_at=_WS_LAST_ACTIVITY,
    )


def fixtures() -> dict[str, Any]:
    ws = _workspace("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=1)
    suspended = _workspace("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED)
    locked = _workspace("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED)
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
    #: TASK file-tree's own drawing: two folders, three files, and enough other
    #: entries to exercise the pager. Folders sort before files, and the names
    #: are deliberately out of order so ``_ordered`` is the thing being measured.
    file_page = FilePage(
        path="/app",
        entries=(
            FileEntry("/app/src", "src", "dir", None, "drwxr-xr-x", NOW, None),
            FileEntry("/app/utils", "utils", "dir", None, "drwxr-xr-x", NOW, None),
            FileEntry("/app/main.py", "main.py", "file", 1229, "-rw-r--r--", NOW, "python3"),
            FileEntry("/app/config.py", "config.py", "file", 340, "-rw-r--r--", NOW, "python3"),
            FileEntry("/app/README.md", "README.md", "file", 2150, "-rw-r--r--", NOW, None),
            FileEntry(
                "/app/requirements.txt",
                "requirements.txt",
                "file",
                18,
                "-rw-r--r--",
                NOW,
                None,
            ),
        ),
        count=6,
        total=6,
        truncated=False,
    )
    #: The second page of an eleven-entry folder: one entry left, a "page 2 of 2"
    #: footer and a previous-page button. Locking it proves the pager is drawn.
    paged_file_page = FilePage(
        path="/app",
        entries=(
            FileEntry("/app/zzz_last.py", "zzz_last.py", "file", 900, "-rw-r--r--", NOW, "python3"),
        ),
        count=1,
        total=11,
        truncated=False,
        offset=10,
        limit=10,
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
    # A distinct ULID sharing the running session's first 12 characters: the
    # realistic same-millisecond case the terminal list must disambiguate.
    ended_session = TerminalSession(
        id="ses_01M6HJATYTFXTWXWBG8A5KWYS8",
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
    full_tunnel_list = TunnelList(
        tunnels=(
            Tunnel(
                id="tnl_01M6HJASCWM9Z1PF755GT89NK4",
                workspace_id=str(ws.id),
                port=8080,
                url="https://example-abc.trycloudflare.com",
                status="active",
                created_at=NOW,
            ),
            Tunnel(
                id="tnl_01M6HJASCWM9Z1PF755GT89NK5",
                workspace_id=str(ws.id),
                port=8081,
                url="https://example-def.trycloudflare.com",
                status="active",
                created_at=NOW,
            ),
        )
    )
    # F-07: a stopped tunnel keeps its row but is no longer live.
    stopped_tunnel_list = TunnelList(
        tunnels=(
            Tunnel(
                id="tnl_01M6HJASCWM9Z1PF755GT89NK4",
                workspace_id=str(ws.id),
                port=8080,
                url="https://example-abc.trycloudflare.com",
                status="active",
                created_at=NOW,
            ),
            Tunnel(
                id="tnl_01M6HJASCWM9Z1PF755GT89NK6",
                workspace_id=str(ws.id),
                port=8082,
                url="https://example-ghi.trycloudflare.com",
                status="stopped",
                created_at=NOW,
            ),
        )
    )
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
    #: TASK metrics-shape: the goldens must cover all four corners — idle and
    #: busy, complete and incomplete — so a tone/bar/idle rule cannot regress in
    #: the combination nobody snapshotted. ``metrics`` is busy+complete and
    #: ``metrics_idle`` is idle+incomplete; these two fill the other corners.
    metrics_busy_incomplete = WorkspaceMetrics(
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
        disk_complete=False,
        write_rate_limit_mb_s=12,
        live_runs=1,
        live_cgroups=1,
    )
    metrics_idle_complete = WorkspaceMetrics(
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
        disk_complete=True,
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
        paged_file_page=paged_file_page,
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
        full_tunnel_list=full_tunnel_list,
        stopped_tunnel_list=stopped_tunnel_list,
        port_report=port_report,
        partial_port_report=partial_port_report,
        metrics=metrics,
        metrics_idle=metrics_idle,
        metrics_busy_incomplete=metrics_busy_incomplete,
        metrics_idle_complete=metrics_idle_complete,
        process_list=process_list,
        empty_process_list=empty_process_list,
        stop_stopped=stop_stopped,
        stop_gone=stop_gone,
        archive_result=archive_result,
        archive_dry_run=archive_dry_run,
    )


def _admin_view(locale: Locale, *, entered: bool = False) -> AdminView:
    ws = fixtures()["ws"]
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
        admin_id=6508129575,
        backup=BackupView(
            config=BackupConfig(
                enabled=True,
                period_kind="daily",
                period_hours=24,
                daily_time="03:00",
                tz_name="Africa/Cairo",
                retention_keep=7,
                next_run_at=NOW + timedelta(hours=6),
                last_run_at=NOW - timedelta(hours=18),
            ),
            files=(
                BackupFile(
                    name="cloudbot-backup-20261103T024139Z.zip",
                    size_bytes=184_320,
                    created_at=NOW - timedelta(hours=18),
                    scope="full",
                ),
                BackupFile(
                    name="cloudbot-settings-20261102T024139Z.json",
                    size_bytes=4_096,
                    created_at=NOW - timedelta(days=1, hours=2),
                    scope="settings",
                ),
            ),
            schema_version=9,
            code_schema_version=9,
            directory="/var/www/data/backups",
        ),
        overview=overview,
        broadcast=_broadcast_view(),
        users_page=UserListPage(users=[user], total=1, offset=0, limit=10),
        user_page=page,
        audit_page=AuditPage(events=[event], total=1, offset=0, limit=20),
        recent_events=[event],
        notification_subscriptions={EventCategory.RUN_STARTED.value: True, EventCategory.RUN_FAILED.value: False},
        notify_paused=False,
        notification_cursor=42,
        entered=EnteredWorkspace(
            ws_id=str(ws.id), owner_tg_id=6508129575, workspace=ws, owner_name="Mohamed"
        )
        if entered
        else None,
        forcejoin_chats=[
            ForceJoinChat(
                id=1,
                chat_id=-1001234567890,
                username="cloud_world",
                title="Cloud World",
                kind="channel",
                bot_is_admin=True,
                enabled=True,
                added_at=NOW,
                added_by=999,
            ),
            ForceJoinChat(
                id=2,
                chat_id=-1009876543210,
                username="cloud_chat",
                title="Cloud Chat",
                kind="group",
                bot_is_admin=False,
                enabled=False,
                added_at=NOW,
                added_by=999,
            ),
        ],
        forcejoin_enabled=True,
    )


def _banned_user(tg_id: int, reason: str = "Abuse", *, expires_in_days: int | None = None):
    return UserBan(
        tg_id=tg_id,
        reason=reason,
        banned_at=NOW - timedelta(days=1),
        banned_by=999,
        expires_at=NOW + timedelta(days=expires_in_days) if expires_in_days else None,
    )


def _forcejoin_stats():
    """A gate-stats read model for the golden screen (TASK forcejoin-v2 Part 3)."""
    from cloudbot.domain.forcejoin_stats import (
        CacheStat,
        ChatStat,
        ForceJoinStats,
        Funnel,
        GateCounts,
        Health,
        OutcomeCount,
        RefusedUser,
        Volume,
    )

    return ForceJoinStats(
        range="7d",
        has_data=True,
        gate=GateCounts(
            enabled=True, chats_total=2, chats_enabled=2, chats_needing_attention=1
        ),
        volume=Volume(decisions=42, distinct_users=17),
        outcomes=(
            OutcomeCount("pass", 24, 57.14),
            OutcomeCount("deny", 12, 28.57),
            OutcomeCount("unknown", 4, 9.52),
            OutcomeCount("error", 2, 4.76),
        ),
        funnel=Funnel(hit_gate=17, refused=9, then_passed=6),
        per_chat=(
            ChatStat(
                chat_id=-1001234567890,
                title="Cloud World",
                username="cloud_world",
                bot_admin=True,
                enabled=True,
                decisions=30,
                deny_rate=33.33,
                unknown_rate=6.67,
                last_decision_at="2026-11-06T11:59:00Z",
            ),
            ChatStat(
                chat_id=-1009876543210,
                title="Cloud Chat",
                username="cloud_chat",
                bot_admin=False,
                enabled=True,
                decisions=12,
                deny_rate=16.67,
                unknown_rate=16.67,
                last_decision_at="2026-11-06T11:58:00Z",
            ),
        ),
        cache=CacheStat(hits=9, misses=33, hit_rate=21.43, p50_ms=180, p95_ms=640),
        top_refused=(
            RefusedUser(tg_id=6508129575, display_name="Mohamed", denials=5),
            RefusedUser(tg_id=123456789, display_name="", denials=3),
        ),
        health=Health(
            unknown_rate=9.52, error_rate=4.76, chats_needing_attention=1, cache_hit_rate=21.43
        ),
    )



def _forcejoin_stats_empty():
    """The honest empty range: ``has_data=False`` renders "no decisions"."""
    from dataclasses import replace

    return replace(_forcejoin_stats(), range="all", has_data=False, volume=replace(_forcejoin_stats().volume, decisions=0, distinct_users=0))


def _moderation_view(locale: Locale) -> AdminView:
    view = _admin_view(locale)
    from dataclasses import replace

    return replace(
        view,
        banned_users=[
            _banned_user(6508129575, "Repeated abuse of the engine", expires_in_days=7),
            _banned_user(123456789, "Spam", expires_in_days=None),
        ],
        banned_workspaces=[
            WorkspaceBan(
                ws_id="ws_01M6HJASCWM9Z1PF755GT89NK4",
                reason="Abuse of shared storage",
                banned_at=NOW - timedelta(hours=3),
                banned_by=999,
            )
        ],
    )

def _broadcast_run(run_id: int = 3, *, status: str = "done", sent: int = 40, failed: int = 4) -> BroadcastRun:
    return BroadcastRun(
        id=run_id,
        created_at=NOW - timedelta(hours=2),
        created_by=6508129575,
        source=BroadcastSource(chat_id=6508129575, message_id=77, kind="photo"),
        audience="all",
        total=sent + failed,
        sent=sent,
        failed=failed,
        status=status,
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        stop_reason="",
    )


def _broadcast_view() -> BroadcastView:
    return BroadcastView(
        options=(
            AudienceOption(key="all", count=1200),
            AudienceOption(key="active", count=342),
            AudienceOption(key="expired", count=118),
            AudienceOption(key="no_plan", count=57),
        ),
        history=(
            _broadcast_run(3),
            _broadcast_run(2, status="stopped", sent=10, failed=5),
            _broadcast_run(1, status="done", sent=0, failed=0),
        ),
        open_run=None,
        max_recipients=5000,
        failure_threshold=5,
    )


def _broadcast_open_view() -> BroadcastView:
    from dataclasses import replace as _replace

    view = _broadcast_view()
    return _replace(view, open_run=_broadcast_run(4, status="sending", sent=12, failed=0))


def _broadcast_result() -> BroadcastResult:
    return BroadcastResult(
        run=_broadcast_run(3),
        groups=(
            FailureGroup(kind="blocked", count=3),
            FailureGroup(kind="other", count=1),
        ),
    )


def plans_plan(code: str):
    from cloudbot.domain.plans import plan as _plan

    return _plan(code)

def _wallet_view(f, locale: Locale, *, none: bool = False, expired: bool = False):
    from cloudbot.domain.billing import StarsPayment
    from cloudbot.domain.subscription import (
        Movement,
        Subscription,
        SubscriptionSource,
        SubscriptionState,
        SubscriptionStatus,
        derive_status,
    )
    from cloudbot.presentation.screens.wallet import WalletView

    balance = 350
    if none:
        subscription = None
        status = SubscriptionStatus(SubscriptionState.NONE, False, 0)
    elif expired:
        subscription = Subscription(
            tg_id=6508129575,
            plan_code="month",
            started_at=NOW - timedelta(days=60),
            expires_at=NOW - timedelta(days=30),
            source=SubscriptionSource.STARS,
        )
        status = derive_status(subscription, NOW)
    else:
        subscription = Subscription(
            tg_id=6508129575,
            plan_code="month",
            started_at=NOW - timedelta(days=2),
            expires_at=NOW + timedelta(days=28),
            source=SubscriptionSource.TRIAL,
        )
        status = derive_status(subscription, NOW)
    # A ledger and payments ending at the fixture clock: one purchase debit, the
    # Stars credit that funded it, an admin grant, and a refunded payment.
    ledger = (
        Movement(
            tg_id=6508129575,
            kind="credit",
            amount=500,
            reason="stars:stars_500",
            at=NOW - timedelta(days=5),
            actor_tg_id=6508129575,
            idempotency_key="wallet:seed:stars",
        ),
        Movement(
            tg_id=6508129575,
            kind="debit",
            amount=-150,
            reason="purchase:month",
            at=NOW - timedelta(days=4),
            actor_tg_id=6508129575,
        ),
        Movement(
            tg_id=6508129575,
            kind="credit",
            amount=50,
            reason="admin adjustment",
            at=NOW - timedelta(days=1),
            actor_tg_id=777,
        ),
    )
    payments = (
        StarsPayment(
            tg_id=6508129575,
            pack_code="stars_150",
            stars=150,
            charge_id="ch_fixture_refunded",
            provider_charge_id="",
            created_at=NOW - timedelta(days=6),
            refunded_at=NOW - timedelta(days=5),
        ),
        StarsPayment(
            tg_id=6508129575,
            pack_code="stars_500",
            stars=500,
            charge_id="ch_fixture_paid",
            provider_charge_id="",
            created_at=NOW - timedelta(days=5),
        ),
    )
    return WalletView(
        locale=locale,
        tg_id=6508129575,
        balance=balance,
        subscription=subscription,
        status=status,
        now=NOW,
        workspaces=(("demo", subscription, status),),
        ledger=ledger,
        payments=payments,
    )

def _admin_entered_view(locale: Locale) -> AdminView:
    return _admin_view(locale, entered=True)


def _wallet_two_plans(locale: Locale):
    """Two workspaces on different plans: the acceptance case for ``planws``.

    One is a live monthly plan, the other's plan lapsed a day ago, so the wallet
    must show two different states on two workspace lines.
    """
    from cloudbot.domain.subscription import (
        Subscription,
        SubscriptionSource,
        derive_status,
    )
    from cloudbot.presentation.screens.wallet import WalletView

    active = Subscription(
        tg_id=6508129575,
        plan_code="month",
        started_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=28),
        source=SubscriptionSource.STARS,
    )
    lapsed = Subscription(
        tg_id=6508129575,
        plan_code="week",
        started_at=NOW - timedelta(days=10),
        expires_at=NOW - timedelta(days=3),
        source=SubscriptionSource.STARS,
    )
    return WalletView(
        locale=locale,
        tg_id=6508129575,
        balance=350,
        subscription=active,
        status=derive_status(active, NOW),
        now=NOW,
        workspaces=(
            ("demo", active, derive_status(active, NOW)),
            ("scraper", lapsed, derive_status(lapsed, NOW)),
        ),
    )


def _wallet_history_view(locale: Locale, *, empty: bool = False):
    view = _wallet_view(fixtures(), locale)
    if not empty:
        return view
    from cloudbot.presentation.screens.wallet import WalletView

    return WalletView(
        locale=view.locale,
        tg_id=view.tg_id,
        balance=view.balance,
        subscription=view.subscription,
        status=view.status,
        now=view.now,
        workspaces=view.workspaces,
        ledger=(),
        payments=(),
    )


def _wallet_history_paged(locale: Locale):
    """More than one page of movements, so the shared pager is exercised."""
    from cloudbot.domain.subscription import Movement
    from cloudbot.presentation.screens.wallet import WalletView

    view = _wallet_view(fixtures(), locale)
    ledger = tuple(
        Movement(
            tg_id=6508129575,
            kind="credit",
            amount=10 + index,
            reason="seed",
            at=NOW - timedelta(hours=index),
            actor_tg_id=777,
        )
        for index in range(12)
    )
    return WalletView(
        locale=view.locale,
        tg_id=view.tg_id,
        balance=view.balance,
        subscription=view.subscription,
        status=view.status,
        now=view.now,
        workspaces=view.workspaces,
        ledger=ledger,
        payments=(),
    )


#: Tracebacks the missing-package screens render. The mapped one names PIL (pip
#: ``pillow``); the unknown one names a module no entry covers.
_MISSING_PIL = "ModuleNotFoundError: No module named 'PIL'\n"
_MISSING_UNKNOWN = "ModuleNotFoundError: No module named 'mystery_lib'\n"
_MISSING_TWO = (
    "ModuleNotFoundError: No module named 'flask'\n"
    "ModuleNotFoundError: No module named 'PIL'\n"
)
_MISSING_FLASK = "ModuleNotFoundError: No module named 'flask'\n"

#: name → builder. This is the registry's payload; ``SECTION_OF`` assigns each
#: name to a section (a first-class grouping, not a prefix convention).
SCREEN_BUILDERS: dict[str, ScreenBuilder] = {
    # -- wallet / subscription (subs2) ---------------------------------------
    "wallet": lambda f, loc: wallet_screens.wallet_screen(_wallet_view(f, loc)),
    "wallet_no_plan": lambda f, loc: wallet_screens.wallet_screen(_wallet_view(f, loc, none=True)),
    "wallet_expired": lambda f, loc: wallet_screens.wallet_screen(_wallet_view(f, loc, expired=True)),
    "wallet_two_plans": lambda f, loc: wallet_screens.wallet_screen(_wallet_two_plans(loc)),
    "wallet_history": lambda f, loc: wallet_screens.history_screen(_wallet_view(f, loc)),
    "wallet_history_empty": lambda f, loc: wallet_screens.history_screen(
        _wallet_history_view(loc, empty=True)
    ),
    "wallet_history_page_2": lambda f, loc: wallet_screens.history_screen(
        _wallet_history_paged(loc), page=1
    ),
    "wallet_plans": lambda f, loc: wallet_screens.plan_picker_screen(
        _wallet_view(f, loc), needed_plan="year"
    ),
    "wallet_packs": lambda f, loc: wallet_screens.packs_screen(_wallet_view(f, loc)),
    # The same page bought mid-wizard: its Back returns to the create flow's plan
    # step instead of the wallet, so «متابعة» is never lost. Locked separately
    # because that exit is the part that changes.
    "wallet_packs_wizard": lambda f, loc: wallet_screens.packs_screen(
        _wallet_view(f, loc), back_action="ws:continue"
    ),
    "wallet_receipt": lambda f, loc: wallet_screens.receipt_screen(
        _wallet_view(f, loc), STAR_PACKS["stars_150"]
    ),
    "wallet_support": lambda f, loc: wallet_screens.support_screen(loc),
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
    # The workspace home is one flat page with no disclosure control (TASK
    # no-more-less), so there is no second, expanded variant of it.
    "ws_detail_suspended": lambda f, loc: ws_screens.workspace_detail(f["suspended"], loc, now=NOW),
    "ws_detail_locked": lambda f, loc: ws_screens.workspace_detail(f["locked"], loc, now=NOW),
    "ws_settings": lambda f, loc: ws_screens.workspace_settings(f["ws"], loc),
    "ws_settings_suspended": lambda f, loc: ws_screens.workspace_settings(f["suspended"], loc),
    "ws_create_name": lambda f, loc: ws_screens.create_name_prompt(loc),
    "ws_create_network": lambda f, loc: ws_screens.create_network(
        Limits(allow_net=True, allow_tunnels=True), loc
    ),
    "ws_create_plan": lambda f, loc: ws_screens.create_plan_picker(
        (plans_plan("trial"), plans_plan("week"), plans_plan("month"), plans_plan("year")),
        350,
        loc,
        workspace_name="demo",
    ),
    "ws_create_plan_short": lambda f, loc: ws_screens.create_plan_picker(
        (plans_plan("trial"), plans_plan("week"), plans_plan("month"), plans_plan("year")),
        20,
        loc,
        workspace_name="demo",
        needed_plan="month",
    ),
    "ws_create_plan_trial_used": lambda f, loc: ws_screens.create_plan_picker(
        (plans_plan("week"), plans_plan("month"), plans_plan("year")),
        350,
        loc,
        workspace_name="demo",
    ),
    "ws_delete_confirm": lambda f, loc: ws_screens.delete_confirm(f["ws"], loc),
    "ws_stop_confirm": lambda f, loc: ws_screens.stop_confirm(f["ws"], loc),
    "ws_suspend_confirm": lambda f, loc: ws_screens.suspend_confirm(f["ws"], loc),
    # -- starter templates (TASK python-templates) ---------------------------
    "tpl_list": lambda f, loc: tpl_screens.list_screen(f["ws"].id, loc),
    "tpl_confirm": lambda f, loc: tpl_screens.confirm_screen(
        _template("module"), f["ws"].id, loc
    ),
    "tpl_confirm_conflict": lambda f, loc: tpl_screens.confirm_screen(
        _template("hello"), f["ws"].id, loc, conflicts=["/hello"]
    ),
    "tpl_done_created": lambda f, loc: tpl_screens.done_created(
        _template("module"), f["ws"].id, loc, tpl_templates.paths(_template("module"))
    ),
    "tpl_done_refused": lambda f, loc: tpl_screens.done_refused(
        _template("hello"), f["ws"].id, loc, ["/hello"]
    ),
    "tpl_done_failed": lambda f, loc: tpl_screens.done_failed(
        _template("flask-api"),
        f["ws"].id,
        loc,
        failed_path="/flask-api/README.md",
        error_code="insufficient_storage",
        written=("/flask-api/app.py",),
        engine_message="disk is full",
    ),
    "file_browser": lambda f, loc: file_screens.file_browser(f["file_page"], f["ws"].id, loc),
    "file_browser_page_2": lambda f, loc: file_screens.file_browser(
        f["paged_file_page"], f["ws"].id, loc, page_index=1
    ),
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
    # The failed run that offers the missing package: a known module whose pip
    # name differs (PIL -> pillow), and one whose pip name we do not know.
    "exec_missing_mapped": lambda f, loc: run_screens.exec_result(
        ExecResult(1, "", _MISSING_PIL, 120, False),
        f["ws"].id,
        loc,
        missing_offer=pkg_screens.missing_section(_MISSING_PIL, f["ws"].id, loc),
        again_action="exec:again:" + str(f["ws"].id),
    ),
    "exec_missing_unknown": lambda f, loc: run_screens.exec_result(
        ExecResult(1, "", _MISSING_UNKNOWN, 120, False),
        f["ws"].id,
        loc,
        missing_offer=pkg_screens.missing_section(_MISSING_UNKNOWN, f["ws"].id, loc),
        again_action="exec:again:" + str(f["ws"].id),
    ),
    "exec_missing_two": lambda f, loc: run_screens.exec_result(
        ExecResult(1, "", _MISSING_TWO, 120, False),
        f["ws"].id,
        loc,
        missing_offer=pkg_screens.missing_section(_MISSING_TWO, f["ws"].id, loc),
        again_action="exec:again:" + str(f["ws"].id),
    ),
    "job_prompt": lambda f, loc: run_screens.job_prompt(f["ws"].id, loc),
    "job_started": lambda f, loc: run_screens.run_started(f["run"], loc),
    "live_logs_running": lambda f, loc: run_screens.live_logs(f["run"], loc, stdout="Loading data...\nprocessed 100 records\n"),
    "live_logs_finished": lambda f, loc: run_screens.live_logs(f["finished_run"], loc, stdout="done: 500 records\n"),
    "run_history": lambda f, loc: history_screens.run_history(f["run_page"], f["ws"].id, loc, now=NOW),
    "purge_conflict": lambda f, loc: history_screens.purge_conflict(f["ws"].id, loc),
    "terminal_view_running": lambda f, loc: term_screens.terminal_view(
        f["running_session"], f["ws"].id, loc, output="$ ls\nmain.py  README.md\n$ ", meta=f["meta"]
    ),
    "terminal_view_typing": lambda f, loc: term_screens.terminal_view(
        f["running_session"], f["ws"].id, loc, output="$ ls\nmain.py  README.md\n$ ", meta=f["meta"], typing=True
    ),
    "terminal_view_ended": lambda f, loc: term_screens.terminal_view(f["ended_session"], f["ws"].id, loc, output="$ echo hi\nhi\n$ "),
    "terminal_view_failed": lambda f, loc: term_screens.terminal_view(f["failed_session"], f["ws"].id, loc),
    # TASK package-offer-fresh: the same live view, but its output is a failed
    # command whose package is **not** installed — the offer is on screen. Locked
    # in both locales so the offer's shape cannot drift silently.
    "terminal_view_offer": lambda f, loc: term_screens.terminal_view(
        f["running_session"], f["ws"].id, loc, output="$ python3 app.py\n" + _MISSING_FLASK
    ),
    # ... and the same view after the package was installed: no offer. The pair is
    # the golden proof of the task's central rule.
    "terminal_view_offer_installed": lambda f, loc: term_screens.terminal_view(
        f["running_session"],
        f["ws"].id,
        loc,
        output="$ python3 app.py\n" + _MISSING_FLASK,
        installed=frozenset({"flask"}),
    ),
    "terminal_stale": lambda f, loc: term_screens.stale_screen(loc),
    "terminal_ended": lambda f, loc: term_screens.ended_screen(f["ended_session"], f["ws"].id, loc),
    "terminal_command": lambda f, loc: term_screens.command_prompt(f["ws"].id, f["running_session"].id, loc),
    "err_unavailable": lambda f, loc: error_mapping.error_code_screen("unavailable", loc),
    "err_workspace_suspended": lambda f, loc: error_mapping.error_code_screen(
        "workspace_suspended", loc, ws_id=str(f["suspended"].id)
    ),
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
    # The result when no origin is remembered (a restart, a cold callback): no
    # back row, and ``pkg:list`` is the single fallback exit.
    "package_install_ok_no_origin": lambda f, loc: pkg_screens.install_result_screen(
        f["install_ok"], "six requests", f["ws"].id, loc, has_origin=False
    ),
    "package_remove_prompt": lambda f, loc: pkg_screens.remove_prompt(f["ws"].id, loc),
    "package_remove_all_confirm": lambda f, loc: pkg_screens.remove_all_confirm("demo", f["ws"].id, loc),
    # -- Phase 2b: tunnels ---------------------------------------------------
    "tunnel_list": lambda f, loc: tnl_screens.tunnels_screen(
        f["tunnel_list"], f["ws"].id, loc, max_tunnels=2
    ),
    "tunnel_list_empty": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, max_tunnels=2
    ),
    "tunnel_list_at_cap": lambda f, loc: tnl_screens.tunnels_screen(
        f["full_tunnel_list"], f["ws"].id, loc, max_tunnels=2
    ),
    "tunnel_list_stopped": lambda f, loc: tnl_screens.tunnels_screen(
        f["stopped_tunnel_list"], f["ws"].id, loc, max_tunnels=3
    ),
    "tunnel_gate_allow_tunnels": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, allow_tunnels=False, max_tunnels=2
    ),
    "tunnel_gate_allow_net": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, allow_net=False, max_tunnels=2
    ),
    "tunnel_gate_server": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, server_enabled=False, max_tunnels=2
    ),
    "tunnel_gate_suspended": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, status=WorkspaceStatus.SUSPENDED, max_tunnels=2
    ),
    "tunnel_gate_locked": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, status=WorkspaceStatus.LOCKED, max_tunnels=2
    ),
    "tunnel_gate_deleted": lambda f, loc: tnl_screens.tunnels_screen(
        f["empty_tunnel_list"], f["ws"].id, loc, status=WorkspaceStatus.DELETED, max_tunnels=2
    ),
    "tunnel_publish": lambda f, loc: tnl_screens.publish_port_screen(f["ws"].id, loc, detected=[8080]),
    "tunnel_publish_unattributed": lambda f, loc: tnl_screens.publish_port_screen(
        f["ws"].id, loc,
        detected=[tnl_screens.DetectedPort(8080), tnl_screens.DetectedPort(9090, attributed=False)],
    ),
    "tunnel_publish_none": lambda f, loc: tnl_screens.publish_port_screen(f["ws"].id, loc, detected=[]),
    "tunnel_publish_custom": lambda f, loc: tnl_screens.publish_custom_prompt(f["ws"].id, loc),
    "tunnel_publishing": lambda f, loc: tnl_screens.publishing_screen(f["ws"].id, loc),
    "tunnel_already_published": lambda f, loc: tnl_screens.tunnels_screen(
        f["tunnel_list"],
        f["ws"].id,
        loc,
        max_tunnels=2,
        note=catalog.t(
            "tunnel.already_published", loc, port=localize_digits("8080", loc)
        ),
    ),
    "tunnel_publish_confirm_unlistened": lambda f, loc: tnl_screens.publish_confirm(
        9000, f["ws"].id, loc,
        warning="Nothing is listening on this port yet. The link will only work once something listens on it.",
    ),
    "tunnel_limit_reached": lambda f, loc: tnl_screens.publish_refused_screen(
        8082, f["ws"].id, loc, max_tunnels=2
    ),
    "tunnel_timeout": lambda f, loc: tnl_screens.tunnel_timeout_screen(8080, f["ws"].id, loc),
    "tunnel_remove_confirm": lambda f, loc: tnl_screens.remove_confirm(f["tunnel_list"].tunnels[0], f["ws"].id, loc),
    # -- Phase 2c: ports -----------------------------------------------------
    "port_list": lambda f, loc: port_screens.ports_screen(f["port_report"], f["ws"].id, loc),
    "port_list_partial": lambda f, loc: port_screens.ports_screen(f["partial_port_report"], f["ws"].id, loc),
    # -- Phase 2d: metrics ---------------------------------------------------
    "metrics": lambda f, loc: met_screens.metrics_screen(f["metrics"], f["ws"].id, loc),
    "metrics_idle_incomplete": lambda f, loc: met_screens.metrics_screen(f["metrics_idle"], f["ws"].id, loc),
    "metrics_busy_incomplete": lambda f, loc: met_screens.metrics_screen(
        f["metrics_busy_incomplete"], f["ws"].id, loc
    ),
    "metrics_idle_complete": lambda f, loc: met_screens.metrics_screen(
        f["metrics_idle_complete"], f["ws"].id, loc
    ),
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
    "admin_forcejoin": lambda f, loc: admin_forcejoin.forcejoin_screen(_admin_view(loc)),
    "admin_forcejoin_stats": lambda f, loc: admin_forcejoin.stats_screen(
        _admin_view(loc), stats=_forcejoin_stats(), range_key="7d"
    ),
    "admin_forcejoin_stats_empty": lambda f, loc: admin_forcejoin.stats_screen(
        _admin_view(loc),
        stats=_forcejoin_stats_empty(),
        range_key="all",
    ),
    "fj_gate": lambda f, loc: gate_screen(
        GateStatus(
            enabled=True,
            required=tuple(_admin_view(loc).forcejoin_chats[:1]),
            missing=tuple(_admin_view(loc).forcejoin_chats[:1]),
        ),
        loc,
    ),
    "admin_audit": lambda f, loc: admin_audit.audit_screen(_admin_view(loc)),
    "admin_moderation": lambda f, loc: admin_moderation.moderation_screen(_moderation_view(loc)),
    "admin_moderation_empty": lambda f, loc: admin_moderation.moderation_screen(
        AdminView(locale=loc, now=NOW)
    ),
    "admin_ban_user_prompt": lambda f, loc: admin_moderation.ban_prompt(
        "user", "6508129575", "Mohamed #6508129575", loc
    ),
    "admin_ban_user_confirm": lambda f, loc: admin_moderation.ban_prompt(
        "user", "6508129575", "Mohamed #6508129575", loc, reason="Abuse"
    ),
    "admin_ban_ws_prompt": lambda f, loc: admin_moderation.ban_prompt(
        "ws", "ws_01M6HJASCWM9Z1PF755GT89NK4", "demo (ws_01M6HJASCWM9Z1PF755GT89NK4)", loc
    ),
    "admin_ban_ws_confirm": lambda f, loc: admin_moderation.ban_prompt(
        "ws", "ws_01M6HJASCWM9Z1PF755GT89NK4", "demo (ws_01M6HJASCWM9Z1PF755GT89NK4)",
        loc, reason="Abuse of shared storage",
    ),
    "admin_msg_prompt": lambda f, loc: admin_moderation.message_prompt(
        6508129575, "Mohamed #6508129575", loc
    ),
    "admin_msg_sent": lambda f, loc: admin_moderation.message_result(
        6508129575, "Please review the engine rules.", delivered=True,
        message_id=42, reason_code="", locale=loc,
    ),
    "admin_msg_failed": lambda f, loc: admin_moderation.message_result(
        6508129575, "Please review the engine rules.", delivered=False,
        message_id=None, reason_code="forbidden", locale=loc,
    ),
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
    "admin_backup": lambda f, loc: admin_backup.backup_screen(_admin_view(loc)),
    "admin_backup_schedule": lambda f, loc: admin_backup.schedule_screen(_admin_view(loc)),
    "admin_backup_restore_prompt": lambda f, loc: admin_backup.restore_prompt_screen(_admin_view(loc)),
    "admin_backup_restore_confirm_full": lambda f, loc: admin_backup.restore_confirm_screen(
        _admin_view(loc), token="abc123", scope="full", label="cloudbot-backup-20261103T024139Z.zip"
    ),
    "admin_backup_restore_confirm_settings": lambda f, loc: admin_backup.restore_confirm_screen(
        _admin_view(loc), token="abc123", scope="settings", label="cloudbot-settings-20261102T024139Z.json"
    ),
    "admin_backup_restore_refused": lambda f, loc: admin_backup.restore_result_screen(
        _admin_view(loc), ok=False, reason="newer_schema"
    ),
    "admin_backup_restore_done": lambda f, loc: admin_backup.restore_result_screen(
        _admin_view(loc), ok=True, scope="full",
        safety="/var/www/data/cloudbot-pre-restore-20261103T204339Z.db",
    ),
    "admin_broadcast": lambda f, loc: admin_broadcast.broadcast_screen(_admin_view(loc)),
    "admin_broadcast_open": lambda f, loc: admin_broadcast.broadcast_screen(
        AdminView(locale=loc, now=NOW, broadcast=_broadcast_open_view())
    ),
    "admin_broadcast_compose": lambda f, loc: admin_broadcast.compose_prompt(_admin_view(loc)),
    "admin_broadcast_audience": lambda f, loc: admin_broadcast.audience_screen(
        _admin_view(loc), _broadcast_view(), source=_broadcast_run().source
    ),
    "admin_broadcast_preview": lambda f, loc: admin_broadcast.preview_screen(
        _admin_view(loc), source=_broadcast_run().source, family="all",
        count=1200, ceiling=5000, over_ceiling=False,
    ),
    "admin_broadcast_preview_over_ceiling": lambda f, loc: admin_broadcast.preview_screen(
        _admin_view(loc), source=_broadcast_run().source, family="all",
        count=9000, ceiling=5000, over_ceiling=True,
    ),
    "admin_broadcast_progress": lambda f, loc: admin_broadcast.progress_screen(
        _admin_view(loc), _broadcast_run(4, status="sending", sent=12, failed=0)
    ),
    "admin_broadcast_result": lambda f, loc: admin_broadcast.result_screen(
        _admin_view(loc), _broadcast_result()
    ),
    "admin_broadcast_history": lambda f, loc: admin_broadcast.history_screen(
        _admin_view(loc), _broadcast_view().history
    ),
}


# -- sections: a first-class grouping ------------------------------------------
@dataclass(frozen=True)
class Section:
    """A named group of screens. ``title_key`` is a catalog key, so the tool shows
    it in the admin's own language like every other string."""

    key: str
    title_key: str
    order: int


#: The sections, in navigation order. A screen is assigned to exactly one.
SECTIONS: tuple[Section, ...] = (
    Section("menu", "dev.section.menu", 10),
    Section("wallet", "dev.section.wallet", 15),
    Section("language", "dev.section.language", 20),
    Section("workspaces", "dev.section.workspaces", 30),
    Section("files", "dev.section.files", 40),
    Section("run", "dev.section.run", 50),
    Section("terminal", "dev.section.terminal", 60),
    Section("tunnels", "dev.section.tunnels", 70),
    Section("ports", "dev.section.ports", 80),
    Section("metrics", "dev.section.metrics", 90),
    Section("packages", "dev.section.packages", 100),
    Section("processes", "dev.section.processes", 110),
    Section("archives", "dev.section.archives", 120),
    Section("admin", "dev.section.admin", 130),
    Section("errors", "dev.section.errors", 140),
)

_SECTION_BY_KEY = {s.key: s for s in SECTIONS}


def _section_of(name: str) -> str:
    if name.startswith("wallet"):
        return "wallet"
    if name.startswith("admin_"):
        return "admin"
    if name.startswith("fj_"):
        return "menu"
    if name.startswith("err_"):
        return "errors"
    if name.startswith("language"):
        return "language"
    if name in ("menu", "menu_empty", "menu_inside", "help", "help_inside", "settings", "settings_inside"):
        return "menu"
    if name.startswith("ws_"):
        return "workspaces"
    if name.startswith("tpl_"):
        return "workspaces"
    if name.startswith("file_"):
        return "files"
    if name.startswith(("run_", "exec_", "job_", "live_logs_", "purge_")):
        return "run"
    if name.startswith("terminal"):
        return "terminal"
    if name.startswith("tunnel"):
        return "tunnels"
    if name.startswith("port_"):
        return "ports"
    if name.startswith("metrics"):
        return "metrics"
    if name.startswith("package_"):
        return "packages"
    if name.startswith("process"):
        return "processes"
    if name.startswith("archive_"):
        return "archives"
    raise KeyError(f"screen {name!r} is not assigned to any section")


@dataclass(frozen=True)
class ScreenSpec:
    name: str
    section: str
    build: ScreenBuilder


_REGISTRY: dict[str, ScreenSpec] = {
    name: ScreenSpec(name=name, section=_section_of(name), build=builder)
    for name, builder in SCREEN_BUILDERS.items()
}


# -- the small catalog API -----------------------------------------------------
def sections() -> tuple[Section, ...]:
    return SECTIONS


def section_keys() -> tuple[str, ...]:
    return tuple(s.key for s in SECTIONS)


def has_section(key: str) -> bool:
    return key in _SECTION_BY_KEY


def section_title(key: str, locale: Locale) -> str:
    section = _SECTION_BY_KEY.get(key)
    if section is None:
        return key
    return catalog.t(section.title_key, locale)


def screens_in(section: str) -> tuple[ScreenSpec, ...]:
    return tuple(
        spec for spec in _REGISTRY.values() if spec.section == section
    )


def screen_names() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def section_of(name: str) -> str:
    return _REGISTRY[name].section


def has(name: str) -> bool:
    return name in _REGISTRY


def register(name: str, section: str, builder: ScreenBuilder) -> ScreenSpec:
    """Add a screen at runtime. The extension point the tests use to inject a
    deliberately broken screen without touching the frozen catalog."""
    if not has_section(section):
        raise KeyError(f"unknown section {section!r}")
    spec = ScreenSpec(name=name, section=section, build=builder)
    _REGISTRY[name] = spec
    return spec


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def build(name: str, locale: Locale, *, fixtures_data: dict[str, Any] | None = None) -> Screen:
    """Render the named screen in ``locale`` from the fixed fixtures."""
    spec = _REGISTRY[name]
    return spec.build(fixtures_data if fixtures_data is not None else fixtures(), locale)


# -- verdicts: the ONE walk shared by the tool and the contract tests ----------
#: verdict strings. ``handled``/``no_handler``/``stale``/``unparseable`` are for
#: callback buttons; ``url`` is a plain link button (no callback to run).
HANDLED = "handled"
NO_HANDLER = "no_handler"
STALE = "stale"
UNPARSEABLE = "unparseable"
URL = "url"
RENDER_ERROR = "render_error"

CALLBACK_VERDICTS: tuple[str, ...] = (HANDLED, NO_HANDLER, STALE, UNPARSEABLE)


@dataclass(frozen=True)
class ButtonInfo:
    index: int
    text: str
    data: str | None
    url: str | None
    style: str | None
    disabled: bool


@dataclass(frozen=True)
class ButtonVerdict:
    button: ButtonInfo
    verdict: str


@dataclass(frozen=True)
class ScreenVerdict:
    name: str
    section: str
    locale: Locale
    buttons: tuple[ButtonVerdict, ...]
    error: str | None = None


@dataclass(frozen=True)
class WalkReport:
    locale: Locale
    screens: int
    buttons: int
    totals: dict[str, int]
    #: ``(screen name, verdict, button index, button text)`` for every non-handled.
    failures: tuple[tuple[str, str, int, str], ...]


def button_infos(screen: Screen) -> tuple[ButtonInfo, ...]:
    """Every button of a screen as metadata, in row-major order."""
    markup = screen.markup
    if markup is None:
        return ()
    rows = getattr(markup, "inline_keyboard", None)
    if rows is None:
        rows = getattr(markup, "keyboard", None) or []
    infos: list[ButtonInfo] = []
    for row in rows:
        for button in row:
            infos.append(
                ButtonInfo(
                    index=len(infos),
                    text=button.text,
                    data=getattr(button, "callback_data", None),
                    url=getattr(button, "url", None),
                    style=getattr(button, "style", None),
                    disabled=getattr(button, "disabled", None) is not None,
                )
            )
    return tuple(infos)


def _is_unparseable(data: str) -> bool:
    """True when the data cannot be a ``prefix:name[:key...]`` callback."""
    if not isinstance(data, str) or not data:
        return True
    parts = data.split(":")
    return len(parts) < 2 or not parts[0] or not parts[1]


def is_stale(data: str) -> bool:
    """True when a token callback's key parts are gone from this process.

    Inline callbacks (the common case) are restart-proof and never stale; only a
    callback that fell back to the in-memory token registry can go stale, and the
    signature is ``decode`` returning an empty key for a ``~token`` payload.
    """
    parts = data.split(":")
    rest = parts[2:]
    if len(rest) == 1 and rest[0].startswith(TOKEN_MARKER):
        return len(codec.decode(data).key) == 0
    return False


def classify(info: ButtonInfo, *, claimed: bool) -> str:
    """Classify one button against the real dispatcher's routing (``claimed``)."""
    if info.data is None:
        return URL if info.url is not None else NO_HANDLER
    if _is_unparseable(info.data):
        return UNPARSEABLE
    if is_stale(info.data):
        return STALE
    return HANDLED if claimed else NO_HANDLER


#: An async predicate: does any handler in the real dispatcher claim ``data``?
Claims = Callable[[str], Awaitable[bool]]


async def walk_screen(
    name: str,
    locale: Locale,
    *,
    claims: Claims,
    fixtures_data: dict[str, Any] | None = None,
) -> ScreenVerdict:
    """Render one screen and classify every button. A render error is a finding,
    never a crash: the walk must survive a broken screen."""
    section = section_of(name)
    try:
        screen = build(name, locale, fixtures_data=fixtures_data)
    except Exception as exc:  # noqa: BLE001 - a broken screen is reported, not fatal
        return ScreenVerdict(
            name=name, section=section, locale=locale, buttons=(), error=f"{type(exc).__name__}: {exc}"
        )
    verdicts: list[ButtonVerdict] = []
    for info in button_infos(screen):
        claimed = False
        if info.data is not None:
            try:
                claimed = await claims(info.data)
            except Exception:  # noqa: BLE001 - a raising filter is a non-claim
                claimed = False
        verdicts.append(ButtonVerdict(button=info, verdict=classify(info, claimed=claimed)))
    return ScreenVerdict(name=name, section=section, locale=locale, buttons=tuple(verdicts))


def _tally(verdicts: tuple[ScreenVerdict, ...], locale: Locale) -> WalkReport:
    totals = {v: 0 for v in CALLBACK_VERDICTS}
    totals[URL] = 0
    totals[RENDER_ERROR] = 0
    buttons = 0
    failures: list[tuple[str, str, int, str]] = []
    for screen in verdicts:
        if screen.error is not None:
            totals[RENDER_ERROR] += 1
            failures.append((screen.name, RENDER_ERROR, -1, screen.error))
            continue
        for verdict in screen.buttons:
            buttons += 1
            totals[verdict.verdict] = totals.get(verdict.verdict, 0) + 1
            if verdict.verdict != HANDLED and verdict.verdict != URL:
                failures.append(
                    (screen.name, verdict.verdict, verdict.button.index, verdict.button.text)
                )
    return WalkReport(
        locale=locale,
        screens=len(verdicts),
        buttons=buttons,
        totals=totals,
        failures=tuple(failures),
    )


async def walk_section(
    section: str,
    locales: tuple[Locale, ...] = (Locale.EN,),
    *,
    claims: Claims,
    fixtures_data: dict[str, Any] | None = None,
) -> WalkReport:
    verdicts: list[ScreenVerdict] = []
    for locale in locales:
        for spec in screens_in(section):
            verdicts.append(
                await walk_screen(spec.name, locale, claims=claims, fixtures_data=fixtures_data)
            )
    return _tally(tuple(verdicts), locales[0] if locales else Locale.EN)


async def walk_catalog(
    locales: tuple[Locale, ...] = (Locale.EN,),
    *,
    claims: Claims,
    fixtures_data: dict[str, Any] | None = None,
) -> WalkReport:
    verdicts: list[ScreenVerdict] = []
    for locale in locales:
        for spec in _REGISTRY.values():
            verdicts.append(
                await walk_screen(spec.name, locale, claims=claims, fixtures_data=fixtures_data)
            )
    return _tally(tuple(verdicts), locales[0] if locales else Locale.EN)


__all__ = [
    "ButtonInfo",
    "ButtonVerdict",
    "CALLBACK_VERDICTS",
    "HANDLED",
    "NEVER_RTL",
    "NO_HANDLER",
    "NOW",
    "RENDER_ERROR",
    "SCREEN_BUILDERS",
    "SECTIONS",
    "STALE",
    "Section",
    "ScreenSpec",
    "ScreenVerdict",
    "UNPARSEABLE",
    "URL",
    "WalkReport",
    "build",
    "button_infos",
    "classify",
    "fixtures",
    "has",
    "has_section",
    "is_stale",
    "register",
    "unregister",
    "screen_names",
    "section_keys",
    "section_of",
    "section_title",
    "screens_in",
    "sections",
    "walk_catalog",
    "walk_screen",
    "walk_section",
]
