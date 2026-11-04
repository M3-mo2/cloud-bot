"""The isolation gate.

``assert_owned(tg_id, ws_id)`` raises ``NotOwned`` unless the repository
confirms ownership. Every workspace-scoped service method calls this first. A
workspace not in the caller's set is treated as absent (identical to a real
``not_found``) so ids cannot be probed.
"""

from __future__ import annotations

from cloudbot.core.errors import NotOwned
from cloudbot.domain.ports.users import UserRepository
from cloudbot.domain.value_objects import WorkspaceId


class OwnershipService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def assert_owned(self, tg_id: int, ws_id: WorkspaceId) -> None:
        if not await self._users.owns(tg_id, ws_id):
            raise NotOwned(str(ws_id))

    async def owns(self, tg_id: int, ws_id: WorkspaceId) -> bool:
        return await self._users.owns(tg_id, ws_id)
