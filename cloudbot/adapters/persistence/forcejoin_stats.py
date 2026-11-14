"""Read-only statistics over ``forcejoin_events`` (TASK forcejoin-v2 Part 3).

Every number is one SQL aggregate over the rows themselves — nothing is sampled,
estimated or stored as a drifting aggregate. The same module serves the bot's admin
*Stats* screen and, through the same Neon database, the panel's ``/forcejoin`` page
(the panel re-implements these queries in TypeScript against the identical schema).

Ranges: ``24h | 7d | 30d | 90d | all``. An empty range is reported as ``has_data =
False`` so a screen can say "no decisions in this range" instead of rendering a
zero that looks healthy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from cloudbot.adapters.persistence.database import Database
from cloudbot.domain.forcejoin_stats import (
    CacheStat,
    ChatStat,
    ForceJoinStats,
    Funnel,
    GateCounts,
    Health,
    OutcomeCount,
    RangeKey,
    RefusedUser,
    Volume,
)

RangeKey = Literal["24h", "7d", "30d", "90d", "all"]

RANGE_KEYS: tuple[RangeKey, ...] = ("24h", "7d", "30d", "90d", "all")

_RANGE_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}

def normalize_range(value: str | None) -> RangeKey:
    key = (value or "").strip().lower()
    if key in RANGE_KEYS:
        return key  # type: ignore[return-value]
    return "7d"

def range_cutoff(key: RangeKey, *, now: datetime | None = None) -> str | None:
    """ISO-8601 UTC cutoff for a range, or ``None`` for ``all`` (no lower bound)."""
    if key == "all":
        return None
    hours = _RANGE_HOURS[key]
    moment = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentile (the one psql's ``percentile_disc`` agrees with)."""
    if not values:
        return 0
    ordered = sorted(values)
    import math

    rank = max(1, math.ceil(fraction * len(ordered)))
    return int(ordered[rank - 1])

def _rate(part: int, whole: int) -> float:
    return (part * 100.0 / whole) if whole else 0.0

class _Window:
    """The range predicate, rendered for a table alias (``e.`` / ``""``)."""

    def __init__(self, cutoff: str | None) -> None:
        self._cutoff = cutoff

    @property
    def params(self) -> list:
        return [self._cutoff] if self._cutoff is not None else []

    def clause(self, alias: str = "", *, lead: str = " WHERE") -> str:
        if self._cutoff is None:
            return ""
        column = f"{alias}.ts" if alias else "ts"
        return f"{lead} {column} >= ?"

    def and_(self, alias: str = "") -> str:
        return " AND " if self._cutoff is not None else " WHERE "

