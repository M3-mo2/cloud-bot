"""SQLite implementation of ``SessionStore`` (Phase 1.5)."""

from __future__ import annotations

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import row_to_session, utc_now_iso
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionId, SessionStatus, WorkspaceId


class SqliteSessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, tg_id: int, ws_id: WorkspaceId, ses_id: SessionId) -> None:
        conn = await self._db.connect()
        now = utc_now_iso()
        await conn.execute(
            "INSERT INTO terminal_sessions (tg_id, ws_id, ses_id, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tg_id, ses_id) DO NOTHING",
            (tg_id, str(ws_id), str(ses_id), now, now),
        )
        await conn.commit()

    async def get(self, tg_id: int, ses_id: SessionId) -> TerminalSession | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM terminal_sessions WHERE tg_id = ? AND ses_id = ?",
            (tg_id, str(ses_id)),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        r = row_to_session(row)
        return TerminalSession(
            id=r.ses_id,
            workspace_id=WorkspaceId(r.ws_id),
            status=SessionStatus.RUNNING,
            cols=r.cols,
            rows=r.rows,
            out_offset=r.out_offset,
        )

    async def set_offset(self, tg_id: int, ses_id: SessionId, offset: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE terminal_sessions SET out_offset = ?, last_seen_at = ? "
            "WHERE tg_id = ? AND ses_id = ?",
            (offset, utc_now_iso(), tg_id, str(ses_id)),
        )
        await conn.commit()

    async def list_for_user(self, tg_id: int) -> list[TerminalSession]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM terminal_sessions WHERE tg_id = ? ORDER BY created_at",
            (tg_id,),
        ) as cur:
            rows = await cur.fetchall()
        result: list[TerminalSession] = []
        for row in rows:
            r = row_to_session(row)
            result.append(
                TerminalSession(
                    id=r.ses_id,
                    workspace_id=WorkspaceId(r.ws_id),
                    status=SessionStatus.RUNNING,
                    cols=r.cols,
                    rows=r.rows,
                    out_offset=r.out_offset,
                )
            )
        return result

    async def delete(self, tg_id: int, ses_id: SessionId) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "DELETE FROM terminal_sessions WHERE tg_id = ? AND ses_id = ?",
            (tg_id, str(ses_id)),
        )
        await conn.commit()
