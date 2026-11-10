"""SQLite implementation of ``SessionStore`` (Phase 1.5).

The engine owns a session's live *status*; this store is the bot-side handle
(``tg_id`` ↔ ``ws_id``/``ses_id``) plus the output offset cursor and PTY size.
``get``/``list`` return the persisted handle with an ``unknown`` status, which
``SessionsService`` merges with the engine's authoritative status.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import parse_dt, row_to_session, utc_now_iso
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionId, SessionStatus, WorkspaceId


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SqliteSessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _to_entity(r) -> TerminalSession:
        ended_at = getattr(r, "ended_at", None)
        return TerminalSession(
            id=r.ses_id,
            workspace_id=WorkspaceId(r.ws_id),
            status=SessionStatus.UNKNOWN,
            cols=r.cols,
            rows=r.rows,
            out_offset=r.out_offset,
            created_at=parse_dt(r.created_at),
            ended_at=parse_dt(ended_at) if ended_at else None,
        )

    async def create(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        ses_id: SessionId,
        *,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        conn = await self._db.connect()
        now = utc_now_iso()
        await conn.execute(
            "INSERT INTO terminal_sessions (tg_id, ws_id, ses_id, cols, rows, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tg_id, ses_id) DO UPDATE SET cols = excluded.cols, rows = excluded.rows, "
            "last_seen_at = excluded.last_seen_at",
            (tg_id, str(ws_id), str(ses_id), cols, rows, now, now),
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
        return self._to_entity(row_to_session(row))

    async def set_offset(self, tg_id: int, ses_id: SessionId, offset: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE terminal_sessions SET out_offset = ?, last_seen_at = ? "
            "WHERE tg_id = ? AND ses_id = ?",
            (offset, utc_now_iso(), tg_id, str(ses_id)),
        )
        await conn.commit()

    async def update_size(
        self, tg_id: int, ses_id: SessionId, *, cols: int, rows: int
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE terminal_sessions SET cols = ?, rows = ?, last_seen_at = ? "
            "WHERE tg_id = ? AND ses_id = ?",
            (cols, rows, utc_now_iso(), tg_id, str(ses_id)),
        )
        await conn.commit()

    async def mark_ended(self, tg_id: int, ses_id: SessionId, at: datetime) -> None:
        """Stamp the bot-side clock for the grace window (idempotent: first wins)."""
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE terminal_sessions SET ended_at = ? "
            "WHERE tg_id = ? AND ses_id = ? AND ended_at IS NULL",
            (_iso(at), tg_id, str(ses_id)),
        )
        await conn.commit()

    async def delete_ended_before(
        self, tg_id: int, ws_id: WorkspaceId, cutoff: datetime
    ) -> int:
        """Forget handles whose terminal status was observed before ``cutoff``."""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT ses_id FROM terminal_sessions "
            "WHERE tg_id = ? AND ws_id = ? AND ended_at IS NOT NULL AND ended_at < ?",
            (tg_id, str(ws_id), _iso(cutoff)),
        ) as cur:
            doomed = [str(r["ses_id"]) for r in await cur.fetchall()]
        for ses_id in doomed:
            await conn.execute(
                "DELETE FROM terminal_sessions WHERE tg_id = ? AND ses_id = ?",
                (tg_id, ses_id),
            )
        if doomed:
            await conn.commit()
        return len(doomed)

    async def list_for_user(self, tg_id: int) -> list[TerminalSession]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM terminal_sessions WHERE tg_id = ? ORDER BY created_at",
            (tg_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._to_entity(row_to_session(r)) for r in rows]

    async def list_for_workspace(
        self, tg_id: int, ws_id: WorkspaceId
    ) -> list[TerminalSession]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM terminal_sessions WHERE tg_id = ? AND ws_id = ? ORDER BY created_at",
            (tg_id, str(ws_id)),
        ) as cur:
            rows = await cur.fetchall()
        return [self._to_entity(row_to_session(r)) for r in rows]

    async def delete(self, tg_id: int, ses_id: SessionId) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "DELETE FROM terminal_sessions WHERE tg_id = ? AND ses_id = ?",
            (tg_id, str(ses_id)),
        )
        await conn.commit()
