"""SQLite/Postgres append-only log of gate decisions (``forcejoin_events``).

One row per membership decision (written by the service, never by a handler) plus
one row per operator configuration change. See migration
``0014_forcejoin_events.sql`` for the taxonomy.

Two properties this adapter owns:

* **a write failure must never break the gate** — :meth:`record` swallows the
  exception after a warning, so the caller can call it on the hot path without a
  ``try`` of its own;
* **retention is a boot step, not a runner** — :meth:`prune` deletes rows older
  than ``keep_days`` and returns how many it removed, so startup can log the count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from cloudbot.adapters.persistence.database import Database
from cloudbot.domain.forcejoin import GateEvent

log = structlog.get_logger("cloudbot.forcejoin.events")

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

class SqliteForceJoinEventStore:
    """``ForceJoinEventSink`` over the bot's database (SQLite or Postgres)."""

    def __init__(self, db: Database, *, clock=None) -> None:
        self._db = db
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def record(self, event: GateEvent) -> None:
        """Append one row. Never raises — a lost row is logged, never propagated."""
        try:
            row = event.normalized()
            conn = await self._db.connect()
            await conn.execute(
                "INSERT INTO forcejoin_events "
                "(ts, tg_id, chat_id, outcome, reason, latency_ms, cached, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    row.tg_id,
                    row.chat_id,
                    row.outcome,
                    row.reason,
                    row.latency_ms,
                    1 if row.cached else 0,
                    row.detail,
                ),
            )
            await conn.commit()
        except Exception as exc:  # noqa: BLE001 - an event write never breaks the gate
            log.warning(
                "forcejoin.event_write_failed",
                outcome=getattr(event, "outcome", ""),
                error=type(exc).__name__,
            )

    async def prune(self, *, keep_days: int = 90) -> int:
        """Delete rows older than ``keep_days``; return the row count removed."""
        cutoff = (self._clock() - timedelta(days=keep_days)).isoformat().replace(
            "+00:00", "Z"
        )
        try:
            conn = await self._db.connect()
            cur = await conn.execute(
                "DELETE FROM forcejoin_events WHERE ts < ?", (cutoff,)
            )
            await conn.commit()
            removed = int(getattr(cur, "rowcount", 0) or 0)
        except Exception as exc:  # noqa: BLE001 - pruning is best-effort
            log.warning("forcejoin.prune_failed", error=type(exc).__name__)
            return 0
        log.info("forcejoin.pruned", removed=removed, keep_days=keep_days)
        return removed
