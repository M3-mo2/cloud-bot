"""DTO → domain entity mapping; centralizes nil-handling."""

from __future__ import annotations

from datetime import datetime, timezone

from cloudbot.adapters.cloud_service import dto
from cloudbot.domain.entities import FileEntry, ResourceUsage, Run, TerminalSession, Workspace
from cloudbot.domain.meta import (
    ArchiveLimits,
    Archives,
    Capability,
    EngineMeta,
    Metrics,
    Network,
    Ports,
    ProfileInfo,
    Profiles,
    SessionConfig,
    Subscriptions,
    Tunnels,
    WorkspaceLimits,
    WriteRate,
)
from cloudbot.domain.value_objects import (
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
    RunPage,
    RunStatus,
    SessionPage,
    SessionStatus,
    SummaryState,
    Tunnel,
    TunnelList,
    UnixSocket,
    WorkspaceId,
    WorkspaceStatus,
    WorkspaceSummary,
)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def limits_from_dto(d: dto.LimitsDTO) -> Limits:
    return Limits(**d.model_dump())


def summary_from_dto(s: dto.WorkspaceSummaryDTO | None) -> WorkspaceSummary:
    if s is None:
        return WorkspaceSummary(
            state=SummaryState.ACTIVE,
            age_sec=0,
            created_at="",
            runs_total=0,
            runs_active=0,
            disk_used_bytes=0,
            disk_limit_bytes=0,
            profile="",
        )
    return WorkspaceSummary(
        state=SummaryState.parse(s.state),
        age_sec=s.age_sec,
        created_at=s.created_at,
        runs_total=s.runs.total,
        runs_active=s.runs.active,
        disk_used_bytes=s.disk.used_bytes,
        disk_limit_bytes=s.disk.limit_bytes,
        profile=s.profile,
        subscription_state=s.subscription.state if s.subscription else None,
        subscription_remaining_sec=(
            s.subscription.remaining_sec if s.subscription else None
        ),
    )


def workspace_from_dto(
    w: dto.WorkspaceDTO, *, owner_tg_id: int = 0
) -> Workspace:
    created = _dt(w.created_at) or datetime.now(timezone.utc)
    last = _dt(w.last_activity_at) or created
    return Workspace(
        id=WorkspaceId(w.id),
        owner_tg_id=owner_tg_id,
        name=w.name,
        profile=w.profile,
        status=WorkspaceStatus.parse(w.status),
        limits=limits_from_dto(w.limits),
        summary=summary_from_dto(w.summary),
        created_at=created,
        last_activity_at=last,
    )


def file_entry_from_dto(e: dto.FileEntryDTO) -> FileEntry:
    ftype = e.type if e.type in ("file", "dir", "symlink") else "file"
    return FileEntry(
        path=e.path,
        name=e.name,
        type=ftype,  # type: ignore[arg-type]
        size=e.size,
        mode=e.mode,
        mod_time=_dt(e.mod_time),
        runnable=e.runnable,
        target=e.target,
    )


def file_page_from_dto(
    env: dto.FileListEnvelope, *, offset: int, limit: int
) -> FilePage:
    return FilePage(
        path=env.path or "/",
        entries=tuple(file_entry_from_dto(e) for e in env.entries),
        count=env.count,
        total=env.total,
        truncated=env.truncated,
        offset=offset,
        limit=limit,
    )


def file_content_from_dto(env: dto.FileContentEnvelope) -> FileContent:
    return FileContent(
        path=env.path,
        content=env.content,
        encoding=env.encoding,
        size=env.size,
        total_size=env.total_size,
        whole_file=env.whole_file,
        sha256=env.sha256,
        content_type=env.content_type,
        truncated=env.truncated,
        binary=env.binary,
        start_line=env.start_line,
        end_line=env.end_line,
        total_lines=env.total_lines,
    )


def run_from_dto(w: dto.RunDTO) -> Run:
    kind = w.kind if w.kind in ("file", "command") else "command"
    return Run(
        id=w.id,  # type: ignore[arg-type]
        workspace_id=w.workspace_id,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        argv=list(w.argv),
        cwd=w.cwd,
        status=RunStatus.parse(w.status),
        exit_code=w.exit_code,
        interpreter=w.interpreter,
        timeout_sec=w.timeout_sec,
        started_at=_dt(w.started_at),
        ended_at=_dt(w.ended_at),
        unlimited=w.unlimited,
        stdout_bytes=w.logs.stdout_bytes,
        stderr_bytes=w.logs.stderr_bytes,
        logs_truncated=w.logs.truncated,
    )


def run_page_from_dto(env: dto.RunListEnvelope) -> RunPage:
    return RunPage(
        runs=tuple(run_from_dto(r) for r in env.runs),
        total=env.total,
        limit=env.limit,
        offset=env.offset,
    )


