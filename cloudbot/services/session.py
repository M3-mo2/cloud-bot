"""The persisted workspace session.

Opening a workspace is *entering* it, like logging in; ``/start`` puts the user
back inside it hours later; a "log out of workspace" button leaves and switches.
Because "press /start again and you are back inside" must survive a restart, a
redeploy and a brand-new process, the session is a **column on ``users``** — never
aiogram FSM state.

This service is the *only* place the session rules live. A handler never reads
``current_ws_id``; it calls :meth:`enter`, :meth:`leave` or :meth:`current`:

* :meth:`enter` refuses unless the caller owns the workspace (a foreign or unknown
  id is refused identically).
* :meth:`leave` clears it; leaving when not inside is a success.
* :meth:`current` validates that the workspace still exists and is still owned. A
  deleted workspace — the admin panel's ``delete`` action, or a user delete —
  **self-heals**: the column is cleared and "not inside" is returned. It never
  surfaces a stale id and never raises for a stale one.
"""

from __future__ import annotations

from cloudbot.core.errors import EngineRefused, NotOwned
from cloudbot.domain.entities import Workspace
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.users import UserRepository
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.services.ownership import OwnershipService


class SessionService:
    def __init__(
        self,
        users: UserRepository,
        engine: EnginePort,
        ownership: OwnershipService,
    ) -> None:
        self._users = users
        self._engine = engine
        self._ownership = ownership

    async def enter(self, tg_id: int, ws_id: WorkspaceId) -> None:
        """Enter ``ws_id`` for ``tg_id``, or refuse.

        Enforced through :class:`OwnershipService` so a foreign id and a
        nonexistent id are refused the same way (``NotOwned``), matching the
        repo's isolation rule.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._users.set_current_workspace(tg_id, ws_id)

    async def leave(self, tg_id: int) -> None:
        """Leave whatever workspace ``tg_id`` is inside. Idempotent."""
        await self._users.set_current_workspace(tg_id, None)

    async def current(self, tg_id: int) -> Workspace | None:
        """The workspace ``tg_id`` is inside, or ``None``.

        Self-heals a dangling session: if the workspace is no longer owned (the
        ownership row is gone — a user delete or the admin panel's delete) or the
        engine no longer has it, the stored id is cleared and ``None`` is
        returned. Never raises for a stale session.
        """
        ws_id = await self._users.current_workspace(tg_id)
        if ws_id is None:
            return None
        if not await self._users.owns(tg_id, ws_id):
            await self._clear(tg_id)
            return None
        try:
            return await self._engine.get_workspace(ws_id)
        except EngineRefused as exc:
            if exc.engine_code in ("not_found", "forbidden"):
                await self._users.remove_workspace(tg_id, ws_id)
                await self._clear(tg_id)
                return None
            return None
        except NotOwned:  # defensive: should not happen past the owns check
            await self._clear(tg_id)
            return None

    async def _clear(self, tg_id: int) -> None:
        try:
            await self._users.set_current_workspace(tg_id, None)
        except Exception:  # noqa: BLE001 - a failed cleanup must not crash a read
            pass
