"""Archive use-cases (Phase 2g): upload-and-extract, in-place extract, export.

The ownership gate runs first; a source path is resolved through the **existing
file path rules** (:func:`services.files.normalize_path`) so it obeys the same
contract a file path does. An upload is checked against the engine's advertised
``archives.limits.upload_mb`` **before** the round trip.

**RAR is read-only by design** (its encoder is proprietary): extracting a
published ``.rar`` is supported, but the bot never offers to *create* one. Export
is always ZIP, and there is deliberately no ``format`` argument anywhere — the
engine detects the uploaded format from magic bytes and writes only ZIP.

The delivery boundary is narrow and documented: the *exported* ZIP and the
*archive the user uploads* are the only binary payloads this feature moves, and
they go through the existing transport seam. **General file delivery to Telegram
documents remains CB-14 (Phase 3)** and is not implemented here.
"""

from __future__ import annotations

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import ArchiveResult, WorkspaceId
from cloudbot.services.files import normalize_path
from cloudbot.services.ownership import OwnershipService


class ArchivesService:
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

    async def _assert_usable(self, tg_id: int, ws_id: WorkspaceId) -> None:
        """Refuse extract/upload on a lapsed plan; export (a read) stays allowed."""
        if self._subscription is not None:
            await self._subscription.assert_usable(tg_id, ws_id)

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def upload_and_extract(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        *,
        filename: str,
        data: bytes,
        path: str = "/",
        overwrite: bool = False,
        dry_run: bool = False,
        size_bytes: int | None = None,
    ) -> ArchiveResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_usable(tg_id, ws_id)
        meta = await self._engine.meta()
        self.check_upload_size(size_bytes if size_bytes is not None else len(data), meta)
        dest = normalize_path(path)
        return await self._engine.upload_and_extract(
            ws_id,
            filename=filename,
            data=data,
            path=dest,
            overwrite=overwrite,
            dry_run=dry_run,
        )

    async def extract(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        *,
        path: str,
        dest: str = "",
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> ArchiveResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_usable(tg_id, ws_id)
        source = resolve_source(path)
        destination = resolve_dest(dest)
        return await self._engine.extract_archive(
            ws_id,
            path=source,
            dest=destination,
            overwrite=overwrite,
            dry_run=dry_run,
        )

    async def export(
        self, tg_id: int, ws_id: WorkspaceId, path: str
    ) -> tuple[str, bytes]:
        """Export a directory as a ZIP; returns ``(suggested_name, bytes)``.

        The export is always ZIP (the engine writes only ZIP); RAR is never an
        option.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        source = resolve_source(path)
        data = await self._engine.export_archive(ws_id, source)
        return suggest_export_name(source), data

    def check_upload_size(self, size_bytes: int, meta: EngineMeta) -> None:
        cap_bytes = meta.archives.limits.upload_mb * 1024 * 1024
        if size_bytes and cap_bytes and size_bytes > cap_bytes:
            raise InvalidInput("archive", "too_large")


def resolve_source(path: str) -> str:
    """Resolve an archive source path (must name a file inside the workspace)."""
    clean = (path or "").strip()
    if not clean:
        raise InvalidInput("archive", "empty")
    return normalize_path(clean)


def resolve_dest(dest: str) -> str:
    """Resolve a destination directory (empty means the workspace root)."""
    clean = (dest or "").strip()
    if not clean:
        return ""
    return normalize_path(clean)


def suggest_export_name(path: str) -> str:
    """A safe ``.zip`` filename derived from the exported directory."""
    name = path.rstrip("/").rsplit("/", 1)[-1] or "workspace"
    # Never trust the directory name as a filename blindly.
    safe = "".join(c for c in name if c.isalnum() or c in "-_.") or "workspace"
    return f"{safe}.zip"
