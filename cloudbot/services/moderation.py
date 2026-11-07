"""``ModerationService`` — ban users, ban workspaces, message a user.

This is the **one** place the two ban rules live:

* **user bans** are read by the ban middleware, which refuses a banned user
  everywhere (including ``/start``), before any handler runs. A temporary ban
  expires by *time*: ``expires_at`` in the past is simply not active, so no
  unban is needed;
* **workspace bans** are enforced here, at the service boundary: every execution
  path (terminal, run button, API, scheduler) calls :meth:`assert_executable`
  before touching the engine. A workspace ban is not the engine's ``suspend``:
  it is an admin decision the owner cannot lift.

Every mutation is also written to the shared audit log by the handler's intent
buffer; this service only states the domain facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from cloudbot.core.errors import CannotBanAdmin, UserBanned, WorkspaceBanned
from cloudbot.domain.moderation import UserBan, WorkspaceBan
from cloudbot.domain.ports.moderation import ModerationStore
from cloudbot.domain.value_objects import WorkspaceId

log = structlog.get_logger("cloudbot.moderation")

class ModerationService:
    """The ban rules. ``protected_ids`` is the operator's admin allowlist, passed
    in from composition so an admin is never bannable — the service never reads
    the identity configuration itself."""

    def __init__(self, store: ModerationStore, *, protected_ids: "frozenset[int] | set[int] | None" = None) -> None:
        self._store = store
        self._protected_ids = frozenset(protected_ids or ())

    # -- user bans -----------------------------------------------------------
    def active_ban(self, ban: UserBan | None, *, now: datetime | None = None) -> UserBan | None:
        """The ban if it still applies, else ``None`` (a past instant has expired)."""
        if ban is None:
            return None
        now = now or datetime.now(timezone.utc)
        return ban if ban.is_active(now) else None

    async def user_ban(self, tg_id: int) -> UserBan | None:
        return await self._store.user_ban(tg_id)

    async def active_user_ban(self, tg_id: int, *, now: datetime | None = None) -> UserBan | None:
        return self.active_ban(await self._store.user_ban(tg_id), now=now)

    async def assert_not_banned(self, tg_id: int, *, now: datetime | None = None) -> None:
        ban = await self.active_user_ban(tg_id, now=now)
        if ban is not None:
            raise UserBanned(ban.reason, ban.expires_at)

    async def ban_user(
        self,
        tg_id: int,
        *,
        reason: str,
        banned_by: int,
        days: int | None = None,
        now: datetime | None = None,
    ) -> UserBan:
        """Ban ``tg_id``. An admin is never bannable (the last-admin anti-lockout)."""
        if tg_id in self._protected_ids:
            raise CannotBanAdmin()
        now = now or datetime.now(timezone.utc)
        expires_at = now + timedelta(days=days) if days else None
        return await self._store.ban_user(
            tg_id,
            reason=reason,
            banned_by=banned_by,
            now=now,
            expires_at=expires_at,
        )

    async def unban_user(self, tg_id: int) -> bool:
        return await self._store.unban_user(tg_id)

    async def banned_users(self) -> list[UserBan]:
        return await self._store.banned_users()

    # -- workspace bans ------------------------------------------------------
    async def workspace_ban(self, ws_id: WorkspaceId | str) -> WorkspaceBan | None:
        return await self._store.workspace_ban(str(ws_id))

    async def assert_executable(self, ws_id: WorkspaceId | str) -> None:
        """Refuse a new execution on a banned workspace, with the admin's reason.

        Callers that start work (runs, exec, terminals, scheduler) call this at
        the **service** boundary, so a path with no UI involved is refused too.
        """
        ban = await self._store.workspace_ban(str(ws_id))
        if ban is not None:
            raise WorkspaceBanned(str(ws_id), ban.reason)

    async def ban_workspace(
        self,
        ws_id: WorkspaceId | str,
        *,
        reason: str,
        banned_by: int,
        now: datetime | None = None,
    ) -> WorkspaceBan:
        now = now or datetime.now(timezone.utc)
        return await self._store.ban_workspace(
            str(ws_id), reason=reason, banned_by=banned_by, now=now
        )

    async def unban_workspace(self, ws_id: WorkspaceId | str) -> bool:
        return await self._store.unban_workspace(str(ws_id))

    async def banned_workspaces(self) -> list[WorkspaceBan]:
        return await self._store.banned_workspaces()
