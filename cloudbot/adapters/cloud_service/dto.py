"""Pydantic DTOs mirroring the Cloud Service v0.0.20 wire format.

``extra="ignore"`` everywhere so a newer engine field never crashes the bot.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ErrorEnvelope(_DTO):
    code: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(_DTO):
    error: ErrorEnvelope = Field(default_factory=ErrorEnvelope)


class LimitsDTO(_DTO):
    cpu_cores: float = 1.0
    memory_mb: int = 512
    disk_mb: int = 256
    max_wall_time_sec: int = 300
    max_runs: int = 8
    allow_net: bool = False
    allow_unlimited_runs: bool = False
    allow_tunnels: bool = False
    write_rate_mb_s: int = 0


class SummaryRunsDTO(_DTO):
    total: int = 0
    active: int = 0


class SummaryDiskDTO(_DTO):
    used_bytes: int = 0
    limit_bytes: int = 0



class WorkspaceSummaryDTO(_DTO):
    state: str = "active"
    age_sec: int = 0
    created_at: str = ""
    runs: SummaryRunsDTO = Field(default_factory=SummaryRunsDTO)
    disk: SummaryDiskDTO = Field(default_factory=SummaryDiskDTO)
    profile: str = ""


class WorkspaceDTO(_DTO):
    id: str
    owner_id: str = ""
    name: str = ""
    profile: str = ""
    created_at: str = ""
    status: str = "active"
    limits: LimitsDTO = Field(default_factory=LimitsDTO)
    last_activity_at: str = ""
    summary: WorkspaceSummaryDTO | None = None


class WorkspaceEnvelope(_DTO):
    workspace: WorkspaceDTO


class WorkspaceListEnvelope(_DTO):
    workspaces: list[WorkspaceDTO] = Field(default_factory=list)


class FileEntryDTO(_DTO):
    name: str = ""
    path: str = ""
    type: str = "file"
    size: int | None = None
    mode: str | None = None
    mod_time: str | None = None
    target: str | None = None
    target_inside: bool | None = None
    runnable: str | None = None
    #: Carried by ``files/stat`` for a regular file (API.md L1316-1328). A
    #: listing omits them and they fall back to the empty/false defaults.
    sha256: str = ""
    content_type: str = ""
    binary: bool = False


class FileListEnvelope(_DTO):
    path: str = "/"
    entries: list[FileEntryDTO] = Field(default_factory=list)
    count: int = 0
    total: int = 0
    truncated: bool = False


class FileStatDTO(_DTO):
    name: str = ""
    path: str = ""
    type: str = "file"
    size: int | None = None
    mode: str | None = None
    mod_time: str | None = None
    content_type: str = ""
    sha256: str = ""
    binary: bool = False
    runnable: str | None = None


class FileContentEnvelope(_DTO):
    path: str = ""
    content: str = ""
    encoding: str = "text"
    size: int = 0
    total_size: int = 0
    whole_file: bool = True
    sha256: str = ""
    content_type: str = ""
    truncated: bool = False
    binary: bool = False
    start_line: int | None = None
    end_line: int | None = None
    total_lines: int | None = None


class RunLogsDTO(_DTO):
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    truncated: bool = False


class RunDTO(_DTO):
    id: str
    workspace_id: str = ""
    kind: str = "command"
    argv: list[str] = Field(default_factory=list)
    status: str = "queued"
    exit_code: int | None = None
    cwd: str = ""
    timeout_sec: int | None = None
    unlimited: bool = False
    interpreter: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    logs: RunLogsDTO = Field(default_factory=RunLogsDTO)


class RunEnvelope(_DTO):
    run: RunDTO


class RunListEnvelope(_DTO):
    runs: list[RunDTO] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class LogChunkDTO(_DTO):
    data: str = ""
    offset: int = 0
    next: int = 0
    eof: bool = True
    truncated: bool = False


class LifecycleDTO(_DTO):
    workspace: WorkspaceDTO | None = None
    changed: bool = True
    stopped_run_ids: list[str] = Field(default_factory=list)
    stopped_run_count: int = 0
    stopped_session_ids: list[str] = Field(default_factory=list)
    stopped_session_count: int = 0
    stopped_process_ids: list[str] = Field(default_factory=list)
    stopped_process_count: int = 0


class ExecResultDTO(_DTO):
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    truncated: bool = False


class SessionDTO(_DTO):
    id: str
    workspace_id: str = ""
    shell: str = ""
    cwd: str = ""
    cols: int = 80
    rows: int = 24
    status: str = "running"
    created_at: str = ""
    last_activity_at: str = ""
    started_at: str = ""
    ended_at: str = ""
    exit_code: int | None = None
    output_bytes: int = 0
    output_truncated: bool = False
    reason: str = ""
    diagnostic: str = ""


class SessionEnvelope(_DTO):
    session: SessionDTO


class SessionListEnvelope(_DTO):
    sessions: list[SessionDTO] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class CloseSessionEnvelope(_DTO):
    session: SessionDTO | None = None
    changed: bool = True


class UsageDTO(_DTO):
    workspace_id: str = ""
    status: str = ""
    disk_bytes: int = 0
    disk_bytes_complete: bool = True
    disk_mb: int = 0
    disk_percent: float = 0.0
    runs_total: int = 0
    runs_by_status: dict[str, int] = Field(default_factory=dict)
    active_runs: int = 0
    max_runs: int = 0


class DeleteRunsEnvelope(_DTO):
    deleted: int = 0


# -- Phase 2a: Python packages -----------------------------------------------
class PackageDTO(_DTO):
    name: str = ""
    version: str = ""


class PackageListEnvelope(_DTO):
    packages: list[PackageDTO] = Field(default_factory=list)
    count: int = 0
    truncated: bool = False
    shared_enabled: bool = False
    shared_available: bool = False
    shared_path: str = ""
    private_path: str = "/opt/python/private"


class InstallResultDTO(_DTO):
    duration_ms: int = 0
    exit_code: int = 0
    output: str = ""
    stdout: str = ""
    timed_out: bool = False
    truncated: bool = False
    target: str = ""


# -- Phase 2b: tunnels -------------------------------------------------------
class TunnelDTO(_DTO):
    id: str = ""
    workspace_id: str = ""
    port: int = 0
    url: str = ""
    status: str = ""
    created_at: str = ""


class TunnelEnvelope(_DTO):
    tunnel: TunnelDTO


class TunnelListEnvelope(_DTO):
    tunnels: list[TunnelDTO] = Field(default_factory=list)


# -- Phase 2c: port discovery ------------------------------------------------
class PortEntryDTO(_DTO):
    port: int = 0
    protocol: str = "tcp"
    address: str = ""
    scope: str = ""
    state: str = ""
    attributed: bool = False
    pid: int | None = None
    kind: str = ""
    label: str = ""
    argv: list[str] = Field(default_factory=list)
    owners: list[int] = Field(default_factory=list)
    inode: int | None = None


class UnixSocketDTO(_DTO):
    path: str = ""
    type: str = ""
    state: str = ""
    attributed: bool = False
    pid: int | None = None
    kind: str = ""


class PortReportEnvelope(_DTO):
    workspace_id: str = ""
    ports: list[PortEntryDTO] = Field(default_factory=list)
    count: int = 0
    unix: list[UnixSocketDTO] = Field(default_factory=list)
    unix_count: int = 0
    complete: bool = True
    notes: list[str] = Field(default_factory=list)
    pids_scanned: int = 0
    fds_scanned: int = 0
    reachability: str = ""


# -- Phase 2d: workspace resource metrics ------------------------------------
class WorkspaceMetricsDTO(_DTO):
    workspace_id: str = ""
    ram_used_bytes: int = 0
    ram_limit_bytes: int = 0
    ram_percent: float = 0.0
    ram_complete: bool = True
    disk_used_bytes: int = 0
    disk_limit_bytes: int = 0
    disk_percent: float = 0.0
    disk_complete: bool = True
    write_rate_limit_mb_s: int = 0
    live_runs: int = 0
    live_cgroups: int = 0


class MetricsEnvelope(_DTO):
    timestamp: str = ""
    interval_seconds: int = 3
    workspaces: list[WorkspaceMetricsDTO] = Field(default_factory=list)
    sampler: dict[str, Any] = Field(default_factory=dict)


# -- Phase 2e: process inventory ---------------------------------------------
class ProcessDTO(_DTO):
    id: str = ""
    pid: int = 0
    ppid: int = 0
    argv: list[str] = Field(default_factory=list)
    kind: str = ""
    source_id: str = ""
    started_at: str = ""


class ProcessListEnvelope(_DTO):
    processes: list[ProcessDTO] = Field(default_factory=list)
    count: int = 0


class ProcessStopEnvelope(_DTO):
    process_id: str = ""
    status: str = ""
    stopped: bool = False
    stopped_at: str = ""


# -- Phase 2g: archives ------------------------------------------------------
class ArchiveEntryDTO(_DTO):
    name: str = ""
    path: str = ""
    type: str = "file"
    size: int = 0
    mode: str = ""
    rejected: bool = False
    skipped: bool = False
    reason: str = ""
    skip_reason: str = ""


class ArchiveResultDTO(_DTO):
    format: str = ""
    destination: str = ""
    entries_total: int = 0
    entries_extracted: int = 0
    entries_skipped: int = 0
    entries_rejected: int = 0
    bytes_written: int = 0
    dry_run: bool = False
    entries: list[ArchiveEntryDTO] = Field(default_factory=list)
