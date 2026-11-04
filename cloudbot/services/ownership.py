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

    async def assert_admin_override(self, admin_id: int, ws_id: WorkspaceId) -> None:
        """THE one place the admin read/write exemption is expressed.

        An admin may read and act on other users' workspaces *by design* — that is
        the entire purpose of the panel (see ``docs/ADMIN-PANEL.md``). Expressed
        here as a single named method so the exemption is not sprinkled as
        ``if is_admin`` through the services. Only ``AdminService`` calls this;
        every user-facing path still goes through ``assert_owned``.

        It is intentionally a no-op that does not even look at ``ws_id``: the
        caller has already been proven an admin by the identity gate
        (``middleware/admin_gate.py``) before any handler runs.
        """
        return None
