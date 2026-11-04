"""``EnginePort`` — the complete Cloud Service surface the bot uses.

Every method returns domain entities or raises a ``domain.errors`` value. This is
the seam that makes the engine swappable: services depend on this Protocol, never
on the HTTP adapter.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from cloudbot.domain.entities import FileEntry, Run, TerminalSession, Workspace
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import (
    ExecResult,
    FileContent,
    FilePage,
    InstallResult,
    LifecycleResult,
    Limits,
    LogChunk,
    PackageList,
    PortReport,
    ResourceUsage,
    RunPage,
    SessionPage,
    Tunnel,
    TunnelList,
    WorkspaceId,
)


class EnginePort(Protocol):
    async def aclose(self) -> None: ...

    async def meta(self, refresh: bool = False) -> EngineMeta: ...
    async def health(self) -> dict[str, Any]: ...
    async def ready(self) -> dict[str, Any]: ...

    async def create_workspace(
        self,
        *,
        name: str,
        profile: str | None,
        limits: Limits | None,
        subscription: dict[str, Any] | None = None,
    ) -> Workspace: ...
    async def list_workspaces(self) -> list[Workspace]: ...
    async def get_workspace(self, ws_id: WorkspaceId) -> Workspace: ...
    async def patch_workspace(
        self, ws_id: WorkspaceId, *, name: str | None = None, limits: Limits | None = None
    ) -> Workspace: ...
    async def suspend_workspace(self, ws_id: WorkspaceId) -> LifecycleResult: ...
    async def resume_workspace(self, ws_id: WorkspaceId) -> LifecycleResult: ...
    async def stop_workspace(self, ws_id: WorkspaceId) -> LifecycleResult: ...
    async def delete_workspace(self, ws_id: WorkspaceId) -> None: ...
    async def workspace_usage(self, ws_id: WorkspaceId) -> ResourceUsage: ...

    async def list_files(
        self, ws_id: WorkspaceId, path: str, *, offset: int = 0, limit: int = 20
    ) -> FilePage: ...
    async def stat_file(self, ws_id: WorkspaceId, path: str) -> FileEntry: ...
    async def read_file(
        self,
        ws_id: WorkspaceId,
        path: str,
        *,
        encoding: str | None = None,
        line_range: tuple[int, int] | None = None,
        meta: bool = False,
    ) -> FileContent: ...
    async def write_file(
        self, ws_id: WorkspaceId, path: str, content: str, *, encoding: str = "text"
    ) -> None: ...
    async def delete_file(self, ws_id: WorkspaceId, path: str) -> None: ...
    async def mkdir(self, ws_id: WorkspaceId, path: str) -> None: ...
    async def rename(self, ws_id: WorkspaceId, src: str, dst: str) -> None: ...
    async def upload_file(
        self, ws_id: WorkspaceId, path: str, filename: str, stream: AsyncIterator[bytes]
    ) -> None: ...

    async def exec(
        self,
        ws_id: WorkspaceId,
        cmd: list[str] | str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult: ...
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
    ) -> Run: ...
    async def list_runs(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> RunPage: ...
    async def get_run(self, ws_id: WorkspaceId, run_id: str) -> Run: ...
    async def run_logs(
        self,
        ws_id: WorkspaceId,
        run_id: str,
        *,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
    ) -> LogChunk: ...
    async def stop_run(self, ws_id: WorkspaceId, run_id: str) -> None: ...
    async def purge_runs(self, ws_id: WorkspaceId) -> int: ...
    async def delete_run(self, ws_id: WorkspaceId, run_id: str) -> None: ...

    # -- interactive terminal sessions (Phase 1.5, native engine PTY) --------
    async def open_session(
        self,
        ws_id: WorkspaceId,
        *,
        shell: str | None = None,
        cwd: str | None = None,
        cols: int = 80,
        rows: int = 24,
        timeout_sec: int | None = None,
    ) -> TerminalSession: ...
    async def list_sessions(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SessionPage: ...
    async def get_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession: ...
    async def session_input(self, ws_id: WorkspaceId, ses_id: str, data: str) -> None: ...
    async def session_output(
        self, ws_id: WorkspaceId, ses_id: str, *, offset: int = 0, limit: int = 65536
    ) -> LogChunk: ...
    async def session_resize(self, ws_id: WorkspaceId, ses_id: str, *, cols: int, rows: int) -> None: ...
    async def session_signal(self, ws_id: WorkspaceId, ses_id: str, signal: str) -> None: ...
    async def close_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession: ...

    # -- Python packages (Phase 2a, pip inside the workspace) ----------------
    async def list_packages(self, ws_id: WorkspaceId) -> PackageList: ...
    async def install_packages(
        self, ws_id: WorkspaceId, packages: list[str]
    ) -> InstallResult: ...
    async def remove_package(self, ws_id: WorkspaceId, name: str) -> None: ...
    async def remove_all_packages(self, ws_id: WorkspaceId) -> None: ...

    # -- tunnels (Phase 2b, ephemeral public URL) ----------------------------
    async def list_tunnels(self, ws_id: WorkspaceId) -> TunnelList: ...
    async def create_tunnel(self, ws_id: WorkspaceId, port: int) -> Tunnel: ...
    async def delete_tunnel(self, ws_id: WorkspaceId, tunnel_id: str) -> None: ...

    # -- port discovery (Phase 2c, read-only introspection) ------------------
    async def list_ports(self, ws_id: WorkspaceId) -> PortReport: ...
