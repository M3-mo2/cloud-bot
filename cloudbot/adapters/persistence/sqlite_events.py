"""SQLite implementation of ``EventLog`` and ``AdminStateStore``.

The event log is append-only; the admin state is a small key/value-ish set of
rows. Both live beside the ownership registry in the bot's own SQLite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import parse_dt, row_to_event, utc_now_iso
from cloudbot.domain.events import EventCategory, EventRecord, NewEvent


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SqliteEventLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, event: NewEvent) -> int:
        conn = await self._db.connect()
        cur = await conn.execute(
            "INSERT INTO events "
            "(ts, category, severity, tg_id, ws_id, summary_key, params, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                utc_now_iso(),
                event.category.value,
                event.effective_severity.value,
                event.tg_id,
                event.ws_id,
                event.summary_key,
                json.dumps(event.params, ensure_ascii=False),
                event.actor,
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)

    async def latest_id(self) -> int:
        conn = await self._db.connect()
        async with conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM events") as cur:
            return int((await cur.fetchone())["n"])

    async def count_after(self, after_id: int) -> int:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE id > ?", (after_id,)
        ) as cur:
            return int((await cur.fetchone())["n"])

    async def events_after(
        self, after_id: int, *, limit: int = 1000, tg_id: int | None = None
    ) -> list[EventRecord]:
        conn = await self._db.connect()
        if tg_id is None:
            sql = "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?"
            params: list[object] = [after_id, limit]
        else:
            sql = "SELECT * FROM events WHERE id > ? AND (tg_id = ? OR actor = ?) ORDER BY id LIMIT ?"
            params = [after_id, tg_id, f"admin:{tg_id}", limit]
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [row_to_event(r) for r in rows]

    async def query(
        self,
        *,
        category: EventCategory | None = None,
        actor: str | None = None,
        tg_id: int | None = None,
        ws_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[EventRecord], int]:
        conn = await self._db.connect()
        clauses: list[str] = []
        params: list[object] = []
        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if tg_id is not None:
            clauses.append("tg_id = ?")
            params.append(tg_id)
        if ws_id is not None:
            clauses.append("ws_id = ?")
            params.append(ws_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("ts <= ?")
            params.append(_iso(until))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with conn.execute(f"SELECT COUNT(*) AS n FROM events{where}", params) as cur:
            total = int((await cur.fetchone())["n"])
        async with conn.execute(
            f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ) as cur:
            rows = await cur.fetchall()
        return [row_to_event(r) for r in rows], total


class SqliteAdminStateStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- subscriptions -------------------------------------------------------
    async def subscriptions(self, admin_id: int) -> dict[str, bool]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT category, enabled FROM event_subscriptions WHERE admin_id = ?",
            (admin_id,),
        ) as cur:
            rows = await cur.fetchall()
        return {str(r["category"]): bool(r["enabled"]) for r in rows}

    async def set_subscription(self, admin_id: int, category: str, enabled: bool) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO event_subscriptions (admin_id, category, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(admin_id, category) DO UPDATE SET enabled = excluded.enabled",
            (admin_id, category, 1 if enabled else 0),
        )
        await conn.commit()

    async def subscribed_admin_ids(self) -> list[int]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT DISTINCT admin_id FROM admin_notify_state"
        ) as cur:
            rows = await cur.fetchall()
        return [int(r["admin_id"]) for r in rows]

    # -- notification state --------------------------------------------------
    async def notify_state(self, admin_id: int) -> tuple[int, bool]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT last_delivered_event_id, paused FROM admin_notify_state "
            "WHERE admin_id = ?",
            (admin_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0, False
        return int(row["last_delivered_event_id"]), bool(row["paused"])

    async def _ensure_state(self, admin_id: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO admin_notify_state (admin_id, last_delivered_event_id, paused, updated_at) "
            "VALUES (?, 0, 0, ?) ON CONFLICT(admin_id) DO NOTHING",
            (admin_id, utc_now_iso()),
        )

    async def set_cursor(self, admin_id: int, event_id: int) -> None:
        conn = await self._db.connect()
        await self._ensure_state(admin_id)
        await conn.execute(
            "UPDATE admin_notify_state SET last_delivered_event_id = ?, updated_at = ? "
            "WHERE admin_id = ?",
            (event_id, utc_now_iso(), admin_id),
        )
        await conn.commit()

    async def set_paused(self, admin_id: int, paused: bool) -> None:
        conn = await self._db.connect()
        await self._ensure_state(admin_id)
        await conn.execute(
            "UPDATE admin_notify_state SET paused = ?, updated_at = ? WHERE admin_id = ?",
            (1 if paused else 0, utc_now_iso(), admin_id),
        )
        await conn.commit()

    async def notify_scope(self, admin_id: int) -> int | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT scope_tg_id FROM admin_notify_state WHERE admin_id = ?", (admin_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["scope_tg_id"] is None:
            return None
        return int(row["scope_tg_id"])

    async def set_notify_scope(self, admin_id: int, tg_id: int | None) -> None:
        conn = await self._db.connect()
        await self._ensure_state(admin_id)
        await conn.execute(
            "UPDATE admin_notify_state SET scope_tg_id = ?, updated_at = ? WHERE admin_id = ?",
            (tg_id, utc_now_iso(), admin_id),
        )
        await conn.commit()

    # -- entered-workspace mode ---------------------------------------------
    async def entered(self, admin_id: int) -> tuple[str, int] | None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT entered_ws_id, entered_owner_tg_id FROM admin_mode WHERE admin_id = ?",
            (admin_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["entered_ws_id"] is None:
            return None
        return str(row["entered_ws_id"]), int(row["entered_owner_tg_id"] or 0)

    async def set_entered(self, admin_id: int, ws_id: str, owner_tg_id: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO admin_mode (admin_id, entered_ws_id, entered_owner_tg_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET entered_ws_id = excluded.entered_ws_id, "
            "entered_owner_tg_id = excluded.entered_owner_tg_id, updated_at = excluded.updated_at",
            (admin_id, ws_id, owner_tg_id, utc_now_iso()),
        )
        await conn.commit()

    async def clear_entered(self, admin_id: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO admin_mode (admin_id, entered_ws_id, entered_owner_tg_id, updated_at) "
            "VALUES (?, NULL, NULL, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET entered_ws_id = NULL, "
            "entered_owner_tg_id = NULL, updated_at = excluded.updated_at",
            (admin_id, utc_now_iso()),
        )
        await conn.commit()
