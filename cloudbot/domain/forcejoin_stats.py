"""Pure statistics read model for the force-join gate (TASK forcejoin-v2 Part 3).

These are plain values: the adapter (``adapters/persistence/forcejoin_stats.py``)
fills them from SQL, the bot's admin *Stats* screen renders them, and the catalog's
golden fixture builds them without a database. Keeping them here is what lets the
presentation layer depend on the domain instead of on an adapter — the same rule
every other screen follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RangeKey = Literal["24h", "7d", "30d", "90d", "all"]

RANGE_KEYS: tuple[RangeKey, ...] = ("24h", "7d", "30d", "90d", "all")

@dataclass(frozen=True)
class GateCounts:
    enabled: bool
    chats_total: int
    chats_enabled: int
    chats_needing_attention: int

@dataclass(frozen=True)
class Volume:
    decisions: int
    distinct_users: int
    per_day: tuple[tuple[str, int], ...] = ()

@dataclass(frozen=True)
class OutcomeCount:
    outcome: str
    count: int
    percent: float

@dataclass(frozen=True)
class Funnel:
    hit_gate: int
    refused: int
    then_passed: int

@dataclass(frozen=True)
class ChatStat:
    chat_id: int
    title: str
    username: str
    bot_admin: bool
    enabled: bool
    decisions: int
    deny_rate: float
    unknown_rate: float
    last_decision_at: str | None

@dataclass(frozen=True)
class CacheStat:
    hits: int
    misses: int
    hit_rate: float
    p50_ms: int
    p95_ms: int

@dataclass(frozen=True)
class RefusedUser:
    tg_id: int
    display_name: str
    denials: int

@dataclass(frozen=True)
class Health:
    unknown_rate: float
    error_rate: float
    chats_needing_attention: int
    cache_hit_rate: float

@dataclass(frozen=True)
class ForceJoinStats:
    """One range's complete answer. ``has_data=False`` means "no decisions" —
    never a zero that looks like a healthy zero."""

    range: RangeKey
    has_data: bool
    gate: GateCounts
    volume: Volume
    outcomes: tuple[OutcomeCount, ...]
    funnel: Funnel
    per_chat: tuple[ChatStat, ...]
    cache: CacheStat
    top_refused: tuple[RefusedUser, ...]
    health: Health
    per_day_outcomes: tuple[tuple[str, str, int], ...] = ()

    def outcome_count(self, outcome: str) -> int:
        for entry in self.outcomes:
            if entry.outcome == outcome:
                return entry.count
        return 0
