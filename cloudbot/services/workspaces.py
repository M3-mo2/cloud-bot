"""Workspace use-cases: list, create, get, patch, lifecycle, usage.

Policy lives here: ownership checks, limit pre-validation against
``EngineMeta.limits_maxima``, and call ordering. No IO libraries.
"""

from __future__ import annotations

import structlog

from cloudbot.core.errors import EngineRefused, InvalidInput, NotOwned
from cloudbot.domain.entities import Workspace
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.users import UserRepository
from cloudbot.domain.value_objects import LifecycleResult, Limits, ResourceUsage, WorkspaceId
from cloudbot.services.ownership import OwnershipService

log = structlog.get_logger("cloudbot.workspaces")

MAX_NAME_LEN = 128

_LIMIT_MAX_ATTRS = {
    "cpu_cores": "cpu_cores",
    "memory_mb": "memory_mb",
    "disk_mb": "disk_mb",
    "max_wall_time_sec": "max_wall_time_sec",
    "max_runs": "max_runs",
}


class WorkspacesService:
    def __init__(
        self,
        engine: EnginePort,
        users: UserRepository,
        ownership: OwnershipService,
    ) -> None:
        self._engine = engine
        self._users = users
        self._ownership = ownership

    async def meta(self, refresh: bool = False) -> EngineMeta:
        return await self._engine.meta(refresh=refresh)

    def validate_name(self, name: str) -> str:
        stripped = (name or "").strip()
        if not stripped:
            raise InvalidInput("name", "empty")
        if len(stripped) > MAX_NAME_LEN:
            raise InvalidInput("name", "too_long")
        return stripped

    def validate_limits(self, limits: Limits | None, maxima: Limits) -> Limits | None:
        """Refuse values above maxima *before* calling the engine (never clamp)."""
        if limits is None:
            return None
        for field, attr in _LIMIT_MAX_ATTRS.items():
            value = getattr(limits, field)
            maximum = getattr(maxima, attr)
            if value > maximum:
                raise InvalidInput(field, "above_max")
        return limits

    async def list(self, tg_id: int) -> list[Workspace]:
        owned = set(await self._users.owned_workspaces(tg_id))
        if not owned:
            return []
        workspaces = await self._engine.list_workspaces()
        by_id = {str(w.id): w for w in workspaces}
        result: list[Workspace] = []
        for ws_id in owned:
            ws = by_id.get(str(ws_id))
            if ws is not None:
                result.append(self._with_owner(ws, tg_id))
        return result

    async def create(
        self,
        tg_id: int,
        name: str,
        *,
        profile: str | None = None,
        limits: Limits | None = None,
    ) -> Workspace:
        meta = await self._engine.meta()
        clean_name = self.validate_name(name)
        self.validate_limits(limits, meta.limits_maxima)
        selected_profile = profile or meta.default_profile
        ws = await self._engine.create_workspace(
            name=clean_name, profile=selected_profile, limits=limits
        )
        try:
            await self._users.add_workspace(tg_id, ws.id)
        except Exception:
            # Compensate: delete the orphan we just created, then re-raise.
            log.error("workspace.registration_failed", ws_id=str(ws.id))
            try:
                await self._engine.delete_workspace(ws.id)
            except Exception:  # noqa: BLE001
                log.error("workspace.compensation_failed", ws_id=str(ws.id))
            raise
        return self._with_owner(ws, tg_id)

    async def get(self, tg_id: int, ws_id: WorkspaceId) -> Workspace:
        await self._ownership.assert_owned(tg_id, ws_id)
        try:
            ws = await self._engine.get_workspace(ws_id)
        except EngineRefused as exc:
            if exc.engine_code in ("not_found", "forbidden"):
                # Lazy reconcile: the engine no longer has it.
                await self._users.remove_workspace(tg_id, ws_id)
                raise
            raise
        return self._with_owner(ws, tg_id)

    async def patch(
        self, tg_id: int, ws_id: WorkspaceId, *, name: str | None = None, limits: Limits | None = None
    ) -> Workspace:
        await self._ownership.assert_owned(tg_id, ws_id)
        if name is not None:
            name = self.validate_name(name)
        if limits is not None:
            meta = await self._engine.meta()
            self.validate_limits(limits, meta.limits_maxima)
        ws = await self._engine.patch_workspace(ws_id, name=name, limits=limits)
        return self._with_owner(ws, tg_id)

    async def suspend(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.suspend_workspace(ws_id)

    async def resume(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.resume_workspace(ws_id)

    async def stop(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.stop_workspace(ws_id)

    async def delete(self, tg_id: int, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._engine.delete_workspace(ws_id)
        await self._users.remove_workspace(tg_id, ws_id)

    async def usage(self, tg_id: int, ws_id: WorkspaceId) -> ResourceUsage:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.workspace_usage(ws_id)

    @staticmethod
    def _with_owner(ws: Workspace, tg_id: int) -> Workspace:
        if ws.owner_tg_id == tg_id:
            return ws
        return Workspace(
            id=ws.id,
            owner_tg_id=tg_id,
            name=ws.name,
            profile=ws.profile,
            status=ws.status,
            limits=ws.limits,
            summary=ws.summary,
            created_at=ws.created_at,
            last_activity_at=ws.last_activity_at,
        )
