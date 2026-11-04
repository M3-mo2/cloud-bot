"""SQLite implementation of ``UserRepository`` — the ownership registry."""

from __future__ import annotations

import aiosqlite

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import row_to_user, utc_now_iso
from cloudbot.core.locale import Locale
from cloudbot.domain.entities import User
from cloudbot.domain.value_objects import WorkspaceId


class SqliteUserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_or_create(self, tg_id: int, *, default_locale: Locale) -> User:
        conn = await self._db.connect()
        now = utc_now_iso()
        # Single transaction: insert-if-absent then select, so concurrent first
        # messages cannot create duplicates.
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at) "
                "VALUES (?, ?, 'free', ?, ?) "
                "ON CONFLICT(tg_id) DO NOTHING",
                (tg_id, default_locale.value, now, now),
            )
            await conn.execute(
                "UPDATE users SET last_seen_at = ? WHERE tg_id = ?", (now, tg_id)
            )
            async with conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
                row = await cur.fetchone()
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        assert row is not None
        return row_to_user(row)

    async def get(self, tg_id: int) -> User | None:
        conn = await self._db.connect()
        async with conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
        return row_to_user(row) if row is not None else None

    async def set_locale(self, tg_id: int, locale: Locale) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE users SET locale = ?, last_seen_at = ? WHERE tg_id = ?",
            (locale.value, utc_now_iso(), tg_id),
        )
        await conn.commit()

    async def add_workspace(self, tg_id: int, ws_id: WorkspaceId) -> None:
        conn = await self._db.connect()
        now = utc_now_iso()
        # Ensure the owner row exists so the foreign key holds even if the
        # workspace is registered before an explicit get_or_create.
        await conn.execute(
            "INSERT INTO users (tg_id, created_at, last_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id) DO NOTHING",
            (tg_id, now, now),
        )
        await conn.execute(
            "INSERT INTO user_workspaces (tg_id, ws_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id, ws_id) DO NOTHING",
            (tg_id, str(ws_id), utc_now_iso()),
        )
        await conn.commit()

    async def remove_workspace(self, tg_id: int, ws_id: WorkspaceId) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "DELETE FROM user_workspaces WHERE tg_id = ? AND ws_id = ?",
            (tg_id, str(ws_id)),
        )
        await conn.commit()

    async def owned_workspaces(self, tg_id: int) -> list[WorkspaceId]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT ws_id FROM user_workspaces WHERE tg_id = ? ORDER BY created_at",
            (tg_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [WorkspaceId(str(r["ws_id"])) for r in rows]

    async def owns(self, tg_id: int, ws_id: WorkspaceId) -> bool:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT EXISTS(SELECT 1 FROM user_workspaces WHERE tg_id = ? AND ws_id = ?)",
            (tg_id, str(ws_id)),
        ) as cur:
            row = await cur.fetchone()
        return bool(row[0]) if row is not None else False
