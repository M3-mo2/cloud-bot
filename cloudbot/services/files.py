"""File use-cases: list, view, write, delete, mkdir, rename, upload.

Encodes binary safely and enforces the upload cap before a wasted round trip.
"""

from __future__ import annotations

from typing import AsyncIterator

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import FileContent, FilePage, WorkspaceId
from cloudbot.services.ownership import OwnershipService

#: Listing page size — UI 03 pages at 10–20 rows.
LIST_PAGE_LIMIT = 20
#: Rich-message code blocks are bounded; keep the tail.
MAX_PREVIEW_BYTES = 12 * 1024


class FilesService:
    def __init__(
        self,
        engine: EnginePort,
        ownership: OwnershipService,
        *,
        subscription: "object | None" = None,
    ) -> None:
        self._engine = engine
        self._ownership = ownership
        self._subscription = subscription

    async def _assert_writable(self, tg_id: int, ws_id: WorkspaceId) -> None:
        """Refuse a mutation on a workspace whose plan is not active.

        Reads stay allowed (the engine allows them while locked and a user's files
        must remain viewable); only writes/deletes/moves/upload are refused here.
        The engine also refuses mutating file operations on a locked workspace
        (``403 subscription_expired``); this is the client-side half, so a path with
        no UI cannot bypass it.
        """
        if self._subscription is not None:
            await self._subscription.assert_usable(tg_id, ws_id)

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def list(
        self, tg_id: int, ws_id: WorkspaceId, path: str, *, offset: int = 0, limit: int = LIST_PAGE_LIMIT
    ) -> FilePage:
        await self._ownership.assert_owned(tg_id, ws_id)
        normalized = _normalize_path(path)
        return await self._engine.list_files(ws_id, normalized, offset=offset, limit=limit)

    async def view(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        path: str,
        *,
        line_range: tuple[int, int] | None = None,
    ) -> FileContent:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.read_file(ws_id, path, line_range=line_range, meta=True)

    async def write(
        self, tg_id: int, ws_id: WorkspaceId, path: str, content: str, *, encoding: str = "text"
    ) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)
        await self._engine.write_file(ws_id, path, content, encoding=encoding)

    async def mkdir(self, tg_id: int, ws_id: WorkspaceId, path: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)
        if not path or path == "/":
            raise InvalidInput("path", "empty")
        await self._engine.mkdir(ws_id, path)

    async def rename(self, tg_id: int, ws_id: WorkspaceId, src: str, dst: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)
        if not dst or dst == "/":
            raise InvalidInput("path", "empty")
        await self._engine.rename(ws_id, src, dst)

    async def delete(self, tg_id: int, ws_id: WorkspaceId, path: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)
        await self._engine.delete_file(ws_id, path)

    def check_upload_size(self, size_bytes: int, meta: EngineMeta) -> None:
        cap_bytes = meta.upload_mb * 1024 * 1024
        if cap_bytes and size_bytes > cap_bytes:
            raise InvalidInput("upload", "too_large")

    async def upload(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        path: str,
        filename: str,
        stream: AsyncIterator[bytes],
        *,
        size_bytes: int | None = None,
    ) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)
        if size_bytes is not None:
            meta = await self._engine.meta()
            self.check_upload_size(size_bytes, meta)
        await self._engine.upload_file(ws_id, path, filename, stream)


def normalize_path(path: str) -> str:
    """Resolve a user-typed workspace path to an absolute one (``/`` root).

    Shared by the file and archive services so an archive source path obeys the
    same rules a file path does.
    """
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _normalize_path(path: str) -> str:
    return normalize_path(path)


def tail_preview(text: str, max_bytes: int = MAX_PREVIEW_BYTES) -> tuple[str, bool]:
    """Return ``(tail_text, trimmed)`` bounded to the last ``max_bytes``."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    tail = encoded[-max_bytes:]
    return tail.decode("utf-8", errors="ignore"), True
