"""``EnginePort`` implementation over the HTTP client.

This is the only module that knows the Cloud Service wire format. It maps domain
calls to HTTP and responses to domain entities. Stale cached numbers are handled
by refreshing ``/v1/meta`` and retrying **once**, but only on idempotent calls
(``PATCH``); a creation ``POST`` is never auto-retried.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx

from cloudbot.adapters.cloud_service import dto
from cloudbot.adapters.cloud_service.client import EngineClient
from cloudbot.adapters.cloud_service.mapping import (
    archive_result_from_dto,
    exec_result_from_dto,
    file_content_from_dto,
    file_page_from_dto,
    file_entry_from_stat_dto,
    install_result_from_dto,
    lifecycle_from_dto,
    log_chunk_from_dto,
    package_list_from_dto,
    port_report_from_dto,
    process_list_from_dto,
    process_stop_from_dto,
    run_from_dto,
    run_page_from_dto,
    session_from_dto,
    session_page_from_dto,
    tunnel_from_dto,
    tunnel_list_from_dto,
    usage_from_dto,
    workspace_from_dto,
    workspace_metrics_from_dto,
)
from cloudbot.adapters.cloud_service.meta import MetaCache
from cloudbot.core.errors import EngineRefused
from cloudbot.domain.entities import FileEntry, ResourceUsage, Run, TerminalSession, Workspace
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import (
    ArchiveResult,
    ExecResult,
    FileContent,
    FilePage,
    InstallResult,
    LifecycleResult,
    Limits,
    LogChunk,
    PackageList,
    PortReport,
    ProcessList,
    ProcessStopResult,
    RunPage,
    SessionPage,
    Tunnel,
    TunnelList,
    WorkspaceId,
    WorkspaceMetrics,
)

#: exec is hard-capped at 60 s by the engine.
EXEC_HARD_CAP_SEC = 60
#: Default page size for file listings (UI 03 uses 10–20 per page).
FILE_PAGE_LIMIT = 20
#: A pip install runs inside the sandbox and is slow (measured ~10 s for one
#: package); the client timeout must exceed that, bounded so it can never hang.
PACKAGE_INSTALL_TIMEOUT_S = 300.0
#: A tunnel create waits for cloudflared to observe a URL
#: (``tunnels.url_timeout_sec``, measured 45 s); bound the client timeout above it.
TUNNEL_TIMEOUT_S = 90.0
#: A bounded port scan reads /proc for the workspace's pids and fds.
PORTS_TIMEOUT_S = 30.0
#: An archive extract/export is bounded by the streaming budget but a large
#: (up to ``archives.upload_mb``) upload and a slow export need room.
ARCHIVE_TIMEOUT_S = 120.0


def _ws_path(ws_id: WorkspaceId, suffix: str = "") -> str:
    return f"/v1/workspaces/{ws_id}{suffix}"


def _rfc3339(when: datetime) -> str:
    """An absolute instant as the wire format the engine parses."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class EngineAdapter:
    def __init__(
        self,
        client: EngineClient,
        *,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = client
        self._timeout_s = timeout_s
        self._meta = MetaCache(client)
        self._install_timeout_s = PACKAGE_INSTALL_TIMEOUT_S
        self._tunnel_timeout_s = TUNNEL_TIMEOUT_S
        #: F-08: the named port-scan budget was defined but never used; the call
        #: now passes it explicitly so bumping the constant has an effect.
        self._ports_timeout_s = PORTS_TIMEOUT_S

    @classmethod
    def create(
        cls,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "EngineAdapter":
        client = EngineClient(
            base_url,
            api_key,
            timeout_s=timeout_s,
            max_retries=max_retries,
            transport=transport,
        )
        return cls(client, timeout_s=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- meta / health -------------------------------------------------------
    async def meta(self, refresh: bool = False) -> EngineMeta:
        return await self._meta.get(refresh=refresh)

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("/healthz")
        return response.json()

    async def ready(self) -> dict[str, Any]:
        response = await self._client.get("/readyz")
        return response.json()

    # -- workspaces ----------------------------------------------------------
    async def create_workspace(
        self,
        *,
        name: str,
        profile: str | None,
        limits: Limits | None,
        subscription: dict[str, Any] | None = None,
    ) -> Workspace:
        body: dict[str, Any] = {"name": name}
        if profile:
            body["profile"] = profile
        if limits is not None:
            body["limits"] = limits.to_request()
        if subscription:
            body["subscription"] = subscription
        try:
            response = await self._client.post("/v1/workspaces", json=body)
        except EngineRefused as exc:
            # A ``422``/``409`` above a cached maximum means the cached numbers
            # were stale; re-read ``/v1/meta`` so the next attempt's pickers are
            # right (INTEGRATION-GUIDE.md:29-32). Creation is not idempotent, so
            # the refusal is still surfaced — never retried behind the user.
            await self._refresh_meta_if_stale(exc)
            raise
        env = dto.WorkspaceEnvelope.model_validate(response.json())
        return workspace_from_dto(env.workspace)

    async def extend_subscription(
        self, ws_id: WorkspaceId, *, expires_at: datetime
    ) -> Workspace:
        """POST the absolute expiry the bot computed (``subscription/extend``).

        ``expires_at`` is an RFC3339 UTC instant in the future; the engine stores
        it as the absolute term (INTEGRATION-GUIDE §8.3). This is the renewal path
        for ``403 subscription_expired``.
        """
        body = {"expires_at": _rfc3339(expires_at)}
        response = await self._client.post(_ws_path(ws_id, "/subscription/extend"), json=body)
        env = dto.WorkspaceEnvelope.model_validate(response.json())
        return workspace_from_dto(env.workspace)

    async def list_workspaces(self) -> list[Workspace]:
        response = await self._client.get("/v1/workspaces")
        env = dto.WorkspaceListEnvelope.model_validate(response.json())
        return [workspace_from_dto(w) for w in env.workspaces]

    async def get_workspace(self, ws_id: WorkspaceId) -> Workspace:
        response = await self._client.get(_ws_path(ws_id))
        env = dto.WorkspaceEnvelope.model_validate(response.json())
        return workspace_from_dto(env.workspace)

    async def patch_workspace(
        self,
        ws_id: WorkspaceId,
        *,
        name: str | None = None,
        limits: Limits | None = None,
    ) -> Workspace:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if limits is not None:
            body["limits"] = limits.to_request()

        async def _call() -> Workspace:
            response = await self._client.patch(_ws_path(ws_id), json=body)
            env = dto.WorkspaceEnvelope.model_validate(response.json())
            return workspace_from_dto(env.workspace)

        # A ``422`` on a limits PATCH is the documented stale-cache case: the
        # cached ``limits_maxima`` no longer matches the engine's. Refresh meta
        # and retry the (idempotent) PATCH once; the refusal is surfaced if it
        # persists. Creation POSTs deliberately do not take this path.
        return await self._with_meta_refresh(_call)

    async def suspend_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        response = await self._client.post(_ws_path(ws_id, "/suspend"))
        return lifecycle_from_dto(dto.LifecycleDTO.model_validate(response.json()))

    async def resume_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        response = await self._client.post(_ws_path(ws_id, "/resume"))
        return lifecycle_from_dto(dto.LifecycleDTO.model_validate(response.json()))

    async def stop_workspace(self, ws_id: WorkspaceId) -> LifecycleResult:
        response = await self._client.post(_ws_path(ws_id, "/stop"))
        return lifecycle_from_dto(dto.LifecycleDTO.model_validate(response.json()))

    async def delete_workspace(self, ws_id: WorkspaceId) -> None:
        await self._client.delete(_ws_path(ws_id))

    async def workspace_usage(self, ws_id: WorkspaceId) -> ResourceUsage:
        response = await self._client.get(_ws_path(ws_id, "/usage"))
        return usage_from_dto(dto.UsageDTO.model_validate(response.json()))

    # -- files ---------------------------------------------------------------
    async def list_files(
        self, ws_id: WorkspaceId, path: str, *, offset: int = 0, limit: int = FILE_PAGE_LIMIT
    ) -> FilePage:
        response = await self._client.get(
            _ws_path(ws_id, "/files"),
            params={"path": path, "offset": offset, "limit": limit},
        )
        env = dto.FileListEnvelope.model_validate(response.json())
        return file_page_from_dto(env, offset=offset, limit=limit)

    async def stat_file(self, ws_id: WorkspaceId, path: str) -> FileEntry:
        response = await self._client.get(
            _ws_path(ws_id, "/files/stat"), params={"path": path}
        )
        # Map the whole stat response through its DTO: it carries ``sha256``,
        # ``content_type`` and ``binary`` for a regular file, and dropping them
        # made a file's integrity/type unverifiable without a byte read.
        payload = dict(response.json())
        payload.setdefault("name", _basename(path))
        payload.setdefault("path", path)
        return file_entry_from_stat_dto(dto.FileStatDTO.model_validate(payload))

    async def read_file(
        self,
        ws_id: WorkspaceId,
        path: str,
        *,
        encoding: str | None = None,
        line_range: tuple[int, int] | None = None,
        meta: bool = False,
    ) -> FileContent:
        params: dict[str, Any] = {"path": path, "meta": "true"}
        if encoding:
            params["encoding"] = encoding
        if line_range is not None:
            params["start_line"] = line_range[0]
            params["end_line"] = line_range[1]
        response = await self._client.get(_ws_path(ws_id, "/files/content"), params=params)
        return file_content_from_dto(
            dto.FileContentEnvelope.model_validate(response.json())
        )

    async def write_file(
        self, ws_id: WorkspaceId, path: str, content: str, *, encoding: str = "text"
    ) -> None:
        if encoding == "base64":
            payload: dict[str, Any] = {"content": content, "encoding": "base64"}
        else:
            payload = {"content": content}
        await self._client.put(
            _ws_path(ws_id, "/files/content"), params={"path": path}, json=payload
        )

    async def delete_file(self, ws_id: WorkspaceId, path: str) -> None:
        await self._client.delete(_ws_path(ws_id, "/files"), params={"path": path})

    async def mkdir(self, ws_id: WorkspaceId, path: str) -> None:
        await self._client.post(_ws_path(ws_id, "/files/mkdir"), json={"path": path})

    async def rename(self, ws_id: WorkspaceId, src: str, dst: str) -> None:
        await self._client.post(
            _ws_path(ws_id, "/files/rename"), json={"from": src, "to": dst}
        )

    async def upload_file(
        self, ws_id: WorkspaceId, path: str, filename: str, stream: AsyncIterator[bytes]
    ) -> None:
        data = b"".join([chunk async for chunk in stream])
        await self._client.post(
            _ws_path(ws_id, "/files/upload"),
            data={"path": path},
            files={"file": (filename, data)},
        )

    # -- exec / runs ---------------------------------------------------------
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
        if isinstance(cmd, str):
            argv = ["/bin/sh", "-c", cmd]
        else:
            argv = list(cmd)
        effective_timeout = min(timeout_sec or EXEC_HARD_CAP_SEC, EXEC_HARD_CAP_SEC)
        body: dict[str, Any] = {"cmd": argv, "timeout_sec": effective_timeout}
        if cwd is not None:
            body["cwd"] = cwd
        if env is not None:
            body["env"] = env
        if stdin is not None:
            body["stdin"] = stdin
        response = await self._client.post(
            _ws_path(ws_id, "/exec"),
            json=body,
            timeout=self._timeout_s + effective_timeout + 5,
        )
        return exec_result_from_dto(dto.ExecResultDTO.model_validate(response.json()))

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
        body: dict[str, Any] = {"kind": kind}
        if kind == "file":
            if file is None:
                raise EngineRefused("validation", message="kind=file requires file")
            body["file"] = file
            if args:
                body["args"] = args
        else:
            body["argv"] = list(argv or [])
        if cwd is not None:
            body["cwd"] = cwd
        if env is not None:
            body["env"] = env
        if interpreter is not None:
            body["interpreter"] = interpreter
        if timeout_sec is not None:
            body["timeout_sec"] = timeout_sec
        if no_timeout:
            body["no_timeout"] = True
        response = await self._client.post(_ws_path(ws_id, "/runs"), json=body)
        env_dto = dto.RunEnvelope.model_validate(response.json())
        return run_from_dto(env_dto.run)

    async def _with_meta_refresh(self, coro_factory: Any) -> Any:
        """Run an idempotent call; on a stale-number refusal refresh meta, retry once.

        INTEGRATION-GUIDE.md:29-32: re-read ``/v1/meta`` "whenever a request is
        refused with `422`/`409` because your cached numbers were stale", then
        retry. Only idempotent calls are wired through here: a non-idempotent
        create is never retried (surfacing its refusal so the user retries).
        """
        try:
            return await coro_factory()
        except EngineRefused as exc:
            if exc.engine_code in ("unprocessable", "conflict"):
                await self._meta.refresh()
                return await coro_factory()
            raise

    async def _refresh_meta_if_stale(self, exc: EngineRefused) -> None:
        """Refresh ``/v1/meta`` after a stale-number refusal, without retrying.

        Used on non-idempotent calls: a ``422 unprocessable`` is exactly the
        stale-cached-maximum refusal the guide names. Re-reading meta corrects
        the bot's pickers/caps for the next attempt; the creation itself is
        still surfaced, since retrying it could create twice.
        """
        if exc.engine_code in ("unprocessable", "conflict"):
            try:
                await self._meta.refresh()
            except Exception:  # noqa: BLE001 - a failed refresh must not hide the refusal
                pass

    async def list_runs(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> RunPage:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if kind:
            params["kind"] = kind
        response = await self._client.get(_ws_path(ws_id, "/runs"), params=params)
        return run_page_from_dto(dto.RunListEnvelope.model_validate(response.json()))

    async def get_run(self, ws_id: WorkspaceId, run_id: str) -> Run:
        response = await self._client.get(_ws_path(ws_id, f"/runs/{run_id}"))
        env = dto.RunEnvelope.model_validate(response.json())
        return run_from_dto(env.run)

    async def run_logs(
        self,
        ws_id: WorkspaceId,
        run_id: str,
        *,
        stream: str = "stdout",
        offset: int = 0,
        limit: int = 65536,
    ) -> LogChunk:
        response = await self._client.get(
            _ws_path(ws_id, f"/runs/{run_id}/logs"),
            params={"stream": stream, "offset": offset, "limit": limit},
            timeout=10.0,
        )
        return log_chunk_from_dto(dto.LogChunkDTO.model_validate(response.json()))

    async def stop_run(self, ws_id: WorkspaceId, run_id: str) -> None:
        await self._client.post(_ws_path(ws_id, f"/runs/{run_id}/stop"))

    async def purge_runs(self, ws_id: WorkspaceId) -> int:
        response = await self._client.delete(_ws_path(ws_id, "/runs"))
        return int(dto.DeleteRunsEnvelope.model_validate(response.json()).deleted)

    async def delete_run(self, ws_id: WorkspaceId, run_id: str) -> None:
        await self._client.delete(_ws_path(ws_id, f"/runs/{run_id}"))

    # -- interactive terminal sessions (native engine PTY) -------------------
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
        body: dict[str, Any] = {"cols": cols, "rows": rows}
        if shell is not None:
            body["shell"] = shell
        if cwd is not None:
            body["cwd"] = cwd
        if timeout_sec is not None:
            body["timeout_sec"] = timeout_sec
        response = await self._client.post(_ws_path(ws_id, "/sessions"), json=body)
        env = dto.SessionEnvelope.model_validate(response.json())
        return session_from_dto(env.session, ws_id=ws_id)

    async def list_sessions(
        self,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SessionPage:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        response = await self._client.get(_ws_path(ws_id, "/sessions"), params=params)
        return session_page_from_dto(
            dto.SessionListEnvelope.model_validate(response.json()),
            ws_id=ws_id,
            offset=offset,
            limit=limit,
        )

    async def get_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession:
        response = await self._client.get(_ws_path(ws_id, f"/sessions/{ses_id}"))
        env = dto.SessionEnvelope.model_validate(response.json())
        return session_from_dto(env.session, ws_id=ws_id)

    async def session_input(self, ws_id: WorkspaceId, ses_id: str, data: str) -> None:
        await self._client.post(
            _ws_path(ws_id, f"/sessions/{ses_id}/input"), json={"data": data}
        )

    async def session_output(
        self, ws_id: WorkspaceId, ses_id: str, *, offset: int = 0, limit: int = 65536
    ) -> LogChunk:
        response = await self._client.get(
            _ws_path(ws_id, f"/sessions/{ses_id}/output"),
            params={"offset": offset, "limit": limit},
            timeout=10.0,
        )
        return log_chunk_from_dto(dto.LogChunkDTO.model_validate(response.json()))

    async def session_resize(
        self, ws_id: WorkspaceId, ses_id: str, *, cols: int, rows: int
    ) -> None:
        await self._client.post(
            _ws_path(ws_id, f"/sessions/{ses_id}/resize"),
            json={"cols": cols, "rows": rows},
        )

    async def session_signal(self, ws_id: WorkspaceId, ses_id: str, signal: str) -> None:
        await self._client.post(
            _ws_path(ws_id, f"/sessions/{ses_id}/signal"), json={"signal": signal}
        )

    async def close_session(self, ws_id: WorkspaceId, ses_id: str) -> TerminalSession:
        response = await self._client.delete(_ws_path(ws_id, f"/sessions/{ses_id}"))
        env = dto.CloseSessionEnvelope.model_validate(response.json())
        if env.session is None:
            raise EngineRefused("not_found")
        return session_from_dto(env.session, ws_id=ws_id)

    # -- Python packages (Phase 2a) ------------------------------------------
    async def list_packages(self, ws_id: WorkspaceId) -> PackageList:
        response = await self._client.get(_ws_path(ws_id, "/packages"))
        return package_list_from_dto(dto.PackageListEnvelope.model_validate(response.json()))

    async def install_packages(
        self, ws_id: WorkspaceId, packages: list[str]
    ) -> InstallResult:
        # An install runs pip inside the sandbox and can take tens of seconds
        # (measured ~10 s for one package); give it a generous, bounded timeout
        # and never let a slow install block the update loop (the handler awaits
        # it off the update task). Creation POSTs are never auto-retried.
        response = await self._client.post(
            _ws_path(ws_id, "/packages/install"),
            json={"packages": list(packages)},
            timeout=self._install_timeout_s,
        )
        return install_result_from_dto(
            dto.InstallResultDTO.model_validate(response.json())
        )

    async def remove_package(self, ws_id: WorkspaceId, name: str) -> None:
        await self._client.delete(_ws_path(ws_id, "/packages"), params={"name": name})

    async def remove_all_packages(self, ws_id: WorkspaceId) -> None:
        await self._client.delete(_ws_path(ws_id, "/packages"), params={"all": "true"})

    # -- tunnels (Phase 2b) --------------------------------------------------
    async def list_tunnels(self, ws_id: WorkspaceId) -> TunnelList:
        response = await self._client.get(_ws_path(ws_id, "/tunnels"))
        return tunnel_list_from_dto(
            dto.TunnelListEnvelope.model_validate(response.json())
        )

    async def create_tunnel(self, ws_id: WorkspaceId, port: int) -> Tunnel:
        # cloudflared can take up to ``tunnels.url_timeout_sec`` to observe a URL;
        # give the call room (a POST is never auto-retried).
        response = await self._client.post(
            _ws_path(ws_id, "/tunnels"),
            json={"port": port},
            timeout=self._tunnel_timeout_s,
        )
        return tunnel_from_dto(
            dto.TunnelEnvelope.model_validate(response.json()).tunnel
        )

    async def delete_tunnel(self, ws_id: WorkspaceId, tunnel_id: str) -> None:
        await self._client.delete(_ws_path(ws_id, f"/tunnels/{tunnel_id}"))

    # -- port discovery (Phase 2c) -------------------------------------------
    async def list_ports(self, ws_id: WorkspaceId) -> PortReport:
        response = await self._client.get(
            _ws_path(ws_id, "/ports"), timeout=self._ports_timeout_s
        )
        return port_report_from_dto(
            dto.PortReportEnvelope.model_validate(response.json())
        )

    # -- metrics (Phase 2d) --------------------------------------------------
    async def workspace_metrics(self, ws_id: WorkspaceId) -> WorkspaceMetrics:
        response = await self._client.get(_ws_path(ws_id, "/metrics"))
        return workspace_metrics_from_dto(
            dto.MetricsEnvelope.model_validate(response.json()), ws_id=ws_id
        )

    # -- processes (Phase 2e) ------------------------------------------------
    async def list_processes(self, ws_id: WorkspaceId) -> ProcessList:
        response = await self._client.get(_ws_path(ws_id, "/processes"))
        return process_list_from_dto(
            dto.ProcessListEnvelope.model_validate(response.json())
        )

    async def stop_process(
        self, ws_id: WorkspaceId, process_id: str
    ) -> ProcessStopResult:
        # A second stop of the same id is a 404 (the process left the inventory);
        # it is a creation POST so it is never auto-retried.
        response = await self._client.post(
            _ws_path(ws_id, f"/processes/{process_id}/stop")
        )
        return process_stop_from_dto(
            dto.ProcessStopEnvelope.model_validate(response.json())
        )

    # -- archives (Phase 2g: the one multipart + one raw-bytes route) --------
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
        # Multipart: the archive is untrusted input, detected from magic bytes.
        # Never routed through the JSON helper.
        form = {"path": path}
        if overwrite:
            form["overwrite"] = "true"
        if dry_run:
            form["dry_run"] = "true"
        response = await self._client.post(
            _ws_path(ws_id, "/files/archive"),
            data=form,
            files={"archive": (filename, data)},
            timeout=ARCHIVE_TIMEOUT_S,
        )
        return archive_result_from_dto(
            dto.ArchiveResultDTO.model_validate(response.json())
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
        body: dict[str, Any] = {"path": path, "overwrite": overwrite, "dry_run": dry_run}
        if dest:
            body["dest"] = dest
        response = await self._client.post(
            _ws_path(ws_id, "/files/extract"),
            json=body,
            timeout=ARCHIVE_TIMEOUT_S,
        )
        return archive_result_from_dto(
            dto.ArchiveResultDTO.model_validate(response.json())
        )

    async def export_archive(self, ws_id: WorkspaceId, path: str) -> bytes:
        # The export streams ZIP bytes; read them raw, never through JSON.
        response = await self._client.get(
            _ws_path(ws_id, "/files/archive"),
            params={"path": path},
            timeout=ARCHIVE_TIMEOUT_S,
        )
        return response.content


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or "/"


def decode_base64(text: str) -> str:
    return base64.b64decode(text).decode("utf-8", errors="replace")
