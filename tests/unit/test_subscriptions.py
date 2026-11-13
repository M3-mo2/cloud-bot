"""The subscription domain and its SQLite store (task subs1).

Offline: no network, no Telegram, no engine. Time-dependent domain functions take
an explicit ``now``; store tests accept the wall clock and assert relative facts.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from cloudbot.adapters.persistence.database import MIGRATIONS_DIR, Database
from cloudbot.adapters.store.sqlite_subscriptions import (
    InsufficientFunds,
    SqliteSubscriptions,
    UnknownPlan,
)
from cloudbot.domain import plans
from cloudbot.domain.subscription import (
    Subscription,
    SubscriptionSource,
    SubscriptionState,
    derive_status,
    extend,
    lock_state,
)
from cloudbot.domain.value_objects import WorkspaceStatus

NOW = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


@pytest.fixture
async def store(db):
    return SqliteSubscriptions(db)


# -- catalogue ---------------------------------------------------------------
def test_the_three_paid_plans_are_one_catalogue():
    assert [p.code for p in plans.paid_plans()] == ["week", "month", "year"]
    expected = {
        "week": (7, 50, 2, 1.0, 512, 1024),
        "month": (30, 150, 3, 2.0, 1024, 5120),
        "year": (365, 1200, 5, 2.0, 2048, 10240),
    }
    for code, (period, price, max_ws, cpu, ram, disk) in expected.items():
        plan = plans.plan(code)
        assert plan.period_days == period
        assert plan.amount_stars == price
        assert plan.max_workspaces == max_ws
        assert plan.limits.cpu_cores == cpu
        assert plan.limits.memory_mb == ram
        assert plan.limits.disk_mb == disk


def test_the_commitment_is_rewarded():
    week_rate = plans.plan("week").amount_stars / plans.plan("week").period_days
    month_rate = plans.plan("month").amount_stars / plans.plan("month").period_days
    year_rate = plans.plan("year").amount_stars / plans.plan("year").period_days
    assert month_rate < week_rate * 0.75
    assert year_rate < month_rate
    # A year is cheaper than 52 weeks and than 12 months.
    assert plans.plan("year").amount_stars < 52 * plans.plan("week").amount_stars
    assert plans.plan("year").amount_stars < 12 * plans.plan("month").amount_stars


# -- status derivation -------------------------------------------------------
WS = "ws_1"

def _sub(days: int, *, source=SubscriptionSource.STARS, start=NOW) -> Subscription:
    return Subscription(
        tg_id=1,
        plan_code="week",
        started_at=start,
        expires_at=start + timedelta(days=days),
        source=source,
        ws_id=WS,
    )


def test_status_derivation_is_one_function():
    assert derive_status(None, NOW).state is SubscriptionState.NONE
    assert derive_status(None, NOW).workspaces_locked is True

    trial = derive_status(_sub(3, source=SubscriptionSource.TRIAL), NOW)
    assert trial.state is SubscriptionState.TRIALING
    assert trial.is_active and trial.days_left == 3

    paid = derive_status(_sub(30), NOW)
    assert paid.state is SubscriptionState.ACTIVE
    assert paid.is_active and paid.days_left == 30


def test_expiry_flips_to_expired_and_locks():
    sub = _sub(7)
    later = NOW + timedelta(days=7, seconds=1)
    status = derive_status(sub, later)
    assert status.state is SubscriptionState.EXPIRED
    assert status.is_active is False
    assert status.workspaces_locked is True
    assert lock_state(WorkspaceStatus.ACTIVE.value, sub, later) == "locked"
    # A suspended workspace keeps its own engine status.
    assert lock_state("suspended", sub, later) == "suspended"
    # An active plan does not lock.
    assert lock_state("active", sub, NOW) == "active"


def test_days_left_rounds_up_and_never_negative():
    sub = _sub(0)
    sub = Subscription(
        tg_id=1,
        plan_code="week",
        started_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        source=SubscriptionSource.STARS,
        ws_id=WS,
    )
    assert derive_status(sub, NOW).days_left == 1
    assert derive_status(sub, NOW + timedelta(days=1)).days_left == 0


def test_extend_continues_from_the_current_expiry():
    current = _sub(5)
    extended = extend(
        current, tg_id=1, ws_id=WS, plan_code="month", days=30,
        source=SubscriptionSource.STARS, now=NOW,
    )
    assert extended.expires_at == current.expires_at + timedelta(days=30)
    assert extended.plan_code == "month"


# -- wallet rules ------------------------------------------------------------
async def test_credit_debit_and_the_balance_is_the_ledger(store):
    wallet = await store.credit(1, 100, "topup", actor=999)
    assert wallet.balance_stars == 100
    assert wallet.balanced and wallet.ledger_sum == 100
    assert wallet.ledger[-1].kind == "credit"
    assert wallet.ledger[-1].actor_tg_id == 999

    wallet = await store.debit(1, 40, "spend", actor=999)
    assert wallet.balance_stars == 60
    assert wallet.balanced
    # Movements are appended, never mutated.
    assert [m.amount for m in wallet.ledger] == [100, -40]


async def test_debit_below_zero_is_refused_not_clamped(store):
    await store.credit(1, 10, "topup", actor=1)
    with pytest.raises(InsufficientFunds):
        await store.debit(1, 11, "spend", actor=1)
    wallet = await store.wallet(1)
    assert wallet.balance_stars == 10
    assert len(wallet.ledger) == 1


async def test_admin_set_credit_and_debit_all_append_an_attributed_movement(store):
    await store.credit(1, 50, "gift", actor=7)
    await store.debit(1, 20, "correction", actor=7)
    wallet = await store.set_balance(1, 300, "manual reset", actor=7)
    assert wallet.balance_stars == 300
    kinds = [m.kind for m in wallet.ledger]
    assert kinds == ["credit", "debit", "admin_set"]
    assert all(m.actor_tg_id == 7 for m in wallet.ledger)
    assert all(m.reason for m in wallet.ledger)
    assert wallet.balanced


async def test_balance_always_equals_the_ledger_after_a_random_sequence(store):
    rng = random.Random(20261103)
    for step in range(200):
        balance = (await store.wallet(1)).balance_stars
        action = rng.choice(["credit", "debit", "set"])
        try:
            if action == "credit":
                await store.credit(1, rng.randint(1, 100), f"c{step}", actor=step)
            elif action == "debit":
                await store.debit(1, rng.randint(1, 100), f"d{step}", actor=step)
            else:
                await store.set_balance(1, rng.randint(0, 500), f"s{step}", actor=step)
        except InsufficientFunds:
            assert (await store.wallet(1)).balance_stars == balance
        wallet = await store.wallet(1)
        assert wallet.balance_stars == wallet.ledger_sum
        assert wallet.balance_stars >= 0


# -- purchase ----------------------------------------------------------------
async def test_purchase_with_enough_balance_activates_and_debits(store):
    await store.credit(1, 200, "topup", actor=1)
    sub = await store.purchase(1, "ws_a", "month")
    wallet = await store.wallet(1)
    assert wallet.balance_stars == 50
    assert wallet.ledger[-1].amount == -150
    assert sub.plan_code == "month"
    assert derive_status(sub, sub.started_at).is_active
    assert (sub.expires_at - sub.started_at) == timedelta(days=30)
    assert sub.source is SubscriptionSource.STARS


async def test_purchase_without_enough_balance_is_refused_untouched(store):
    await store.credit(1, 149, "topup", actor=1)
    with pytest.raises(InsufficientFunds):
        await store.purchase(1, "ws_a", "month")
    wallet = await store.wallet(1)
    assert wallet.balance_stars == 149
    assert len(wallet.ledger) == 1
    assert await store.get(1, "ws_a") is None


async def test_purchase_extends_an_active_plan(store):
    await store.credit(1, 100, "topup", actor=1)
    first = await store.purchase(1, "ws_a", "week")
    second = await store.purchase(1, "ws_a", "week")
    assert second.expires_at == first.expires_at + timedelta(days=7)
    assert (await store.wallet(1)).balance_stars == 0


async def test_unknown_or_trial_plan_is_not_purchasable(store):
    await store.credit(1, 1000, "topup", actor=1)
    with pytest.raises(UnknownPlan):
        await store.purchase(1, "ws_a", "trial")
    with pytest.raises(UnknownPlan):
        await store.purchase(1, "ws_a", "decade")
    assert (await store.wallet(1)).balance_stars == 1000


async def test_two_concurrent_purchases_cannot_both_succeed(store):
    """The proof: one balance, two concurrent purchases, exactly one wins."""
    await store.credit(1, 50, "topup", actor=1)
    results = await asyncio.gather(
        store.purchase(1, "ws_a", "week"), store.purchase(1, "ws_a", "week"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, InsufficientFunds)]
    assert len(successes) == 1
    assert len(failures) == 1
    wallet = await store.wallet(1)
    assert wallet.balance_stars == 0
    assert [m.amount for m in wallet.ledger] == [50, -50]


async def test_two_connections_cannot_both_spend_one_balance(tmp_path):
    """The same proof at the database level: two connections, one file.

    ``BEGIN IMMEDIATE`` serialises them, so the second sees the debited balance
    and is refused — the guarantee survives a second process, not just the
    in-process lock.
    """

    path = str(tmp_path / "shared.db")
    first_db = Database(f"sqlite+aiosqlite:///{path}")
    await first_db.connect()
    await first_db.migrate()
    second_db = Database(f"sqlite+aiosqlite:///{path}")
    await second_db.connect()

    first = SqliteSubscriptions(first_db)
    second = SqliteSubscriptions(second_db)
    await first.credit(1, 50, "topup", actor=1)

    results = await asyncio.gather(
        first.purchase(1, "ws_a", "week"), second.purchase(1, "ws_a", "week"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, InsufficientFunds)]
    assert len(successes) == 1, results
    assert len(failures) == 1, results
    assert (await first.wallet(1)).balance_stars == 0
    await first_db.close()
    await second_db.close()


# -- idempotency -------------------------------------------------------------
async def test_replaying_a_credit_key_does_not_credit_twice(store):
    first = await store.credit(1, 100, "stars invoice", actor=1, idempotency_key="pay_1")
    second = await store.credit(1, 100, "stars invoice", actor=1, idempotency_key="pay_1")
    assert first.balance_stars == second.balance_stars == 100
    assert len(second.ledger) == 1


async def test_replaying_a_purchase_key_does_not_extend_twice(store):
    await store.credit(1, 500, "topup", actor=1)
    first = await store.purchase(1, "ws_a", "month", idempotency_key="p1")
    second = await store.purchase(1, "ws_a", "month", idempotency_key="p1")
    assert second.expires_at == first.expires_at
    assert (await store.wallet(1)).balance_stars == 350


async def test_replaying_a_debit_key_does_not_debit_twice(store):
    await store.credit(1, 100, "topup", actor=1)
    await store.debit(1, 10, "x", actor=1, idempotency_key="d1")
    wallet = await store.debit(1, 10, "x", actor=1, idempotency_key="d1")
    assert wallet.balance_stars == 90


async def test_refund_appends_a_positive_movement(store):
    await store.credit(1, 100, "topup", actor=1)
    await store.debit(1, 100, "purchase", actor=1)
    wallet = await store.refund(1, 100, "refund", actor=999)
    assert wallet.balance_stars == 100
    assert wallet.ledger[-1].kind == "refund"
    assert wallet.ledger[-1].amount == 100
    assert wallet.balanced


# -- trial -------------------------------------------------------------------
async def test_trial_is_granted_once_and_never_extended(store):
    trial = await store.start_trial(1, "ws_a")
    assert trial is not None
    assert trial.source is SubscriptionSource.TRIAL
    assert (trial.expires_at - trial.started_at) == timedelta(days=3)
    again = await store.start_trial(1, "ws_a")
    assert again is not None
    assert again.expires_at == trial.expires_at
    assert await store.trial_granted(1) is True


async def test_trial_does_not_overwrite_a_paid_subscription(store):
    await store.credit(1, 50, "topup", actor=1)
    paid = await store.purchase(1, "ws_a", "week")
    # Even if the trial path were reached later, it must not be granted.
    result = await store.start_trial(1, "ws_a")
    assert result is not None
    assert result.expires_at == paid.expires_at


# -- admin grant / revoke ----------------------------------------------------
async def test_admin_grant_extends_and_shortens(store):
    granted = await store.grant(1, "ws_a", "month", 30, actor=999, reason="support")
    assert granted is not None and derive_status(granted, granted.started_at).is_active
    extended = await store.grant(1, "ws_a", "month", 5, actor=999, reason="goodwill")
    assert extended.expires_at == granted.expires_at + timedelta(days=5)
    shortened = await store.grant(1, "ws_a", "month", -10, actor=999, reason="correction")
    assert shortened.expires_at == extended.expires_at - timedelta(days=10)


async def test_admin_revoke_ends_it_and_locks(store):
    granted = await store.grant(1, "ws_a", "month", 30, actor=999, reason="support")
    revoked = await store.revoke(1, "ws_a", actor=999, reason="refund")
    assert revoked is not None
    assert derive_status(revoked, revoked.expires_at).is_active is False
    assert lock_state("active", revoked, revoked.expires_at) == "locked"


async def test_revoke_without_a_subscription_is_a_no_op(store):
    assert await store.revoke(1, "ws_a", actor=999, reason="none") is None


# -- wiring: the existing workspace path uses the new model ------------------
async def test_creating_the_first_workspace_starts_the_trial(services, users):
    from cloudbot.core.locale import Locale

    await users.get_or_create(5, default_locale=Locale.EN)
    ws = await services.workspaces.create(5, "first", plan_code="trial")
    subscription = await services.admin.subscription(5, str(ws.id))
    assert subscription is not None
    assert subscription.source is SubscriptionSource.TRIAL
    assert subscription.ws_id == str(ws.id)
    assert await services.workspaces._subscriptions.trial_granted(5) is True


async def test_an_inactive_subscription_derives_locked_workspaces(services, users):
    from cloudbot.core.locale import Locale

    await users.get_or_create(6, default_locale=Locale.EN)
    await services.workspaces.create(6, "demo", plan_code="trial")
    # The trial is active: the workspace reads active.
    listed = await services.workspaces.list(6)
    assert listed and listed[0].status is WorkspaceStatus.ACTIVE

    # Revoking ends the plan; the same stored workspace now reads locked.
    await services.admin.revoke(6, "test", actor=999)
    listed = await services.workspaces.list(6)
    assert listed and listed[0].status is WorkspaceStatus.LOCKED


# -- migration ---------------------------------------------------------------
async def test_existing_database_upgrades_without_data_loss(tmp_path):
    path = str(tmp_path / "existing.db")
    async with aiosqlite.connect(path) as conn:
        for name in (
            "0001_init.sql",
            "0002_admin.sql",
            "0003_session.sql",
            "0004_keyboard_reset.sql",
        ):
            await conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
            version = int(name[:4])
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
        await conn.execute(
            "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at, display_name) "
            "VALUES (7, 'ar', 'free', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 'Sara')"
        )
        await conn.execute(
            "INSERT INTO user_workspaces (tg_id, ws_id, created_at) "
            "VALUES (7, 'ws_OLD', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()

    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.connect()
    assert await database.migrate() == [5, 6, 7, 8, 9, 10, 11, 12, 13]

    conn = await database.connect()
    async with conn.execute("SELECT tg_id, locale, display_name FROM users") as cur:
        row = await cur.fetchone()
    assert (int(row["tg_id"]), row["locale"], row["display_name"]) == (7, "ar", "Sara")
    async with conn.execute("SELECT ws_id FROM user_workspaces") as cur:
        assert [str(r["ws_id"]) for r in await cur.fetchall()] == ["ws_OLD"]
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        names = {r["name"] for r in await cur.fetchall()}
    for table in ("subscriptions", "subscription_trials", "wallet_ledger", "subscription_events"):
        assert table in names
    # A re-run applies nothing.
    assert await database.migrate() == []
    await database.close()
