"""TASK forcejoin-v2: the statistics read model over ``forcejoin_events``.

Two properties matter and each is checked against a hand-computed expectation,
not against the reader's own arithmetic:

* the funnel (hit → refused at least once → then passed) is SQL over the rows;
* an empty range reports ``has_data=False`` so a screen says "no decisions",
  never a zero that looks like a healthy zero.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import Message

from cloudbot.adapters.persistence.forcejoin_stats import (
    ForceJoinStatsReader,
    normalize_range,
    range_cutoff,
)
from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore
from cloudbot.domain.forcejoin import GateEvent, MembershipResult
from cloudbot.services.forcejoin import ForceJoinService

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)

@pytest.fixture
async def events(database):
    return SqliteForceJoinEventStore(database)

async def _append(database, *, outcome, ts, tg_id=None, chat_id=-1001, reason="", cached=0, latency=None):
    conn = await database.connect()
    await conn.execute(
        "INSERT INTO forcejoin_events "
        "(ts, tg_id, chat_id, outcome, reason, latency_ms, cached, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '')",
        (ts, tg_id, chat_id, outcome, reason, latency, cached),
    )
    await conn.commit()

async def _chat(database, chat_id: int, *, title="Cloud", is_admin=1, enabled=1):
    conn = await database.connect()
    await conn.execute(
        "INSERT INTO forcejoin_chats "
        "(chat_id, username, title, kind, is_admin, enabled, added_at, added_by) "
        "VALUES (?, 'cloud', ?, 'channel', ?, ?, ?, 1)",
        (chat_id, title, is_admin, enabled, "2026-11-01T00:00:00Z"),
    )
    await conn.commit()

async def test_empty_range_reports_no_data_not_zeros(database):
    await _chat(database, -1001)
    reader = ForceJoinStatsReader(database)
    stats = await reader.read("7d")
    assert stats.has_data is False
    assert stats.volume.decisions == 0
    # Until a row exists, the gate is still reported from its own table.
    assert stats.gate.chats_total == 1

async def test_funnel_and_outcomes_match_a_hand_computed_fixture(database):
    await _chat(database, -1001)
    # user 1: deny then pass                -> refused, then passed
    # user 2: deny, deny, pass              -> refused, then passed
    # user 3: pass only                     -> not refused
    # user 4: unknown only                 -> not refused
    rows = [
        ("deny", "2026-11-05T10:00:00Z", 1),
        ("pass", "2026-11-05T10:01:00Z", 1),
        ("deny", "2026-11-05T10:02:00Z", 2),
        ("deny", "2026-11-05T10:03:00Z", 2),
        ("pass", "2026-11-05T10:04:00Z", 2),
        ("pass", "2026-11-05T10:05:00Z", 3),
        ("unknown", "2026-11-05T10:06:00Z", 4),
    ]
    for outcome, ts, tg_id in rows:
        await _append(database, outcome=outcome, ts=ts, tg_id=tg_id)

    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("7d")
    assert stats.has_data is True
    assert stats.volume.decisions == 7
    assert stats.volume.distinct_users == 4
    # hit=4 users, refused=2 users (1 and 2), then passed=2 (both did)
    assert stats.funnel.hit_gate == 4
    assert stats.funnel.refused == 2
    assert stats.funnel.then_passed == 2
    counts = {o.outcome: o.count for o in stats.outcomes}
    assert counts == {"pass": 3, "deny": 3, "unknown": 1, "error": 0}
    assert stats.outcome_count("deny") == 3

async def test_cache_hit_rate_and_latency_percentiles(database):
    await _chat(database, -1001)
    for latency in (100, 200, 300, 400):
        await _append(
            database, outcome="pass", ts="2026-11-05T10:00:00Z", tg_id=1,
            cached=0, latency=latency,
        )
    await _append(database, outcome="pass", ts="2026-11-05T10:01:00Z", tg_id=1,
                  reason="cached_positive", cached=1, latency=0)
    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("7d")
    assert stats.cache.hits == 1
    assert stats.cache.misses == 4
    assert stats.health.cache_hit_rate == pytest.approx(20.0)
    assert stats.cache.p50_ms == 200
    assert stats.cache.p95_ms == 400

async def test_per_chat_rates_last_decision_and_admin_flag(database):
    await _chat(database, -1001, title="Cloud", is_admin=1)
    await _chat(database, -1002, title="Chat", is_admin=0)
    for outcome in ("deny", "deny", "pass", "unknown"):
        await _append(database, outcome=outcome, ts="2026-11-05T10:00:00Z", tg_id=1, chat_id=-1001)
    await _append(database, outcome="pass", ts="2026-11-06T09:00:00Z", tg_id=2, chat_id=-1002)
    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("7d")
    by_id = {c.chat_id: c for c in stats.per_chat}
    assert by_id[-1001].decisions == 4
    assert by_id[-1001].deny_rate == pytest.approx(50.0)
    assert by_id[-1001].unknown_rate == pytest.approx(25.0)
    assert by_id[-1001].last_decision_at == "2026-11-05T10:00:00Z"
    assert by_id[-1001].bot_admin is True
    assert by_id[-1002].bot_admin is False
    assert stats.health.chats_needing_attention == 1

async def test_top_refused_names_the_user_from_users(database):
    await _chat(database, -1001)
    conn = await database.connect()
    await conn.execute(
        "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at, display_name) "
        "VALUES (?, 'en', 'free', ?, ?, 'Mohamed')",
        (6508129575, "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"),
    )
    await conn.commit()
    for _ in range(3):
        await _append(database, outcome="deny", ts="2026-11-05T10:00:00Z", tg_id=6508129575)
    await _append(database, outcome="deny", ts="2026-11-05T10:00:00Z", tg_id=42)
    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("7d")
    assert stats.top_refused[0].tg_id == 6508129575
    assert stats.top_refused[0].display_name == "Mohamed"
    assert stats.top_refused[0].denials == 3

async def test_range_window_excludes_older_rows(database):
    await _chat(database, -1001)
    await _append(database, outcome="pass", ts="2026-11-05T10:00:00Z", tg_id=1)
    await _append(database, outcome="deny", ts="2025-01-01T10:00:00Z", tg_id=1)
    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("7d")
    assert stats.volume.decisions == 1  # the 2025 row is outside the window
    all_time = await ForceJoinStatsReader(database, clock=lambda: NOW).read("all")
    assert all_time.volume.decisions == 2

def test_range_parsing_and_cutoff():
    assert normalize_range("24h") == "24h"
    assert normalize_range("nonsense") == "7d"
    assert normalize_range(None) == "7d"
    assert range_cutoff("all", now=NOW) is None
    assert range_cutoff("24h", now=NOW) == "2026-11-05T12:00:00Z"

async def test_event_store_prune_removes_only_old_rows(database):
    from datetime import timedelta

    await _append(database, outcome="pass", ts="2025-01-01T10:00:00Z", tg_id=1)
    await _append(database, outcome="pass", ts="2026-11-05T10:00:00Z", tg_id=2)
    store = SqliteForceJoinEventStore(database, clock=lambda: NOW)
    removed = await store.prune(keep_days=90)
    assert removed == 1
    stats = await ForceJoinStatsReader(database, clock=lambda: NOW).read("all")
    assert stats.volume.decisions == 1

async def test_event_writes_cover_the_middleware_path(database, fake_directory):
    """The service, not the handler, writes the row — so any entry point is covered."""
    from cloudbot.adapters.store.sqlite_forcejoin import SqliteForceJoinStore

    store = SqliteForceJoinStore(database)
    await store.add(
        chat_id=-1001, username="cloud", title="Cloud", kind="channel",
        bot_is_admin=True, added_by=1,
    )
    fake_directory.memberships[-1001] = MembershipResult.refused(-1001)
    gate = ForceJoinService(
        store, fake_directory, bot_id=42,
        events=SqliteForceJoinEventStore(database),
    )
    await gate.status(500)
    conn = await database.connect()
    async with conn.execute(
        "SELECT outcome, reason FROM forcejoin_events"
    ) as cur:
        rows = await cur.fetchall()
    assert [(r["outcome"], r["reason"]) for r in rows] == [("deny", "absent")]
