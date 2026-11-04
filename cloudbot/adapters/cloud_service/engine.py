"""``EnginePort`` implementation over the HTTP client.

This is the only module that knows the Cloud Service wire format. It maps domain
calls to HTTP and responses to domain entities, and refreshes ``/v1/meta`` on a
``422 unprocessable`` refusal (usually a stale cached cap), retrying once.
"""

from __future__ import annotations

import base64
from typing import Any, AsyncIterator

import httpx

from cloudbot.adapters.cloud_service import dto
from cloudbot.adapters.cloud_service.client import EngineClient
from cloudbot.adapters.cloud_service.mapping import (
    exec_result_from_dto,
    file_content_from_dto,
    file_page_from_dto,
    file_entry_from_dto,
    lifecycle_from_dto,
    log_chunk_from_dto,
    run_from_dto,
    run_page_from_dto,
    usage_from_dto,
    workspace_from_dto,
)
from cloudbot.adapters.cloud_service.meta import MetaCache
from cloudbot.core.errors import EngineRefused
from cloudbot.domain.entities import FileEntry, ResourceUsage, Run, Workspace
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import (
    ExecResult,
    FileContent,
    FilePage,
    LifecycleResult,
    Limits,
    LogChunk,
    RunPage,
    WorkspaceId,
)

#: exec is hard-capped at 60 s by the engine.
EXEC_HARD_CAP_SEC = 60
#: Default page size for file listings (UI 03 uses 10–20 per page).
FILE_PAGE_LIMIT = 20


def _ws_path(ws_id: WorkspaceId, suffix: str = "") -> str:
    return f"/v1/workspaces/{ws_id}{suffix}"


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
        response = await self._client.post("/v1/workspaces", json=body)
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
        response = await self._client.patch(_ws_path(ws_id), json=body)
        env = dto.WorkspaceEnvelope.model_validate(response.json())
        return workspace_from_dto(env.workspace)

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
        return file_entry_from_dto(
            dto.FileEntryDTO(
                name=_basename(path),
                path=path,
                type=response.json().get("type", "file"),
                size=response.json().get("size"),
                mode=response.json().get("mode"),
                mod_time=response.json().get("mod_time"),
                runnable=response.json().get("runnable"),
            )
        )

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
        """Run a call; on ``unprocessable`` refresh meta and retry once."""
        try:
            return await coro_factory()
        except EngineRefused as exc:
            if exc.engine_code == "unprocessable":
                await self._meta.refresh()
                return await coro_factory()
            raise

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


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or "/"


def decode_base64(text: str) -> str:
    return base64.b64decode(text).decode("utf-8", errors="replace")