class ForceJoinStatsReader:
    """One query per section over ``forcejoin_events`` and ``forcejoin_chats``."""

    def __init__(self, db: Database, *, clock=None) -> None:
        self._db = db
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def read(self, range_key: str | None = None) -> ForceJoinStats:
        key = normalize_range(range_key)
        window = _Window(range_cutoff(key, now=self._clock()))
        params = window.params
        conn = await self._db.connect()

        gate = await self._gate(conn)
        decisions = await self._int(
            conn, f"SELECT COUNT(*) AS n FROM forcejoin_events{window.clause()}", params
        )
        distinct_users = await self._int(
            conn,
            "SELECT COUNT(DISTINCT tg_id) AS n FROM forcejoin_events"
            f"{window.clause()}{window.and_()}tg_id IS NOT NULL",
            params,
        )
        outcomes = await self._outcomes(conn, window, params, decisions)
        funnel = await self._funnel(conn, window, params)
        per_chat = await self._per_chat(conn, window, params)
        cache = await self._cache(conn, window, params)
        top_refused = await self._top_refused(conn, window, params)
        per_day = await self._per_day(conn, window, params)
        per_day_outcomes = await self._per_day_outcomes(conn, window, params)
        unknown = self._find(outcomes, "unknown")
        error = self._find(outcomes, "error")
        health = Health(
            unknown_rate=_rate(unknown, decisions),
            error_rate=_rate(error, decisions),
            chats_needing_attention=gate.chats_needing_attention,
            cache_hit_rate=cache.hit_rate,
        )
        return ForceJoinStats(
            range=key,
            has_data=decisions > 0,
            gate=gate,
            volume=Volume(
                decisions=decisions, distinct_users=distinct_users, per_day=tuple(per_day)
            ),
            outcomes=tuple(outcomes),
            funnel=funnel,
            per_chat=tuple(per_chat),
            cache=cache,
            top_refused=tuple(top_refused),
            health=health,
            per_day_outcomes=tuple(per_day_outcomes),
        )

    # -- sections ------------------------------------------------------------
    async def _gate(self, conn) -> GateCounts:
        enabled = await self._flag(conn)
        total = await self._int(
            conn, "SELECT COUNT(*) AS n FROM forcejoin_chats", []
        )
        enabled_n = await self._int(
            conn, "SELECT COUNT(*) AS n FROM forcejoin_chats WHERE enabled = 1", []
        )
        attention = await self._int(
            conn,
            "SELECT COUNT(*) AS n FROM forcejoin_chats WHERE enabled = 1 AND is_admin = 0",
            [],
        )
        return GateCounts(
            enabled=enabled,
            chats_total=total,
            chats_enabled=enabled_n,
            chats_needing_attention=attention,
        )

    async def _flag(self, conn) -> bool:
        async with conn.execute(
            "SELECT value FROM runtime_flags WHERE key = 'forcejoin.enabled'"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return True
        return str(row["value"]) not in ("0", "false", "off", "")

    async def _outcomes(
        self, conn, window: _Window, params: list, total: int
    ) -> list[OutcomeCount]:
        async with conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM forcejoin_events"
            f"{window.clause()} GROUP BY outcome",
            params,
        ) as cur:
            rows = await cur.fetchall()
        counts = {str(r["outcome"]): int(r["n"]) for r in rows}
        return [
            OutcomeCount(
                outcome=name,
                count=counts.get(name, 0),
                percent=_rate(counts.get(name, 0), total),
            )
            for name in ("pass", "deny", "unknown", "error")
        ]

    async def _funnel(self, conn, window: _Window, params: list) -> Funnel:
        hit = await self._int(
            conn,
            "SELECT COUNT(DISTINCT tg_id) AS n FROM forcejoin_events"
            f"{window.clause()}{window.and_()}tg_id IS NOT NULL",
            params,
        )
        refused = await self._int(
            conn,
            "SELECT COUNT(DISTINCT tg_id) AS n FROM forcejoin_events"
            f"{window.clause()}{window.and_()}outcome = 'deny'",
            params,
        )
        sql = (
            "SELECT COUNT(*) AS n FROM ("
            " SELECT e.tg_id AS tg_id, MIN(CASE WHEN e.outcome = 'deny' THEN e.ts END) AS first_deny"
            f" FROM forcejoin_events e{window.clause('e')}{window.and_('e')}e.tg_id IS NOT NULL"
            " GROUP BY e.tg_id"
            ") d WHERE d.first_deny IS NOT NULL AND EXISTS ("
            " SELECT 1 FROM forcejoin_events p WHERE p.tg_id = d.tg_id"
            " AND p.outcome = 'pass' AND p.ts > d.first_deny)"
        )
        then_passed = await self._int(conn, sql, params)
        return Funnel(hit_gate=hit, refused=refused, then_passed=then_passed)

    async def _per_chat(self, conn, window: _Window, params: list) -> list[ChatStat]:
        if window.clause():
            on = f"e.chat_id = c.chat_id AND e.ts >= ?"
        else:
            on = "e.chat_id = c.chat_id"
        async with conn.execute(
            "SELECT c.chat_id AS chat_id, c.title AS title, c.username AS username,"
            " c.is_admin AS is_admin, c.enabled AS enabled,"
            " COUNT(e.id) AS decisions,"
            " SUM(CASE WHEN e.outcome = 'deny' THEN 1 ELSE 0 END) AS denies,"
            " SUM(CASE WHEN e.outcome IN ('unknown', 'error') THEN 1 ELSE 0 END) AS unknowns,"
            " MAX(e.ts) AS last_ts"
            " FROM forcejoin_chats c LEFT JOIN forcejoin_events e"
            f" ON {on}"
            " GROUP BY c.chat_id, c.title, c.username, c.is_admin, c.enabled"
            " ORDER BY decisions DESC, c.chat_id",
            params,
        ) as cur:
            rows = await cur.fetchall()
        out: list[ChatStat] = []
        for row in rows:
            decisions = int(row["decisions"] or 0)
            out.append(
                ChatStat(
                    chat_id=int(row["chat_id"]),
                    title=str(row["title"] or ""),
                    username=str(row["username"] or ""),
                    bot_admin=bool(row["is_admin"]),
                    enabled=bool(row["enabled"]),
                    decisions=decisions,
                    deny_rate=_rate(int(row["denies"] or 0), decisions),
                    unknown_rate=_rate(int(row["unknowns"] or 0), decisions),
                    last_decision_at=str(row["last_ts"]) if row["last_ts"] else None,
                )
            )
        return out

    async def _cache(self, conn, window: _Window, params: list) -> CacheStat:
        hits = await self._int(
            conn,
            "SELECT COUNT(*) AS n FROM forcejoin_events"
            f"{window.clause()}{window.and_()}cached = 1",
            params,
        )
        async with conn.execute(
            "SELECT latency_ms FROM forcejoin_events"
            f"{window.clause()}{window.and_()}cached = 0 AND latency_ms IS NOT NULL",
            params,
        ) as cur:
            rows = await cur.fetchall()
        latencies = [int(r["latency_ms"]) for r in rows]
        misses = len(latencies)
        return CacheStat(
            hits=hits,
            misses=misses,
            hit_rate=_rate(hits, hits + misses),
            p50_ms=_percentile(latencies, 0.50),
            p95_ms=_percentile(latencies, 0.95),
        )

    async def _top_refused(self, conn, window: _Window, params: list) -> list[RefusedUser]:
        sql = (
            "SELECT e.tg_id AS tg_id, COALESCE(u.display_name, '') AS display_name,"
            " COUNT(*) AS denials"
            " FROM forcejoin_events e LEFT JOIN users u ON u.tg_id = e.tg_id"
            f"{window.clause('e')}{window.and_('e')}"
            "e.outcome = 'deny' AND e.tg_id IS NOT NULL"
            " GROUP BY e.tg_id, u.display_name ORDER BY denials DESC, e.tg_id LIMIT 5"
        )
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            RefusedUser(
                tg_id=int(r["tg_id"]),
                display_name=str(r["display_name"] or ""),
                denials=int(r["denials"]),
            )
            for r in rows
        ]

    async def _per_day(self, conn, window: _Window, params: list) -> list[tuple[str, int]]:
        async with conn.execute(
            "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n FROM forcejoin_events"
            f"{window.clause()} GROUP BY day ORDER BY day",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [(str(r["day"]), int(r["n"])) for r in rows]

    async def _per_day_outcomes(
        self, conn, window: _Window, params: list
    ) -> list[tuple[str, str, int]]:
        async with conn.execute(
            "SELECT substr(ts, 1, 10) AS day, outcome, COUNT(*) AS n "
            f"FROM forcejoin_events{window.clause()} GROUP BY day, outcome ORDER BY day, outcome",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [(str(r["day"]), str(r["outcome"]), int(r["n"])) for r in rows]

    # -- helpers -------------------------------------------------------------
    @staticmethod
    async def _int(conn, sql: str, params: list) -> int:
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row is not None else 0

    @staticmethod
    def _find(outcomes: list[OutcomeCount], name: str) -> int:
        for entry in outcomes:
            if entry.outcome == name:
                return entry.count
        return 0