def log_chunk_from_dto(d: dto.LogChunkDTO) -> LogChunk:
    return LogChunk(data=d.data, offset=d.offset, next=d.next, eof=d.eof, truncated=d.truncated)


def session_from_dto(s: dto.SessionDTO, *, ws_id: WorkspaceId | None = None) -> TerminalSession:
    resolved_ws = WorkspaceId(s.workspace_id) if s.workspace_id else ws_id
    if resolved_ws is None:
        raise ValueError("session DTO carries no workspace_id and none was supplied")
    return TerminalSession(
        id=s.id,
        workspace_id=resolved_ws,
        status=SessionStatus.parse(s.status),
        cols=s.cols,
        rows=s.rows,
        out_offset=0,
        created_at=_dt(s.created_at),
        reason=s.reason,
        diagnostic=s.diagnostic,
        exit_code=s.exit_code,
        output_truncated=s.output_truncated,
    )


def session_page_from_dto(
    env: dto.SessionListEnvelope, *, ws_id: WorkspaceId | None = None, offset: int = 0, limit: int = 20
) -> SessionPage:
    return SessionPage(
        sessions=tuple(session_from_dto(s, ws_id=ws_id) for s in env.sessions),
        total=env.total,
        limit=env.limit or limit,
        offset=env.offset or offset,
    )


def lifecycle_from_dto(d: dto.LifecycleDTO) -> LifecycleResult:
    return LifecycleResult(
        changed=d.changed,
        stopped_run_count=d.stopped_run_count,
        stopped_session_count=d.stopped_session_count,
        stopped_process_count=d.stopped_process_count,
        stopped_run_ids=tuple(d.stopped_run_ids),
        stopped_session_ids=tuple(d.stopped_session_ids),
        stopped_process_ids=tuple(d.stopped_process_ids),
    )


def exec_result_from_dto(d: dto.ExecResultDTO) -> ExecResult:
    return ExecResult(
        exit_code=d.exit_code,
        stdout=d.stdout,
        stderr=d.stderr,
        duration_ms=d.duration_ms,
        truncated=d.truncated,
    )


def usage_from_dto(d: dto.UsageDTO, *, fallback_limit_mb: int = 0) -> ResourceUsage:
    limit_bytes = d.disk_mb * 1024 * 1024 if d.disk_mb else fallback_limit_mb * 1024 * 1024
    return ResourceUsage(
        disk_bytes=d.disk_bytes,
        disk_limit_bytes=limit_bytes,
        disk_percent=d.disk_percent,
        runs_total=d.runs_total,
        active_runs=d.active_runs,
        max_runs=d.max_runs,
        disk_complete=d.disk_bytes_complete,
    )


def meta_from_dict(data: dict) -> EngineMeta:
    """Parse the ``/v1/meta`` payload into a domain ``EngineMeta``."""
    profiles_raw = data.get("profiles") or {}
    profiles = Profiles(
        default=str(profiles_raw.get("default", "python")),
        ready=bool(profiles_raw.get("ready", True)),
        profiles=tuple(
            ProfileInfo(
                name=str(p.get("name", "")),
                description=str(p.get("description", "")),
                commands=tuple(p.get("commands", []) or []),
                languages=tuple(p.get("languages", []) or []),
                provides=tuple(p.get("provides", []) or []),
                notes=str(p.get("notes", "")),
            )
            for p in (profiles_raw.get("profiles") or [])
        ),
    )
    archives_raw = data.get("archives") or {}
    arch_limits_raw = archives_raw.get("limits") or {}
    archives = Archives(
        enabled=bool(archives_raw.get("enabled", False)),
        formats=tuple(archives_raw.get("formats", []) or []),
        read_only=tuple(archives_raw.get("read_only", []) or []),
        limits=ArchiveLimits(
            **{
                k: arch_limits_raw[k]
                for k in ArchiveLimits.__dataclass_fields__
                if k in arch_limits_raw
            }
        ),
    )
    metrics_raw = data.get("metrics") or {}
    subscriptions_raw = data.get("subscriptions") or {}
    tunnels_raw = data.get("tunnels") or {}
    sessions_raw = data.get("sessions") or {}
    workspace_limits_raw = data.get("workspace_limits") or {}
    write_rate_raw = data.get("write_rate") or {}
    network_raw = data.get("network") or {}
    ports_raw = data.get("ports") or {}

    def _capability(key: str) -> Capability:
        raw = data.get(key) or {}
        return Capability(
            enabled=bool(raw.get("enabled", False)),
            ready=bool(raw.get("ready", False)),
            reason=str(raw.get("reason", "") or ""),
        )

    return EngineMeta(
        version=str(data.get("version", "")),
        api_version=str(data.get("api_version", "")),
        runtimes=tuple(data.get("runtimes", []) or []),
        interpreters=tuple(data.get("interpreters", []) or []),
        runnable_extensions=tuple(data.get("runnable_extensions", []) or []),
        profiles=profiles,
        limits_defaults=Limits.from_dict(data.get("limits_defaults")),
        limits_maxima=Limits.from_dict(data.get("limits_maxima")),
        network=Network(
            enabled=bool(network_raw.get("enabled", False)),
            available=bool(network_raw.get("available", False)),
            ready=bool(network_raw.get("ready", False)),
        ),
        ports=Ports(enabled=bool(ports_raw.get("enabled", False))),
        metrics=Metrics(
            enabled=bool(metrics_raw.get("enabled", False)),
            interval_seconds=float(metrics_raw.get("interval_seconds", 3.0)),
            min_interval_sec=float(metrics_raw.get("min_interval_sec", 1.0)),
            max_interval_sec=float(metrics_raw.get("max_interval_sec", 300.0)),
        ),
        archives=archives,
        subscriptions=Subscriptions(
            enabled=bool(subscriptions_raw.get("enabled", False)),
            default_duration=str(subscriptions_raw.get("default_duration", "") or ""),
            max_duration=str(subscriptions_raw.get("max_duration", "") or ""),
        ),
        tunnels=Tunnels(
            enabled=bool(tunnels_raw.get("enabled", False)),
            ready=bool(tunnels_raw.get("ready", False)),
            max_per_workspace=int(tunnels_raw.get("max_per_workspace", 2)),
            url_timeout_sec=int(tunnels_raw.get("url_timeout_sec", 60)),
        ),
        sessions=SessionConfig(
            **{
                k: sessions_raw[k]
                for k in SessionConfig.__dataclass_fields__
                if k in sessions_raw
            }
        ),
        workspace_limits=WorkspaceLimits(
            enabled=bool(workspace_limits_raw.get("enabled", False)),
            mechanism=str(workspace_limits_raw.get("mechanism", "counter")),
        ),
        write_rate=WriteRate(
            enabled=bool(write_rate_raw.get("enabled", False)),
            server_max_mb_s=int(write_rate_raw.get("server_max_mb_s", 0)),
            default_mb_s=int(write_rate_raw.get("default_mb_s", 0)),
        ),
        egress_decoy=_capability("egress_decoy"),
        identity_masking=_capability("identity_masking"),
        provider_tools_hidden=_capability("provider_tools_hidden"),
    )


