"""``SessionPort`` — the persisted "which workspace am I inside" session.

The rules (ownership on enter, self-healing on read) live in
``services/session.py``; this port is the seam a handler depends on so the
column itself is never read outside the service.
"""

from __future__ import annotations

from typing import Protocol

from cloudbot.domain.entities import Workspace
from cloudbot.domain.value_objects import WorkspaceId


class SessionPort(Protocol):
    async def enter(self, tg_id: int, ws_id: WorkspaceId) -> None: ...
    async def leave(self, tg_id: int) -> None: ...
    async def current(self, tg_id: int) -> Workspace | None: ...
