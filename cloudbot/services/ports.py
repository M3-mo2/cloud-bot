"""Port-discovery use-cases (Phase 2c): read the listening endpoints.

A read-only introspection of the caller's own workspace; the ownership gate runs
first like every other workspace-scoped call. The engine's ``reachability`` note
and the ``complete`` flag are carried through untouched — the screen must surface
the reachability sentence verbatim and say when the list is only a partial view.
"""

from __future__ import annotations

from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import PortReport, WorkspaceId
from cloudbot.services.ownership import OwnershipService


class PortsService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def list(self, tg_id: int, ws_id: WorkspaceId) -> PortReport:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_ports(ws_id)
