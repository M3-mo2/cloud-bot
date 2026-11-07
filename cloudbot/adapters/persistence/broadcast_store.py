"""SQLite implementation of ``BroadcastStore``.

Counts are read back from the row (never recomputed at render time), and the
audience presets are SQL over the stored tables — so a count the panel shows is a
number the database produced, and a restart shows exactly what is stored.

Disjointness of the presets is a property this module *has* to keep, so each query
is expressed so the sets cannot overlap:

* ``active``  — the user has at least one subscription whose ``expires_at`` is future;
* ``expired`` — the user has subscriptions, but none of them is future;
* ``no_plan`` — the user owns a workspace, but has no subscription row at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import parse_dt
from cloudbot.domain.broadcast import (
    AudienceKey,
    BroadcastRun,
    BroadcastSource,
    RunStatus,
)
from cloudbot.domain.value_objects import WorkspaceId  # noqa: F401  (typing parity)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

class SqliteBroadcastStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- audience -----------------------------------------------------------
    async def audience_members(self, audience: AudienceKey, *, now: datetime) -> list[int]:
        conn = await self._db.connect()
        stamp = _iso(now)
        if audience == "all":
            sql = "SELECT tg_id FROM users ORDER BY tg_id"
            params: tuple[object, ...] = ()
        elif audience == "active":
            sql = (
                "SELECT DISTINCT tg_id FROM subscriptions "
                "WHERE expires_at > ? ORDER BY tg_id"
            )
            params = (stamp,)
        elif audience == "expired":
            sql = (
                "SELECT DISTINCT s.tg_id FROM subscriptions s "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM subscriptions a "
                "  WHERE a.tg_id = s.tg_id AND a.expires_at > ?"
                ") ORDER BY s.tg_id"
            )
            params = (stamp,)
        elif audience == "no_plan":
            sql = (
                "SELECT DISTINCT w.tg_id FROM user_workspaces w "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM subscriptions s WHERE s.tg_id = w.tg_id"
                ") ORDER BY w.tg_id"
            )
            params = ()
        else:  # pragma: no cover - the codec refuses an unknown key first
            return []
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [int(row[0]) for row in rows]

    # -- runs ---------------------------------------------------------------
    async def create_draft(
        self,
        *,
        source: BroadcastSource,
        created_by: int,
        created_at: datetime,
    ) -> BroadcastRun:
        conn = await self._db.connect()
        cur = await conn.execute(
            "INSERT INTO broadcasts "
            "(created_at, created_by, source_chat_id, source_message_id, source_kind, "
            " audience, total, sent, failed, status) "
            "VALUES (?, ?, ?, ?, ?, 'all', 0, 0, 0, 'composing')",
            (
                _iso(created_at),
                int(created_by),
                int(source.chat_id),
                int(source.message_id),
                source.kind,
            ),
        )
        run_id = int(cur.lastrowid or 0)
        await conn.commit()
        run = await self.get_run(run_id)
        assert run is not None
        return run

    async def plan_audience(
        self,
        run_id: int,
        *,
        audience: AudienceKey,
        recipients: list[int],
        now: datetime,
    ) -> BroadcastRun | None:
        """Replace a draft's targets with the frozen audience and roll its counts."""
        conn = await self._db.connect()
        await conn.execute(
            "DELETE FROM broadcast_targets WHERE broadcast_id = ?", (int(run_id),)
        )
        if recipients:
            await conn.executemany(
                "INSERT INTO broadcast_targets (broadcast_id, tg_id, status, error, "
                "attempts, updated_at) VALUES (?, ?, 'pending', '', 0, ?) "
                "ON CONFLICT(broadcast_id, tg_id) DO NOTHING",
                [(int(run_id), int(tg), _iso(now)) for tg in recipients],
            )
        await conn.execute(
            "UPDATE broadcasts SET audience = ?, total = ?, sent = 0, failed = 0 "
            "WHERE id = ?",
            (audience, len(recipients), int(run_id)),
        )
        await conn.commit()
        return await self.get_run(run_id)

    async def create_run(
        self,
        *,
        source: BroadcastSource,
        audience: AudienceKey,
        total: int,
        recipients: list[int],
        created_by: int,
        created_at: datetime,
    ) -> BroadcastRun:
        conn = await self._db.connect()
        cur = await conn.execute(
            "INSERT INTO broadcasts "
            "(created_at, created_by, source_chat_id, source_message_id, source_kind, "
            " audience, total, sent, failed, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'composing')",
            (
                _iso(created_at),
                int(created_by),
                int(source.chat_id),
                int(source.message_id),
                source.kind,
                audience,
                int(total),
            ),
        )
        run_id = int(cur.lastrowid or 0)
        if recipients:
            await conn.executemany(
                "INSERT INTO broadcast_targets (broadcast_id, tg_id, status, error, "
                "attempts, updated_at) VALUES (?, ?, 'pending', '', 0, ?) "
                "ON CONFLICT(broadcast_id, tg_id) DO NOTHING",
                [(run_id, int(tg), _iso(created_at)) for tg in recipients],
            )
        await conn.commit()
        run = await self.get_run(run_id)
        assert run is not None
        return run

    async def get_run(self, run_id: int) -> BroadcastRun | None:
        conn = await self._db.connect()
        async with conn.execute("SELECT * FROM broadcasts WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row is not None else None

    async def latest_draft(self, *, created_by: int) -> BroadcastRun | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM broadcasts WHERE status = 'composing' AND created_by = ? "
            "ORDER BY id DESC LIMIT 1",
            (int(created_by),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row is not None else None

    async def open_run(self) -> BroadcastRun | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM broadcasts WHERE status = 'sending' ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row is not None else None

    async def claim_run(self, run_id: int, *, now: datetime) -> bool:
        """Conditional update: SUCCEEDS only while no *other* run is ``sending``.

        The ``WHERE NOT EXISTS`` makes the guard atomic at the database, so two
        concurrent confirms cannot both start a run — the second's update matches
        no row.
        """
        conn = await self._db.connect()
        cur = await conn.execute(
            "UPDATE broadcasts SET status = 'sending', started_at = ?, stop_reason = '' "
            "WHERE id = ? AND status IN ('composing', 'stopped', 'done', 'interrupted') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM broadcasts b WHERE b.status = 'sending' AND b.id != ?"
            ")",
            (_iso(now), int(run_id), int(run_id)),
        )
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def mark_interrupted(self, run_id: int, *, now: datetime) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE broadcasts SET status = 'interrupted', finished_at = ? "
            "WHERE id = ? AND status = 'sending'",
            (_iso(now), int(run_id)),
        )
        await conn.commit()

    # -- targets ------------------------------------------------------------
    async def pending_targets(self, run_id: int) -> list[int]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT tg_id FROM broadcast_targets "
            "WHERE broadcast_id = ? AND status = 'pending' ORDER BY tg_id",
            (int(run_id),),
        ) as cur:
            rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    async def failed_targets(self, run_id: int) -> list[int]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT tg_id FROM broadcast_targets "
            "WHERE broadcast_id = ? AND status = 'failed' ORDER BY tg_id",
            (int(run_id),),
        ) as cur:
            rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    async def failure_counts(self, run_id: int) -> dict[str, int]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT error, COUNT(*) AS n FROM broadcast_targets "
            "WHERE broadcast_id = ? AND status = 'failed' GROUP BY error",
            (int(run_id),),
        ) as cur:
            rows = await cur.fetchall()
        return {str(r["error"] or "other"): int(r["n"]) for r in rows}

    async def record_success(self, run_id: int, tg_id: int, *, now: datetime) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE broadcast_targets SET status = 'sent', error = '', "
            "attempts = attempts + 1, updated_at = ? "
            "WHERE broadcast_id = ? AND tg_id = ?",
            (_iso(now), int(run_id), int(tg_id)),
        )
        await conn.execute(
            "UPDATE broadcasts SET sent = sent + 1 WHERE id = ?", (int(run_id),)
        )
        await conn.commit()

    async def record_failure(
        self, run_id: int, tg_id: int, error: str, *, now: datetime
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE broadcast_targets SET status = 'failed', error = ?, "
            "attempts = attempts + 1, updated_at = ? "
            "WHERE broadcast_id = ? AND tg_id = ?",
            (str(error), _iso(now), int(run_id), int(tg_id)),
        )
        await conn.execute(
            "UPDATE broadcasts SET failed = failed + 1 WHERE id = ?", (int(run_id),)
        )
        await conn.commit()

    async def reset_failed(self, run_id: int) -> int:
        """Return failed targets to pending and roll the failed counter back.

        The ``failed`` counter is recomputed from the rows, so it cannot drift from
        the targets after a retry.
        """
        conn = await self._db.connect()
        cur = await conn.execute(
            "UPDATE broadcast_targets SET status = 'pending', error = '' "
            "WHERE broadcast_id = ? AND status = 'failed'",
            (int(run_id),),
        )
        moved = cur.rowcount or 0
        await conn.execute(
            "UPDATE broadcasts SET failed = ("
            "  SELECT COUNT(*) FROM broadcast_targets "
            "  WHERE broadcast_id = ? AND status = 'failed'"
            ") WHERE id = ?",
            (int(run_id), int(run_id)),
        )
        await conn.commit()
        return int(moved)

    async def finish_run(
        self, run_id: int, status: RunStatus, *, stop_reason: str, now: datetime
    ) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE broadcasts SET status = ?, finished_at = ?, stop_reason = ? "
            "WHERE id = ?",
            (status, _iso(now), stop_reason, int(run_id)),
        )
        await conn.commit()

    # -- history ------------------------------------------------------------
    async def history(self, *, limit: int = 10) -> list[BroadcastRun]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]

def _row_to_run(row: aiosqlite.Row) -> BroadcastRun:
    from cloudbot.domain.broadcast import BroadcastSource

    started = row["started_at"]
    finished = row["finished_at"]
    return BroadcastRun(
        id=int(row["id"]),
        created_at=parse_dt(row["created_at"]),
        created_by=int(row["created_by"]),
        source=BroadcastSource(
            chat_id=int(row["source_chat_id"]),
            message_id=int(row["source_message_id"]),
            kind=str(row["source_kind"]),
        ),
        audience=str(row["audience"]),
        total=int(row["total"]),
        sent=int(row["sent"]),
        failed=int(row["failed"]),
        status=str(row["status"]),
        started_at=parse_dt(started) if started else None,
        finished_at=parse_dt(finished) if finished else None,
        stop_reason=str(row["stop_reason"] or ""),
    )
