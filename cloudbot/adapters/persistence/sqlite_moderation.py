"""SQLite implementation of ``ModerationStore``."""

from __future__ import annotations

from datetime import datetime, timezone

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import parse_dt
from cloudbot.domain.moderation import UserBan, WorkspaceBan

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

class SqliteModerationStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- user bans -----------------------------------------------------------
    async def ban_user(
        self,
        tg_id: int,
        *,
        reason: str,
        banned_by: int,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> UserBan:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO user_bans (tg_id, reason, banned_at, banned_by, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET reason = excluded.reason, "
            "banned_at = excluded.banned_at, banned_by = excluded.banned_by, "
            "expires_at = excluded.expires_at",
            (
                tg_id,
                reason,
                _iso(now),
                banned_by,
                _iso(expires_at) if expires_at is not None else None,
            ),
        )
        await conn.commit()
        return UserBan(
            tg_id=tg_id,
            reason=reason,
            banned_at=now,
            banned_by=banned_by,
            expires_at=expires_at,
        )

    async def unban_user(self, tg_id: int) -> bool:
        conn = await self._db.connect()
        cur = await conn.execute("DELETE FROM user_bans WHERE tg_id = ?", (tg_id,))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def user_ban(self, tg_id: int) -> UserBan | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM user_bans WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user_ban(row) if row is not None else None

    async def banned_users(self) -> list[UserBan]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM user_bans ORDER BY banned_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_user_ban(r) for r in rows]

    # -- workspace bans ------------------------------------------------------
    async def ban_workspace(
        self,
        ws_id: str,
        *,
        reason: str,
        banned_by: int,
        now: datetime,
    ) -> WorkspaceBan:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO workspace_bans (ws_id, reason, banned_at, banned_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ws_id) DO UPDATE SET reason = excluded.reason, "
            "banned_at = excluded.banned_at, banned_by = excluded.banned_by",
            (str(ws_id), reason, _iso(now), banned_by),
        )
        await conn.commit()
        return WorkspaceBan(
            ws_id=str(ws_id), reason=reason, banned_at=now, banned_by=banned_by
        )

    async def unban_workspace(self, ws_id: str) -> bool:
        conn = await self._db.connect()
        cur = await conn.execute(
            "DELETE FROM workspace_bans WHERE ws_id = ?", (str(ws_id),)
        )
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def workspace_ban(self, ws_id: str) -> WorkspaceBan | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM workspace_bans WHERE ws_id = ?", (str(ws_id),)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_workspace_ban(row) if row is not None else None

    async def banned_workspaces(self) -> list[WorkspaceBan]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM workspace_bans ORDER BY banned_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_workspace_ban(r) for r in rows]

def _row_to_user_ban(row) -> UserBan:
    expires = row["expires_at"]
    return UserBan(
        tg_id=int(row["tg_id"]),
        reason=str(row["reason"] or ""),
        banned_at=parse_dt(row["banned_at"]),
        banned_by=int(row["banned_by"]),
        expires_at=parse_dt(expires) if expires else None,
    )

def _row_to_workspace_ban(row) -> WorkspaceBan:
    return WorkspaceBan(
        ws_id=str(row["ws_id"]),
        reason=str(row["reason"] or ""),
        banned_at=parse_dt(row["banned_at"]),
        banned_by=int(row["banned_by"]),
    )