# -- Phase 2a: Python packages -----------------------------------------------
def package_list_from_dto(env: dto.PackageListEnvelope) -> PackageList:
    return PackageList(
        packages=tuple(
            Package(name=p.name, version=p.version) for p in env.packages
        ),
        count=env.count,
        truncated=env.truncated,
        shared_enabled=env.shared_enabled,
        shared_available=env.shared_available,
        shared_path=env.shared_path,
        private_path=env.private_path,
    )


def install_result_from_dto(d: dto.InstallResultDTO) -> InstallResult:
    return InstallResult(
        exit_code=d.exit_code,
        output=d.output,
        stdout=d.stdout,
        duration_ms=d.duration_ms,
        timed_out=d.timed_out,
        truncated=d.truncated,
        target=d.target,
    )


# -- Phase 2b: tunnels -------------------------------------------------------
def tunnel_from_dto(t: dto.TunnelDTO) -> Tunnel:
    return Tunnel(
        id=t.id,
        workspace_id=t.workspace_id,
        port=t.port,
        url=t.url,
        status=t.status,
        created_at=_dt(t.created_at),
    )


def tunnel_list_from_dto(env: dto.TunnelListEnvelope) -> TunnelList:
    return TunnelList(tunnels=tuple(tunnel_from_dto(t) for t in env.tunnels))


# -- Phase 2c: port discovery ------------------------------------------------
def port_entry_from_dto(p: dto.PortEntryDTO) -> PortEntry:
    return PortEntry(
        port=p.port,
        protocol=p.protocol,
        address=p.address,
        scope=p.scope,
        state=p.state,
        attributed=p.attributed,
        pid=p.pid,
        kind=p.kind,
        label=p.label,
        argv=tuple(p.argv),
        owners=tuple(p.owners),
        inode=p.inode,
    )


def unix_socket_from_dto(u: dto.UnixSocketDTO) -> UnixSocket:
    return UnixSocket(
        path=u.path,
        type=u.type,
        state=u.state,
        attributed=u.attributed,
        pid=u.pid,
        kind=u.kind,
    )


def port_report_from_dto(env: dto.PortReportEnvelope) -> PortReport:
    return PortReport(
        workspace_id=env.workspace_id,
        ports=tuple(port_entry_from_dto(p) for p in env.ports),
        unix=tuple(unix_socket_from_dto(u) for u in env.unix),
        count=env.count,
        unix_count=env.unix_count,
        complete=env.complete,
        notes=tuple(env.notes),
        pids_scanned=env.pids_scanned,
        fds_scanned=env.fds_scanned,
        reachability=env.reachability,
    )
