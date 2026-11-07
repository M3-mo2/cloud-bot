"""``FakeEngine`` — deterministic fixtures for DEV mode and unit tests.

Serves a couple of workspaces, a sample tree, and a run that advances
``queued → running → exited`` with synthetic logs. Implements ``EnginePort`` so
every screen can be exercised with no engine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from cloudbot.core.errors import EngineRefused
from cloudbot.domain.entities import FileEntry, ResourceUsage, Run, TerminalSession, Workspace
from cloudbot.domain.meta import EngineMeta, Profiles, ProfileInfo, SessionConfig, Tunnels
from cloudbot.domain.value_objects import (
    ArchiveEntry,
    ArchiveResult,
    ExecResult,
    FileContent,
    FilePage,
    InstallResult,
    LifecycleResult,
    Limits,
    LogChunk,
    Package,
    PackageList,
    PortEntry,
    PortReport,
    ProcessEntry,
    ProcessList,
    ProcessStopResult,
    RunPage,
    RunStatus,
    SessionPage,
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

_NOW = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)


def _ws(ws_id: str, name: str, status: WorkspaceStatus, active: int = 0) -> Workspace:
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
            created_at=_NOW.isoformat(),
            runs_total=3,
            runs_active=active,
            disk_used_bytes=2_400_000,
            disk_limit_bytes=268_435_456,
            profile="python",
        ),
        created_at=_NOW,
        last_activity_at=_NOW - timedelta(minutes=2),
    )


class FakeEngine:
    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._files: dict[str, list[FileEntry]] = {}
        self._runs: dict[str, Run] = {}
        self._run_logs: dict[str, tuple[str, str]] = {}
        self._run_ticks: dict[str, int] = {}
        self._sessions: dict[str, TerminalSession] = {}
        self._session_output: dict[str, str] = {}
        self._tunnels: dict[str, Tunnel] = {}
        self._seq = 0
        #: The engine's per-workspace tunnel cap. Tests override it to prove
        #: nothing hardcodes the number; the real value lives in ``/v1/meta``.
        self.max_tunnels = 2
        #: Every ``extend_subscription`` the bot made, as ``(ws_id, expires_at)``.
        self.subscription_extensions: list[tuple[str, datetime]] = []
        self._seed_workspace("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE, active=1)
        self._seed_workspace("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED)

    # -- fixtures ------------------------------------------------------------
    def _seed_workspace(
        self, ws_id: str, name: str, status: WorkspaceStatus, active: int = 0
    ) -> None:
        self._workspaces[ws_id] = _ws(ws_id, name, status, active=active)
        self._files[ws_id] = [
            FileEntry("/src", "src", "dir", None, "drwxr-xr-x", _NOW, None),
            FileEntry("/data", "data", "dir", None, "drwxr-xr-x", _NOW, None),
            FileEntry(
                "/main.py", "main.py", "file", 812, "-rw-r--r--", _NOW, "python3"
            ),
            FileEntry("/README.md", "README.md", "file", 1200, "-rw-r--r--", _NOW, None),
            FileEntry(
                "/archive.zip",
                "archive.zip",
                "file",
                4_100_000,
                "-rw-r--r--",
                _NOW,
                None,
            ),
        ]

    def add_workspace(self, name: str, *, active: int = 0) -> Workspace:
        self._seq += 1
        ws_id = f"ws_FAKE{self._seq:020d}"
        ws = _ws(ws_id, name, WorkspaceStatus.ACTIVE, active=active)
        self._workspaces[ws_id] = ws
        self._files[ws_id] = []
        return ws

    # -- EnginePort ----------------------------------------------------------
    async def aclose(self) -> None:
        return None

    async def meta(self, refresh: bool = False) -> EngineMeta:
        return EngineMeta(
            version="0.0.20",
            api_version="v1",
            runtimes=("python3",),
            interpreters=("bash", "node", "perl", "python3"),
            runnable_extensions=(".py", ".sh", ".js"),
            profiles=Profiles(
                default="python",
                profiles=(ProfileInfo(name="python", description="Python 3 toolchain"),),
            ),
            limits_defaults=Limits(),
            limits_maxima=Limits(
                cpu_cores=8, memory_mb=8192, disk_mb=51200, max_wall_time_sec=3600,
                allow_net=True, allow_tunnels=True,
            ),
            tunnels=Tunnels(
                enabled=True,
                ready=True,
                max_per_workspace=self.max_tunnels,
                url_timeout_sec=45,
            ),
        )

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def ready(self) -> dict[str, Any]:
        return {"status": "ready"}

    async def create_workspace(
        self,
        *,
        name: str,
        profile: str | None,
        limits: Limits | None,
        subscription: dict[str, Any] | None = None,
    ) -> Workspace:
        ws = self.add_workspace(name)
        if limits is not None:
            ws = Workspace(
                id=ws.id,
                owner_tg_id=ws.owner_tg_id,
                name=ws.name,
                profile=profile or ws.profile,
                status=ws.status,
                limits=limits,
                summary=ws.summary,
                created_at=ws.created_at,
                last_activity_at=ws.last_activity_at,
            )
            self._workspaces[str(ws.id)] = ws
        return ws

    async def extend_subscription(
        self, ws_id: WorkspaceId, *, expires_at: datetime
    ) -> Workspace:
        """Record the term the bot asked for; the fake never locks itself."""
        self.subscription_extensions.append((str(ws_id), expires_at))
        return await self.get_workspace(ws_id)

    async def list_workspaces(self) -> list[Workspace]:
        return list(self._workspaces.values())

    async def get_workspace(self, ws_id: WorkspaceId) -> Workspace:
        ws = self._workspaces.get(str(ws_id))
        if ws is None:
            raise EngineRefused("not_found")
        return ws

    async def patch_workspace(
        self, ws_id: WorkspaceId, *, name: str | None = None, limits: Limits | None = None
    ) -> Workspace:
        ws = await self.get_workspace(ws_id)
        new = Workspace(
            id=ws.id,
            owner_tg_id=ws.owner_tg_id,
            name=name or ws.name,
            profile=ws.profile,
            status=ws.status,
            limits=limits or ws.limits,
            summary=ws.summary,
            created_at=ws.created_at,
            last_activity_at=ws.last_activity_at,
        )
        self._workspaces[str(ws_id)] = new
        return new

    def _set_status(self, ws_id: WorkspaceId, status: WorkspaceStatus) -> Workspace:
        ws = self._workspaces[str(ws_id)]
        new = Workspace(
            id=ws.id,
            owner_tg_id=ws.owner_tg_id,
            name=ws.name,
            profile=ws.profile,
            status=status,
            limits=ws.limits,
            summary=ws.summary,
            created_at=ws.created_at,
            last_activity_at=ws.last_activity_at,
        )
        self._workspaces[str(ws_id)] = new
        return new

    async def suspend_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        self._set_status(ws_id, WorkspaceStatus.SUSPENDED)
        return LifecycleResult(changed=True, stopped_run_count=1)

    async def resume_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        self._set_status(ws_id, WorkspaceStatus.ACTIVE)
        return LifecycleResult(changed=True)

    async def stop_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        return LifecycleResult(
            changed=True, stopped_run_count=2, stopped_session_count=1, stopped_process_count=0
        )

    async def delete_workspace(self, ws_id: WorkspaceId) -> None:
        if str(ws_id) not in self._workspaces:
            raise EngineRefused("not_found")
        del self._workspaces[str(ws_id)]

    async def workspace_usage(self, ws_id: WorkspaceId) -> ResourceUsage:
        ws = await self.get_workspace(ws_id)
        return ResourceUsage(
            disk_bytes=ws.summary.disk_used_bytes,
            disk_limit_bytes=ws.summary.disk_limit_bytes,
            disk_percent=1.0,
            runs_total=ws.summary.runs_total,
            active_runs=ws.summary.runs_active,
            max_runs=ws.limits.max_runs,
        )

    async def list_files(
        self, ws_id: WorkspaceId, path: str, *, offset: int = 0, limit: int = 20
    ) -> FilePage:
        await self.get_workspace(ws_id)
        entries = self._files.get(str(ws_id), [])
        if path == "/":
            page = entries
        else:
            prefix = path.rstrip("/") + "/"
            page = [e for e in entries if e.path.startswith(prefix)]
        total = len(page)
        window = page[offset : offset + limit]
        return FilePage(
            path=path,
            entries=tuple(window),
            count=len(window),
            total=total,
            truncated=total > offset + limit,
            offset=offset,
            limit=limit,
        )

    async def stat_file(self, ws_id: WorkspaceId, path: str) -> FileEntry:
        for entry in self._files.get(str(ws_id), []):
            if entry.path == path:
                return entry
        raise EngineRefused("not_found")

    async def read_file(
        self,
        ws_id: WorkspaceId,
        path: str,
        *,
        encoding: str | None = None,
        line_range: tuple[int, int] | None = None,
        meta: bool = False,
    ) -> FileContent:
        if path.endswith(".zip"):
            return FileContent(
                path=path,
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
        if path.endswith("main.py"):
            body = (
                'def greet(name):\n    print(f"Hello, {name}")\n\n'
                'if __name__ == "__main__":\n    greet("world")\n'
            )
        else:
            body = "hello\nworld\n"
        lines = body.splitlines()
        start = line_range[0] if line_range else 1
        end = line_range[1] if line_range else len(lines)
        window = lines[start - 1 : end]
        return FileContent(
            path=path,
            content="\n".join(window) + ("\n" if window else ""),
            encoding="utf-8",
            size=len(body),
            total_size=len(body),
            whole_file=line_range is None,
            sha256="9f2c4e7a1b3d5f6089a1",
            content_type="text/x-python" if path.endswith(".py") else "text/plain",
            truncated=line_range is not None,
            binary=False,
            start_line=start,
            end_line=start + len(window) - 1,
            total_lines=len(lines),
        )

    async def write_file(
        self, ws_id: WorkspaceId, path: str, content: str, *, encoding: str = "text"
    ) -> None:
        await self.get_workspace(ws_id)

    async def delete_file(self, ws_id: WorkspaceId, path: str) -> None:
        await self.get_workspace(ws_id)

    async def mkdir(self, ws_id: WorkspaceId, path: str) -> None:
        await self.get_workspace(ws_id)
        self._files.setdefault(str(ws_id), []).append(
            FileEntry(path, path.rstrip("/").rsplit("/", 1)[-1], "dir", None, "drwxr-xr-x", _NOW, None)
        )

    async def rename(self, ws_id: WorkspaceId, src: str, dst: str) -> None:
        await self.get_workspace(ws_id)

    async def upload_file(
        self, ws_id: WorkspaceId, path: str, filename: str, stream: AsyncIterator[bytes]
    ) -> None:
        await self.get_workspace(ws_id)
        async for _ in stream:
            pass

    async def exec(
        self,
        ws_id: WorkspaceId,
        cmd: list[str] | str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        ws = await self.get_workspace(ws_id)
        if ws.status is WorkspaceStatus.SUSPENDED:
            raise EngineRefused("workspace_suspended")
        return ExecResult(
            exit_code=0,
            stdout="hello from main.py\nargs: ['a', 'b']\n",
            stderr="",
            duration_ms=68,
            truncated=False,
        )

    async def create_run(
        self,
        ws_id: WorkspaceId,
        *,
        kind: str,
        file: str | None = None,
        argv: list[str] | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        interpreter: str | None = None,
        timeout_sec: int | None = None,
        no_timeout: bool = False,
    ) -> Run:
        await self.get_workspace(ws_id)
        self._seq += 1
        run_id = f"run_FAKE{self._seq:020d}"
        run = Run(
            id=run_id,  # type: ignore[arg-type]
            workspace_id=ws_id,
            kind=kind,  # type: ignore[arg-type]
            argv=list(argv or ([interpreter or "python3", file or ""])),
            cwd=cwd or "/",
            status=RunStatus.QUEUED,
            exit_code=None,
            interpreter=interpreter or "python3",
            timeout_sec=timeout_sec,
            started_at=_NOW,
            ended_at=None,
        )
        self._runs[run_id] = run
        self._run_ticks[run_id] = 0
        self._run_logs[run_id] = ("", "")
        return run

    async def list_runs(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> RunPage:
        runs = [r for r in self._runs.values() if str(r.workspace_id) == str(ws_id)]
        if status:
            runs = [r for r in runs if r.status.value == status]
        if kind:
            runs = [r for r in runs if r.kind == kind]
        total = len(runs)
        return RunPage(runs=tuple(runs[offset : offset + limit]), total=total, limit=limit, offset=offset)

    async def get_run(self, ws_id: WorkspaceId, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise EngineRefused("not_found")
        return self._advance(run)

    def _advance(self, run: Run) -> Run:
        """Progress a fake run one tick: queued -> running -> exited."""
        ticks = self._run_ticks.get(str(run.id), 0)
        if ticks == 0:
            new = self._replace(run, status=RunStatus.RUNNING)
            self._run_logs[str(run.id)] = ("Loading data...\nprocessed 100 records\n", "")
        elif ticks == 1:
            new = self._replace(
                run, status=RunStatus.EXITED, exit_code=0, ended_at=_NOW + timedelta(seconds=4)
            )
            self._run_logs[str(run.id)] = ("done: 500 records\n", "")
        else:
            new = run
        self._run_ticks[str(run.id)] = ticks + 1
        self._runs[str(run.id)] = new
        return new

    @staticmethod
    def _replace(run: Run, **changes: Any) -> Run:
        data = dict(run.__dict__)
        data.update(changes)
        return Run(**data)

    async def run_logs(
        self,
        ws_id: WorkspaceId,
        run_id: str,
        *,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
    ) -> LogChunk:
        stdout, stderr = self._run_logs.get(run_id, ("", ""))
        data = stdout if stream == "stdout" else stderr
        chunk = data[offset : offset + limit]
        return LogChunk(
            data=chunk, offset=offset, next=offset + len(chunk), eof=True, truncated=False
        )

    async def stop_run(self, ws_id: WorkspaceId, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            self._runs[run_id] = self._replace(run, status=RunStatus.KILLED)

    async def purge_runs(self, ws_id: WorkspaceId) -> int:
        deleted = [k for k, r in self._runs.items() if str(r.workspace_id) == str(ws_id)]
        for k in deleted:
            del self._runs[k]
        return len(deleted)

    async def delete_run(self, ws_id: WorkspaceId, run_id: str) -> None:
        self._runs.pop(run_id, None)

    # -- sessions ------------------------------------------------------------
    async def open_session(
        self,
        ws_id: WorkspaceId,
        *,
        shell: str | None = None,
        cwd: str | None = None,
        cols: int = 80,
        rows: int = 24,
        timeout_sec: int | None = None,
    ) -> TerminalSession:
        ws = await self.get_workspace(ws_id)
        if ws.status is WorkspaceStatus.SUSPENDED:
            raise EngineRefused("workspace_suspended")
        live = [
            s
            for s in self._sessions.values()
            if str(s.workspace_id) == str(ws_id) and s.status is SessionStatus.RUNNING
        ]
        if len(live) >= 4:
            raise EngineRefused("too_many_sessions")
        self._seq += 1
        ses_id = f"ses_FAKE{self._seq:020d}"
        session = TerminalSession(
            id=ses_id,
            workspace_id=ws_id,
            status=SessionStatus.RUNNING,
            cols=cols,
            rows=rows,
            created_at=_NOW,
        )
        self._sessions[ses_id] = session
        self._session_output[ses_id] = ""
        return session

    async def list_sessions(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SessionPage:
        sessions = [s for s in self._sessions.values() if str(s.workspace_id) == str(ws_id)]
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        total = len(sessions)
        return SessionPage(
            sessions=tuple(sessions[offset : offset + limit]),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        return session

    async def session_input(self, ws_id: WorkspaceId, ses_id: str, data: str) -> None:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        if session.status is not SessionStatus.RUNNING:
            raise EngineRefused("session_ended", details={"status": session.status.value})
        self._session_output[ses_id] = self._session_output.get(ses_id, "") + f"$ {data}"

    async def session_output(
        self, ws_id: WorkspaceId, ses_id: str, *, offset: int = 0, limit: int = 65536
    ) -> LogChunk:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        if session.status is SessionStatus.KILLED:
            raise EngineRefused("session_ended", details={"status": "killed"})
        data = self._session_output.get(ses_id, "")
        chunk = data[offset : offset + limit]
        return LogChunk(
            data=chunk, offset=offset, next=offset + len(chunk), eof=True, truncated=False
        )

    async def session_resize(
        self, ws_id: WorkspaceId, ses_id: str, *, cols: int, rows: int
    ) -> None:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        self._sessions[ses_id] = TerminalSession(
            id=session.id,
            workspace_id=session.workspace_id,
            status=session.status,
            cols=cols,
            rows=rows,
            created_at=session.created_at,
            reason=session.reason,
            diagnostic=session.diagnostic,
        )

    async def session_signal(self, ws_id: WorkspaceId, ses_id: str, signal: str) -> None:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        if signal not in ("INT", "TERM", "QUIT", "HUP"):
            raise EngineRefused("unprocessable", details={"field": "signal"})
        if session.status is not SessionStatus.RUNNING:
            raise EngineRefused("session_ended", details={"status": session.status.value})

    async def close_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession:
        session = self._sessions.get(ses_id)
        if session is None:
            raise EngineRefused("not_found")
        closed = TerminalSession(
            id=session.id,
            workspace_id=session.workspace_id,
            status=SessionStatus.KILLED,
            cols=session.cols,
            rows=session.rows,
            created_at=session.created_at,
            reason="user_closed",
        )
        self._sessions[ses_id] = closed
        return closed

    # -- packages (Phase 2a) -------------------------------------------------
    async def list_packages(self, ws_id: WorkspaceId) -> PackageList:
        await self.get_workspace(ws_id)
        return PackageList(
            packages=(Package(name="six", version="1.16.0"),),
            count=1,
            truncated=False,
            shared_enabled=True,
            shared_available=False,
            shared_path="",
            private_path="/opt/python/private",
        )

    async def install_packages(self, ws_id: WorkspaceId, packages: list[str]) -> InstallResult:
        ws = await self.get_workspace(ws_id)
        if not ws.limits.allow_net:
            return InstallResult(
                exit_code=1,
                output="ERROR: Could not find a version that satisfies the requirement ...\n"
                "Could not fetch URL https://pypi.org/simple/: connection error",
                stdout="",
                duration_ms=1200,
                timed_out=False,
                truncated=False,
                target="/opt/python/private",
            )
        names = " ".join(packages)
        return InstallResult(
            exit_code=0,
            output=f"Successfully installed {names}\n",
            stdout=f"Successfully installed {names}\n",
            duration_ms=10_000,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        )

    async def remove_package(self, ws_id: WorkspaceId, name: str) -> None:
        await self.get_workspace(ws_id)

    async def remove_all_packages(self, ws_id: WorkspaceId) -> None:
        await self.get_workspace(ws_id)

    # -- tunnels (Phase 2b) --------------------------------------------------
    async def list_tunnels(self, ws_id: WorkspaceId) -> TunnelList:
        await self.get_workspace(ws_id)
        tunnels = [t for t in self._tunnels.values() if t.workspace_id == str(ws_id)]
        return TunnelList(tunnels=tuple(tunnels))

    async def create_tunnel(self, ws_id: WorkspaceId, port: int) -> Tunnel:
        ws = await self.get_workspace(ws_id)
        if not ws.limits.allow_net:
            raise EngineRefused(
                "tunnels_disabled",
                details={"config": "limits.allow_net", "field": "allow_net"},
            )
        if not ws.limits.allow_tunnels:
            raise EngineRefused(
                "tunnels_disabled",
                details={"config": "limits.allow_tunnels", "field": "allow_tunnels"},
            )
        # The per-workspace cap the real engine enforces (``tunnels.max_per_workspace``).
        existing = [t for t in self._tunnels.values() if t.workspace_id == str(ws_id)]
        if len(existing) >= self.max_tunnels:
            raise EngineRefused(
                "too_many_tunnels",
                details={"max_per_workspace": self.max_tunnels},
            )
        self._seq += 1
        tunnel = Tunnel(
            id=f"tnl_FAKE{self._seq:020d}",
            workspace_id=str(ws_id),
            port=port,
            url=f"https://fake-{self._seq}.trycloudflare.com",
            status="active",
            created_at=_NOW,
        )
        self._tunnels[tunnel.id] = tunnel
        return tunnel

    async def delete_tunnel(self, ws_id: WorkspaceId, tunnel_id: str) -> None:
        if tunnel_id not in self._tunnels:
            raise EngineRefused("not_found")
        del self._tunnels[tunnel_id]

    # -- ports (Phase 2c) ----------------------------------------------------
    async def list_ports(self, ws_id: WorkspaceId) -> PortReport:
        await self.get_workspace(ws_id)
        return PortReport(
            workspace_id=str(ws_id),
            ports=(
                PortEntry(
                    port=8080,
                    protocol="tcp",
                    address="0.0.0.0",
                    scope="wildcard",
                    state="LISTEN",
                    attributed=True,
                    pid=1234,
                    kind="run",
                    label="python3 -m http.server 8080",
                    argv=("python3", "-m", "http.server", "8080"),
                    owners=(1234,),
                    inode=99,
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
                    pid=4321,
                    kind="run",
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

    # -- metrics (Phase 2d) --------------------------------------------------
    async def workspace_metrics(self, ws_id: WorkspaceId) -> WorkspaceMetrics:
        ws = await self.get_workspace(ws_id)
        active = ws.summary.runs_active
        return WorkspaceMetrics(
            workspace_id=str(ws_id),
            timestamp=_NOW.isoformat(),
            interval_seconds=3,
            ram_used_bytes=18_874_368 if active else 0,
            ram_limit_bytes=ws.limits.memory_mb * 1024 * 1024,
            ram_percent=3.5 if active else 0.0,
            ram_complete=True,
            disk_used_bytes=ws.summary.disk_used_bytes,
            disk_limit_bytes=ws.summary.disk_limit_bytes,
            disk_percent=0.9,
            disk_complete=True,
            write_rate_limit_mb_s=0,
            live_runs=active,
            live_cgroups=active,
        )

    # -- processes (Phase 2e) ------------------------------------------------
    async def list_processes(self, ws_id: WorkspaceId) -> ProcessList:
        await self.get_workspace(ws_id)
        processes = (
            ProcessEntry(
                id="proc_0123456789abcdef",
                pid=179173,
                ppid=179172,
                argv=("python3", "bot.py"),
                kind="session",
                source_id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
                started_at=_NOW,
            ),
            ProcessEntry(
                id="proc_fedcba9876543210",
                pid=179200,
                ppid=1,
                argv=("sleep", "600"),
                kind="detached",
                source_id="",
                started_at=_NOW,
            ),
        )
        return ProcessList(processes=processes, count=len(processes))

    async def stop_process(
        self, ws_id: WorkspaceId, process_id: str
    ) -> ProcessStopResult:
        await self.get_workspace(ws_id)
        # A known fake id is stopped; anything else reads as already gone.
        if process_id == "proc_0123456789abcdef":
            return ProcessStopResult(
                process_id=process_id, status="stopped", stopped=True, stopped_at=_NOW
            )
        return ProcessStopResult(
            process_id=process_id, status="gone", stopped=False, stopped_at=_NOW
        )

    # -- archives (Phase 2g) -------------------------------------------------
    async def upload_and_extract(
        self,
        ws_id: WorkspaceId,
        *,
        filename: str,
        data: bytes,
        path: str = "/",
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> ArchiveResult:
        ws = await self.get_workspace(ws_id)
        return self._fake_archive_result(
            ws, path or "/", dry_run=dry_run, real_size=len(data)
        )

    async def extract_archive(
        self,
        ws_id: WorkspaceId,
        *,
        path: str,
        dest: str = "",
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> ArchiveResult:
        ws = await self.get_workspace(ws_id)
        return self._fake_archive_result(ws, dest or "/", dry_run=dry_run, real_size=15)

    async def export_archive(self, ws_id: WorkspaceId, path: str) -> bytes:
        await self.get_workspace(ws_id)
        # Minimal valid ZIP local-file-header magic so a caller can sanity-check.
        return b"PK\x03\x04" + b"\x00" * 16

    def _fake_archive_result(
        self, ws: Workspace, destination: str, *, dry_run: bool, real_size: int
    ) -> ArchiveResult:
        """Model the honest split: a dry run reports what *would* extract but
        writes **nothing** (``bytes_written`` 0, no files added); a real extract
        adds the entries and reports the bytes written."""
        base = destination.rstrip("/")
        file_entry = ArchiveEntry(
            name="src/main.py",
            path=f"{base}/src/main.py" or "/src/main.py",
            type="file",
            size=15,
            mode="-rw-r--r--",
            rejected=False,
            skipped=False,
        )
        junk_entry = ArchiveEntry(
            name="__MACOSX/._x",
            path=f"{base}/__MACOSX/._x",
            type="file",
            size=0,
            mode="-rw-r--r--",
            rejected=False,
            skipped=True,
            skip_reason="junk",
        )
        if not dry_run:
            self._files.setdefault(str(ws.id), []).append(
                FileEntry(
                    file_entry.path,
                    file_entry.name.rsplit("/", 1)[-1],
                    "file",
                    file_entry.size,
                    file_entry.mode,
                    _NOW,
                    None,
                )
            )
        return ArchiveResult(
            format="zip",
            destination=destination,
            entries_total=2,
            # A dry run reports how many entries *would* be extracted (the real
            # engine reports the same count); the honesty signal for "nothing
            # written" is ``bytes_written`` and the file list, never this count.
            entries_extracted=2,
            entries_skipped=1,
            entries_rejected=0,
            bytes_written=0 if dry_run else max(real_size, 15),
            dry_run=dry_run,
            entries=(file_entry, junk_entry),
        )

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - helper
        await asyncio.sleep(seconds)
