"""Process-inventory use-cases (Phase 2e): list and stop one process tree.

The ownership gate runs first like every workspace-scoped call. A process is
addressed by its stable ``proc_<hex>`` id — never a raw pid (a pid can be
reused), so the id's shape is validated locally before it reaches the engine.

Stop is **honest and idempotent**: the engine returns ``stopped``/``true`` for a
live tree and ``gone``/``false`` for one that had already exited; a *second* stop
returns ``404`` because the process left the inventory, which the service folds
back into the same ``gone`` outcome so the screen never shows an error.

The engine stops the process **tree** via the sandbox cgroup, so a workload that
called ``os.setsid()`` is still stopped; the client never signals a pid itself.
"""

from __future__ import annotations

import re

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import (
    ProcessList,
    ProcessStopResult,
    WorkspaceId,
)
from cloudbot.services.ownership import OwnershipService

#: The engine's stable process id is ``proc_`` + lowercase hex. A raw pid must
#: never be sent as the address (it can be reused), so the shape is enforced.
_SAFE_PROCESS_ID = re.compile(r"^proc_[0-9a-f]+$")


class ProcessesService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def list(self, tg_id: int, ws_id: WorkspaceId) -> ProcessList:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_processes(ws_id)

    async def stop(
        self, tg_id: int, ws_id: WorkspaceId, process_id: str
    ) -> ProcessStopResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        clean = validate_process_id(process_id)
        try:
            return await self._engine.stop_process(ws_id, clean)
        except EngineRefused as exc:
            # A repeat stop is a 404 because the process has left the inventory.
            # That is "already gone", not a failure.
            if exc.engine_code == "not_found":
                return ProcessStopResult(
                    process_id=clean, status="gone", stopped=False
                )
            raise


def validate_process_id(process_id: str) -> str:
    """Return the id if it matches the engine's ``proc_<hex>`` shape."""
    clean = (process_id or "").strip()
    if not clean:
        raise InvalidInput("process", "empty")
    if not _SAFE_PROCESS_ID.match(clean):
        raise InvalidInput("process", "invalid")
    return clean
