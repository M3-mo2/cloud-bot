"""SQLite implementation of the ``Subscriptions`` port.

The store owns **atomicity**: a purchase is one transaction (check balance →
debit → extend → append the ledger). The bot runs one shared autocommit
connection, so an ``asyncio.Lock`` serialises the transactions in-process and a
``BEGIN IMMEDIATE`` keeps a second writer (another process on the same file) out
— that is what makes two concurrent purchases on one balance impossible.

Idempotency is a caller-supplied key. It is stored on the ledger row and on the
subscription-event row; the partial unique indexes reject a replay, and the store
checks the key before doing anything, so ``successful_payment`` arriving twice
credits (or purchases) once. Keys are global, so a caller should namespace them
(e.g. ``wallet:{tg_id}:{charge_id}``) to keep two users' charge ids apart.

The balance is never stored: it is ``SUM(amount)`` over the ledger, so it cannot
drift from its own history.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.models import parse_dt
from cloudbot.domain import plans
from cloudbot.domain.subscription import (
    Movement,
    Subscription,
    SubscriptionSource,
    Wallet,
    extend,
)


class SubscriptionError(Exception):
    code = "subscription_error"


class InsufficientFunds(SubscriptionError):
    code = "insufficient_funds"

    def __init__(self, required: int, available: int) -> None:
        super().__init__(f"needs {required} stars, has {available}")
        self.required = required
        self.available = available


class UnknownPlan(SubscriptionError):
    code = "unknown_plan"

    def __init__(self, plan_code: str) -> None:
        super().__init__(plan_code)
        self.plan_code = plan_code


class SqliteSubscriptions:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = asyncio.Lock()

    # -- reads ---------------------------------------------------------------
    async def get(self, tg_id: int) -> Subscription | None:
        conn = await self._db.connect()
        return await self._get(conn, tg_id)

    async def wallet(self, tg_id: int) -> Wallet:
        conn = await self._db.connect()
        return await self._wallet(conn, tg_id)

    async def trial_granted(self, tg_id: int) -> bool:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT 1 FROM subscription_trials WHERE tg_id = ?", (tg_id,)
        ) as cur:
            return await cur.fetchone() is not None

    # -- mutations -----------------------------------------------------------
    async def credit(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet:
        if amount <= 0:
            raise ValueError("credit amount must be positive")
        return await self._mutate(
            tg_id, kind="credit", amount=amount, reason=reason, actor=actor,
            key=idempotency_key,
        )

    async def debit(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet:
        if amount <= 0:
            raise ValueError("debit amount must be positive")
        return await self._mutate(
            tg_id, kind="debit", amount=-amount, reason=reason, actor=actor,
            key=idempotency_key,
        )

    async def refund(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet:
        if amount <= 0:
            raise ValueError("refund amount must be positive")
        return await self._mutate(
            tg_id, kind="refund", amount=amount, reason=reason, actor=actor,
            key=idempotency_key,
        )

    async def set_balance(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet:
        if amount < 0:
            raise ValueError("balance can never be negative")
        return await self._mutate(
            tg_id, kind="admin_set", amount=amount, reason=reason, actor=actor,
            key=idempotency_key, absolute=True,
        )

    async def purchase(
        self, tg_id: int, plan_code: str, *, idempotency_key: str = ""
    ) -> Subscription:
        try:
            plan = plans.plan(plan_code)
        except KeyError as exc:
            raise UnknownPlan(plan_code) from exc
        if not plan.purchasable:
            raise UnknownPlan(plan_code)

        async with self._lock:
            conn = await self._db.connect()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key and await self._seen(conn, idempotency_key):
                    existing = await self._get(conn, tg_id)
                    await conn.execute("ROLLBACK")
                    if existing is None:
                        raise SubscriptionError("replayed purchase has no subscription")
                    return existing

                available = await self._balance(conn, tg_id)
                if available < plan.amount_stars:
                    raise InsufficientFunds(plan.amount_stars, available)

                now = datetime.now(timezone.utc)
                await self._append_ledger(
                    conn, tg_id, "debit", -plan.amount_stars,
                    f"purchase:{plan.code}", now, tg_id, idempotency_key,
                )
                current = await self._get(conn, tg_id)
                new = extend(
                    current, tg_id=tg_id, plan_code=plan.code,
                    days=plan.period_days, source=SubscriptionSource.STARS, now=now,
                )
                await self._put(conn, new, now)
                await self._append_event(
                    conn, tg_id, "purchase", plan.code, plan.period_days,
                    tg_id, f"purchase:{plan.code}", now, idempotency_key,
                )
                await conn.execute("COMMIT")
                return new
            except Exception:
                await self._rollback(conn)
                raise

    async def grant(
        self,
        tg_id: int,
        plan_code: str,
        days: int,
        actor: int,
        reason: str,
        *,
        idempotency_key: str = "",
    ) -> Subscription | None:
        if days == 0:
            raise ValueError("grant days must be non-zero")
        try:
            plans.plan(plan_code)
        except KeyError as exc:
            raise UnknownPlan(plan_code) from exc

        async with self._lock:
            conn = await self._db.connect()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key and await self._seen(conn, idempotency_key):
                    current = await self._get(conn, tg_id)
                    await conn.execute("ROLLBACK")
                    return current

                now = datetime.now(timezone.utc)
                current = await self._get(conn, tg_id)
                if current is None and days < 0:
                    await conn.execute("ROLLBACK")
                    return None
                if days < 0 and current is not None:
                    # An admin may shorten: the expiry moves back, never the start.
                    new = Subscription(
                        tg_id=current.tg_id,
                        plan_code=plan_code,
                        started_at=current.started_at,
                        expires_at=current.expires_at + timedelta(days=days),
                        source=SubscriptionSource.ADMIN,
                    )
                else:
                    new = extend(
                        current, tg_id=tg_id, plan_code=plan_code, days=days,
                        source=SubscriptionSource.ADMIN, now=now,
                    )
                await self._put(conn, new, now)
                await self._append_event(
                    conn, tg_id, "grant" if days > 0 else "shorten", plan_code,
                    days, actor, reason, now, idempotency_key,
                )
                await conn.execute("COMMIT")
                return new
            except Exception:
                await self._rollback(conn)
                raise

    async def revoke(
        self,
        tg_id: int,
        actor: int,
        reason: str,
        *,
        idempotency_key: str = "",
    ) -> Subscription | None:
        async with self._lock:
            conn = await self._db.connect()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key and await self._seen(conn, idempotency_key):
                    current = await self._get(conn, tg_id)
                    await conn.execute("ROLLBACK")
                    return current

                now = datetime.now(timezone.utc)
                current = await self._get(conn, tg_id)
                if current is None:
                    await conn.execute("ROLLBACK")
                    return None
                revoked = Subscription(
                    tg_id=current.tg_id,
                    plan_code=current.plan_code,
                    started_at=current.started_at,
                    expires_at=now,
                    source=current.source,
                )
                await self._put(conn, revoked, now)
                await self._append_event(
                    conn, tg_id, "revoke", current.plan_code, 0, actor, reason,
                    now, idempotency_key,
                )
                await conn.execute("COMMIT")
                return revoked
            except Exception:
                await self._rollback(conn)
                raise

    async def start_trial(self, tg_id: int) -> Subscription | None:
        async with self._lock:
            conn = await self._db.connect()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT 1 FROM subscription_trials WHERE tg_id = ?", (tg_id,)
                ) as cur:
                    already = await cur.fetchone() is not None
                if already:
                    current = await self._get(conn, tg_id)
                    await conn.execute("ROLLBACK")
                    return current

                now = datetime.now(timezone.utc)
                # The trial exists only for a user who has never had a plan; an
                # existing (even expired) subscription means "already past it".
                current = await self._get(conn, tg_id)
                if current is not None:
                    await conn.execute("ROLLBACK")
                    return current

                await conn.execute(
                    "INSERT OR IGNORE INTO subscription_trials (tg_id, granted_at) "
                    "VALUES (?, ?)",
                    (tg_id, now.isoformat().replace("+00:00", "Z")),
                )
                trial_plan = plans.plan("trial")
                new = extend(
                    current, tg_id=tg_id, plan_code=trial_plan.code,
                    days=trial_plan.period_days,
                    source=SubscriptionSource.TRIAL, now=now,
                )
                await self._put(conn, new, now)
                await self._append_event(
                    conn, tg_id, "trial", trial_plan.code, trial_plan.period_days,
                    tg_id, "trial", now, "",
                )
                await conn.execute("COMMIT")
                return new
            except Exception:
                await self._rollback(conn)
                raise

    # -- internals -----------------------------------------------------------
    async def _mutate(
        self,
        tg_id: int,
        *,
        kind: str,
        amount: int,
        reason: str,
        actor: int,
        key: str,
        absolute: bool = False,
    ) -> Wallet:
        async with self._lock:
            conn = await self._db.connect()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if key and await self._seen(conn, key):
                    wallet = await self._wallet(conn, tg_id)
                    await conn.execute("ROLLBACK")
                    return wallet

                now = datetime.now(timezone.utc)
                balance = await self._balance(conn, tg_id)
                delta = amount - balance if absolute else amount
                if balance + delta < 0:
                    raise InsufficientFunds(-delta, balance)
                await self._append_ledger(
                    conn, tg_id, kind, delta, reason, now, actor, key
                )
                await conn.execute("COMMIT")
                return await self._wallet(conn, tg_id)
            except Exception:
                await self._rollback(conn)
                raise

    @staticmethod
    async def _rollback(conn: aiosqlite.Connection) -> None:
        try:
            await conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 - already closed / no transaction
            pass

    async def _seen(self, conn: aiosqlite.Connection, key: str) -> bool:
        async with conn.execute(
            "SELECT 1 FROM wallet_ledger WHERE idempotency_key = ? LIMIT 1", (key,)
        ) as cur:
            return await cur.fetchone() is not None

    async def _balance(self, conn: aiosqlite.Connection, tg_id: int) -> int:
        async with conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM wallet_ledger WHERE tg_id = ?",
            (tg_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"])

    async def _ledger(self, conn: aiosqlite.Connection, tg_id: int) -> tuple[Movement, ...]:
        async with conn.execute(
            "SELECT * FROM wallet_ledger WHERE tg_id = ? ORDER BY id", (tg_id,)
        ) as cur:
            rows = await cur.fetchall()
        return tuple(
            Movement(
                tg_id=int(r["tg_id"]),
                kind=str(r["kind"]),  # type: ignore[arg-type]
                amount=int(r["amount"]),
                reason=str(r["reason"]),
                at=parse_dt(r["at"]),
                actor_tg_id=int(r["actor_tg_id"]),
                idempotency_key=str(r["idempotency_key"]),
            )
            for r in rows
        )

    async def _wallet(self, conn: aiosqlite.Connection, tg_id: int) -> Wallet:
        ledger = await self._ledger(conn, tg_id)
        return Wallet(
            tg_id=tg_id,
            balance_stars=sum(m.amount for m in ledger),
            ledger=ledger,
        )

    async def _get(self, conn: aiosqlite.Connection, tg_id: int) -> Subscription | None:
        async with conn.execute(
            "SELECT * FROM subscriptions WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Subscription(
            tg_id=int(row["tg_id"]),
            plan_code=str(row["plan_code"]),
            started_at=parse_dt(row["started_at"]),
            expires_at=parse_dt(row["expires_at"]),
            source=SubscriptionSource(str(row["source"])),
        )

    async def _put(
        self, conn: aiosqlite.Connection, sub: Subscription, now: datetime
    ) -> None:
        stamp = now.isoformat().replace("+00:00", "Z")
        await conn.execute(
            "INSERT INTO subscriptions "
            "(tg_id, plan_code, started_at, expires_at, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET "
            "plan_code=excluded.plan_code, started_at=excluded.started_at, "
            "expires_at=excluded.expires_at, source=excluded.source, "
            "updated_at=excluded.updated_at",
            (
                sub.tg_id,
                sub.plan_code,
                sub.started_at.isoformat().replace("+00:00", "Z"),
                sub.expires_at.isoformat().replace("+00:00", "Z"),
                sub.source.value,
                stamp,
            ),
        )

    async def _append_ledger(
        self,
        conn: aiosqlite.Connection,
        tg_id: int,
        kind: str,
        amount: int,
        reason: str,
        at: datetime,
        actor: int,
        key: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO wallet_ledger "
            "(tg_id, kind, amount, reason, at, actor_tg_id, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tg_id,
                kind,
                amount,
                reason,
                at.isoformat().replace("+00:00", "Z"),
                actor,
                key,
            ),
        )

    async def _append_event(
        self,
        conn: aiosqlite.Connection,
        tg_id: int,
        action: str,
        plan_code: str,
        days_delta: int,
        actor: int,
        reason: str,
        at: datetime,
        key: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO subscription_events "
            "(tg_id, action, plan_code, days_delta, actor_tg_id, reason, at, "
            "idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tg_id,
                action,
                plan_code,
                days_delta,
                actor,
                reason,
                at.isoformat().replace("+00:00", "Z"),
                key,
            ),
        )


__all__ = [
    "InsufficientFunds",
    "SqliteSubscriptions",
    "SubscriptionError",
    "UnknownPlan",
]
