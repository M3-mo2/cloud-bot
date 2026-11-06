"""``Subscriptions`` — the wallet + subscription port.

The single source of truth for "is this user's plan active?" and "how many Stars
do they have?". Implemented by ``adapters/store/sqlite_subscriptions.py``; used
by the services (and, in part 2, by the payment handlers). Pure data in, pure data
out — no Telegram, no HTTP.
"""

from __future__ import annotations

from typing import Protocol

from cloudbot.domain.subscription import Subscription, Wallet


class Subscriptions(Protocol):
    async def get(self, tg_id: int) -> Subscription | None: ...

    async def wallet(self, tg_id: int) -> Wallet: ...

    async def credit(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet: ...

    async def debit(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet: ...

    async def set_balance(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet: ...

    async def refund(
        self,
        tg_id: int,
        amount: int,
        reason: str,
        actor: int,
        *,
        idempotency_key: str = "",
    ) -> Wallet: ...

    async def purchase(
        self,
        tg_id: int,
        plan_code: str,
        *,
        idempotency_key: str = "",
    ) -> Subscription: ...

    async def grant(
        self,
        tg_id: int,
        plan_code: str,
        days: int,
        actor: int,
        reason: str,
        *,
        idempotency_key: str = "",
    ) -> Subscription | None: ...

    async def revoke(
        self,
        tg_id: int,
        actor: int,
        reason: str,
        *,
        idempotency_key: str = "",
    ) -> Subscription | None: ...

    async def start_trial(self, tg_id: int) -> Subscription | None: ...

    async def trial_granted(self, tg_id: int) -> bool: ...
