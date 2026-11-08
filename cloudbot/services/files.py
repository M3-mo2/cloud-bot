"""File use-cases: list, view, write, delete, mkdir, rename, upload, create many.

Encodes binary safely and enforces the upload cap before a wasted round trip.

``create_many`` is the one multi-file write (starter templates): it checks every
target path for a collision **before** it writes anything, and it reports exactly
how far it got if the engine refuses part-way. The engine has no transaction for
a set of writes (INTEGRATION-GUIDE §3 — one file per ``PUT``), so atomicity is
never claimed: the caller is told the truth about partial state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import FileContent, FilePage, WorkspaceId
from cloudbot.services.ownership import OwnershipService


class TemplatePathsExist(InvalidInput):
    """A multi-file create was refused because target paths already exist.

    Carries the conflicting paths in order so the refusal screen can name every
    one; nothing was written when this is raised. A starter template passes its
    **folder** as the guarded path, so the refusal names the folder rather than
    one file inside it.
    """

    code = "template_paths_exist"

    def __init__(self, paths: list[str]) -> None:
        super().__init__("template", "exists")
        self.paths = tuple(paths)


#: Listing page size — UI 03 pages at 10–20 rows.
LIST_PAGE_LIMIT = 20
#: Rich-message code blocks are bounded; keep the tail.
MAX_PREVIEW_BYTES = 12 * 1024

#: Engine codes that mean "the path is absent" for a ``files/stat`` probe. The
#: real engine answers 404 ``not_found``; ``not_directory`` (422) means an
#: ancestor is a file, which also means the target path is not an existing file.
_MISSING_CODES = frozenset({"not_found", "not_directory"})


@dataclass(frozen=True)
class FileWrite:
    """One file to write: ``path`` absolute in the workspace, ``content`` text."""

    path: str
    content: str


@dataclass(frozen=True)
class CreateManyResult:
    """The honest outcome of writing several files.

    ``written`` are the paths that landed, in order; ``failed_path``,
    ``error_code`` and ``message`` are set when the engine refused a write after
    some had already landed (partial state is stated, never implied).
    """

    written: tuple[str, ...] = ()
    failed_path: str | None = None
    error_code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.failed_path is None


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

    async def exists(self, tg_id: int, ws_id: WorkspaceId, path: str) -> bool:
        """Whether ``path`` names a file that already exists.

        A ``files/stat`` probe; a ``not_found`` (or ``not_directory``) answer is
        a clean "no", never an error. Any other refusal propagates so the caller
        cannot mistake an engine outage for an absent file and overwrite it.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        try:
            await self._engine.stat_file(ws_id, path)
        except EngineRefused as exc:
            if exc.engine_code in _MISSING_CODES:
                return False
            raise
        # Any stat success means the path exists — file, directory or symlink.
        # A directory on the target path is a collision too: writing over it would
        # be refused by the engine (``not_directory``) after earlier files landed.
        return True

    async def existing(self, tg_id: int, ws_id: WorkspaceId, paths: list[str]) -> list[str]:
        """The subset of ``paths`` that already exists, in the given order."""
        found: list[str] = []
        for path in paths:
            if await self.exists(tg_id, ws_id, path):
                found.append(path)
        return found

    async def folder_conflicts(
        self, tg_id: int, ws_id: WorkspaceId, folder: str, paths: list[str]
    ) -> list[str]:
        """The template folder if it — or a file inside it — already exists.

        A starter template owns a whole directory, so a collision inside it is
        still a collision on the folder: the refusal names ``folder`` and never
        one file within it. A *different* template's folder is untouched, which
        is what lets two starters coexist.
        """
        if await self.exists(tg_id, ws_id, folder):
            return [folder]
        if await self.existing(tg_id, ws_id, list(paths)):
            return [folder]
        return []

    async def create_many(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        files: list[FileWrite],
        *,
        overwrite: bool = False,
        folder: str | None = None,
    ) -> CreateManyResult:
        """Write several files, refusing the whole set on any collision.

        Every rule the single write enforces still holds: ownership, the plan
        gate, and the engine's own path/size checks (the content goes through
        ``write_file`` unchanged). The collision check runs **first**, so with
        ``overwrite=False`` a template can never silently overwrite a user's
        file: nothing is written and no path is touched.

        ``folder`` is for a caller that owns a whole directory (a starter
        template): the guard then asks about the folder itself **and** any file
        already inside it, so the one refusal names the folder rather than one
        file within it. A *different* template writing into its own folder is not
        a collision. Without ``folder`` the target file paths are the guard.

        There is no engine transaction across a set of ``PUT``s, so a refusal
        part-way is reported honestly — ``written`` lists what landed and
        ``failed_path`` / ``error_code`` name where it stopped.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_writable(tg_id, ws_id)

        if not overwrite:
            if folder is not None:
                conflicts = await self.folder_conflicts(
                    tg_id, ws_id, folder, [f.path for f in files]
                )
            else:
                conflicts = await self.existing(tg_id, ws_id, [f.path for f in files])
            if conflicts:
                raise TemplatePathsExist(conflicts)

        written: list[str] = []
        for item in files:
            try:
                await self._engine.write_file(ws_id, item.path, item.content)
            except EngineRefused as exc:
                return CreateManyResult(
                    written=tuple(written),
                    failed_path=item.path,
                    error_code=exc.engine_code,
                    message=getattr(exc, "message", "") or "",
                )
            written.append(item.path)
        return CreateManyResult(written=tuple(written))


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
