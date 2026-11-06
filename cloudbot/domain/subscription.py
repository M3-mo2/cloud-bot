"""Subscription and wallet — the pure money model.

No I/O, no clock of its own: every function takes the instant it reasons about.
The store (``adapters/store/sqlite_subscriptions.py``) is the only place that
persists these values; the domain decides what they mean.

Two invariants live here and are enforced by tests:

* a debit may never take a balance below zero (the store refuses, it does not
  clamp);
* every movement is appended to the ledger and the balance always equals the sum
  of the ledger.

A ``admin_set`` movement is stored as the *delta* it applied, so the sum identity
holds for every movement kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal


class SubscriptionSource(str, Enum):
    TRIAL = "trial"
    STARS = "stars"
    ADMIN = "admin"


MovementKind = Literal["credit", "debit", "admin_set", "refund", "reversal"]


@dataclass(frozen=True)
class Subscription:
    tg_id: int
    plan_code: str
    started_at: datetime
    expires_at: datetime
    source: SubscriptionSource

    def is_active(self, now: datetime) -> bool:
        return now < self.expires_at


@dataclass(frozen=True)
class Movement:
    tg_id: int
    kind: MovementKind
    #: Signed delta: positive for credit/refund, negative for debit/admin_set.
    amount: int
    reason: str
    at: datetime
    actor_tg_id: int
    idempotency_key: str = ""


@dataclass(frozen=True)
class Wallet:
    tg_id: int
    balance_stars: int
    ledger: tuple[Movement, ...] = ()

    @property
    def ledger_sum(self) -> int:
        return sum(m.amount for m in self.ledger)

    @property
    def balanced(self) -> bool:
        """The balance is exactly the sum of its ledger."""
        return self.balance_stars == self.ledger_sum


class SubscriptionState(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    EXPIRED = "expired"
    NONE = "none"


@dataclass(frozen=True)
class SubscriptionStatus:
    """The derived answer to "can this user use their workspaces right now?"."""

    state: SubscriptionState
    is_active: bool
    days_left: int

    @property
    def workspaces_locked(self) -> bool:
        return not self.is_active


def derive_status(subscription: Subscription | None, now: datetime) -> SubscriptionStatus:
    """The **single** place subscription state is derived.

    ``trialing`` and ``active`` are both usable; ``expired`` and ``none`` lock
    every workspace. ``days_left`` rounds up (a subscription ending in 2 h still
    has one day left) and is zero when not active.
    """
    if subscription is None:
        return SubscriptionStatus(SubscriptionState.NONE, False, 0)
    if now >= subscription.expires_at:
        return SubscriptionStatus(SubscriptionState.EXPIRED, False, 0)
    remaining = subscription.expires_at - now
    days_left = max(0, -(-int(remaining.total_seconds()) // 86_400))
    state = (
        SubscriptionState.TRIALING
        if subscription.source is SubscriptionSource.TRIAL
        else SubscriptionState.ACTIVE
    )
    return SubscriptionStatus(state, True, days_left)


def extend(
    subscription: Subscription | None,
    *,
    tg_id: int,
    plan_code: str,
    days: int,
    source: SubscriptionSource,
    now: datetime,
) -> Subscription:
    """Return a subscription valid for ``days`` from now.

    An already-active subscription is extended from its expiry (never truncates
    the time already paid for); an expired or missing one starts now.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    extended_active = subscription is not None and now < subscription.expires_at
    # A renewal keeps the original start; a fresh/expired one starts now.
    started_at = subscription.started_at if extended_active else now
    base = subscription.expires_at if extended_active else now
    return Subscription(
        tg_id=tg_id,
        plan_code=plan_code,
        started_at=started_at,
        expires_at=base + timedelta(days=days),
        source=source,
    )


def lock_state(
    workspace_status: str,
    subscription: Subscription | None,
    now: datetime,
) -> str:
    """The effective workspace status, derived — never a second stored lock.

    Only an ``active`` workspace is locked by an inactive subscription; a
    suspended or deleted workspace keeps its own engine-owned status.
    """
    if workspace_status == "active" and not derive_status(subscription, now).is_active:
        return "locked"
    return workspace_status


__all__ = [
    "Movement",
    "MovementKind",
    "Subscription",
    "SubscriptionSource",
    "SubscriptionState",
    "SubscriptionStatus",
    "Wallet",
    "derive_status",
    "extend",
    "lock_state",
]
