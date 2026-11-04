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
from cloudbot.domain.meta import EngineMeta, Profiles, ProfileInfo, SessionConfig
from cloudbot.domain.value_objects import (
    ExecResult,
    FileContent,
    FilePage,
    LifecycleResult,
    Limits,
    LogChunk,
    RunPage,
    RunStatus,
    SessionPage,
    SessionStatus,
    SummaryState,
    WorkspaceId,
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
        self._seq = 0
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
            limits_maxima=Limits(cpu_cores=8, memory_mb=8192, disk_mb=51200, max_wall_time_sec=3600),
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

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - helper
        await asyncio.sleep(seconds)
