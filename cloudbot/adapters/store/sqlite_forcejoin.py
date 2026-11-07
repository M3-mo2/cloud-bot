"""SQLite implementation of ``ForceJoinStore``.

Two tables (see migration ``0008_forcejoin.sql``): ``forcejoin_chats`` for the
required chats, ``runtime_flags`` for runtime switches (the force-join gate has
``forcejoin.enabled``; a missing row means **on**, so a fresh database behaves as
the operator expects once a chat is added).
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from cloudbot.adapters.persistence.database import Database
from cloudbot.domain.forcejoin import GATE_FLAG, ChatKind, ForceJoinChat

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_dt(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _row_to_chat(row: aiosqlite.Row) -> ForceJoinChat:
    kind: ChatKind = "group" if str(row["kind"]) == "group" else "channel"
    return ForceJoinChat(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        username=str(row["username"] or ""),
        title=str(row["title"] or ""),
        kind=kind,
        bot_is_admin=bool(row["is_admin"]),
        enabled=bool(row["enabled"]),
        added_at=_parse_dt(row["added_at"]),
        added_by=int(row["added_by"]),
    )

class SqliteForceJoinStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def chats(self) -> list[ForceJoinChat]:
        conn = await self._db.connect()
        async with conn.execute("SELECT * FROM forcejoin_chats ORDER BY id") as cur:
            rows = await cur.fetchall()
        return [_row_to_chat(r) for r in rows]

    async def enabled_chats(self) -> list[ForceJoinChat]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM forcejoin_chats WHERE enabled = 1 ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_chat(r) for r in rows]

    async def get(self, chat_id: int) -> ForceJoinChat | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM forcejoin_chats WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_chat(row) if row is not None else None

    async def add(
        self,
        *,
        chat_id: int,
        username: str,
        title: str,
        kind: ChatKind,
        bot_is_admin: bool,
        added_by: int,
    ) -> ForceJoinChat:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO forcejoin_chats "
            "(chat_id, username, title, kind, is_admin, enabled, added_at, added_by) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "username = excluded.username, title = excluded.title, "
            "kind = excluded.kind, is_admin = excluded.is_admin, "
            "enabled = 1",
            (
                chat_id,
                username,
                title,
                kind,
                1 if bot_is_admin else 0,
                _utc_now_iso(),
                added_by,
            ),
        )
        await conn.commit()
        stored = await self.get(chat_id)
        assert stored is not None
        return stored

    async def remove(self, chat_id: int) -> None:
        conn = await self._db.connect()
        await conn.execute("DELETE FROM forcejoin_chats WHERE chat_id = ?", (chat_id,))
        await conn.commit()

    async def set_enabled(self, chat_id: int, enabled: bool) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE forcejoin_chats SET enabled = ? WHERE chat_id = ?",
            (1 if enabled else 0, chat_id),
        )
        await conn.commit()

    async def set_admin(self, chat_id: int, bot_is_admin: bool) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE forcejoin_chats SET is_admin = ? WHERE chat_id = ?",
            (1 if bot_is_admin else 0, chat_id),
        )
        await conn.commit()

    async def flag(self, key: str, default: bool) -> bool:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT value FROM runtime_flags WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return default
        return str(row["value"]) not in ("0", "false", "off", "")

    async def set_flag(self, key: str, value: bool) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO runtime_flags (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, "1" if value else "0", _utc_now_iso()),
        )
        await conn.commit()
