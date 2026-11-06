"""Billing use-cases: Stars delivery, plan purchase, and refunds.

All money rules live here; the handlers only speak Telegram. The service depends
on the ``Subscriptions`` port, so it is testable with an in-memory store and no
network.

Two guarantees are visible in this module:

* **the catalogue is the price** — :func:`cloudbot.domain.billing.decode_payload`
  returns the pack, and the credited amount is the pack's ``stars``; a number in
  the payload is never trusted.
* **delivery is idempotent** — a Telegram re-delivery is keyed by
  ``telegram_payment_charge_id`` on both the payment row and the wallet ledger,
  so the second delivery neither records nor credits anything twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cloudbot.domain.billing import StarPack, StarsPayment, decode_payload
from cloudbot.domain.ports.subscriptions import Subscriptions
from cloudbot.domain.plans import Plan, plan as plan_for, paid_plans
from cloudbot.domain.subscription import Subscription, SubscriptionStatus, derive_status

@dataclass(frozen=True)
class Quote:
    """A verified invoice: the buyer, the pack and the catalogue price."""

    tg_id: int
    pack: StarPack

@dataclass(frozen=True)
class PaymentResult:
    tg_id: int
    stars: int
    balance: int
    #: ``False`` when this was a Telegram re-delivery of an already credited charge.
    credited: bool

def quote(payload: str, amount: int) -> Quote | None:
    """Verify an invoice payload and amount against the catalogue.

    Returns ``None`` for a forged payload, an unknown pack, or an amount that is
    not the pack's catalogue price. The caller cancels the checkout with a
    human-readable reason; nothing is credited here (only ``successful_payment``
    credits).
    """
    decoded = decode_payload(payload)
    if decoded is None:
        return None
    tg_id, pack = decoded
    if amount != pack.stars:
        return None
    return Quote(tg_id=tg_id, pack=pack)

def quote_rejection(payload: str, amount: int) -> str:
    """The catalog key of why a checkout is refused (human, never a raw code)."""
    if decode_payload(payload) is None:
        return "pay.checkout.forged"
    return "pay.checkout.amount"

class BillingService:
    def __init__(self, subscriptions: Subscriptions) -> None:
        self._subscriptions = subscriptions

    async def wallet(self, tg_id: int):
        return await self._subscriptions.wallet(tg_id)

    async def subscription(self, tg_id: int) -> Subscription | None:
        return await self._subscriptions.get(tg_id)

    async def status(
        self, tg_id: int, *, now: datetime | None = None
    ) -> SubscriptionStatus:
        now = now or datetime.now(timezone.utc)
        return derive_status(await self._subscriptions.get(tg_id), now)

    async def buy_plan(
        self, tg_id: int, plan_code: str, *, now: datetime | None = None
    ) -> Subscription:
        """Buy a plan from the balance. Raises ``InsufficientFunds`` untouched."""
        return await self._subscriptions.purchase(tg_id, plan_code)

    async def deliver_payment(
        self,
        *,
        tg_id: int,
        pack: StarPack,
        charge_id: str,
        provider_charge_id: str = "",
        now: datetime | None = None,
    ) -> PaymentResult:
        """Credit a delivered Stars payment exactly once.

        The payment row is inserted first as the claim (``charge_id`` is unique),
        then the credit is applied with the same charge id as its idempotency key.
        Both are idempotent, so a crash between them is repaired by the customer's
        next tap or Telegram's re-delivery instead of losing or doubling Stars.
        """
        if not charge_id:
            raise ValueError("charge_id is required")
        now = now or datetime.now(timezone.utc)
        # The payment row is the single-flight claim: only the caller that
        # inserts ``charge_id`` may credit, so two concurrent deliveries cannot
        # both report (or apply) a credit.
        newly_recorded = await self._subscriptions.record_payment(
            tg_id=tg_id,
            pack_code=pack.code,
            stars=pack.stars,
            charge_id=charge_id,
            provider_charge_id=provider_charge_id,
            at=now,
        )
        wallet = await self._subscriptions.credit(
            tg_id,
            pack.stars,
            f"stars:{pack.code}",
            tg_id,
            idempotency_key=f"wallet:{tg_id}:{charge_id}",
        )
        return PaymentResult(
            tg_id=tg_id,
            stars=pack.stars,
            balance=wallet.balance_stars,
            credited=newly_recorded,
        )

    async def refund(
        self,
        *,
        tg_id: int,
        charge_id: str,
        actor: int,
        reason: str,
        now: datetime | None = None,
    ) -> StarsPayment | None:
        """Refund a recorded payment and reverse its wallet credit, once.

        Returns the payment after the attempt, or ``None`` when no such payment
        exists. A payment that was already refunded is returned unchanged and the
        ledger is not touched again.
        """
        payment = await self._subscriptions.payment(charge_id)
        if payment is None or payment.tg_id != tg_id:
            return None
        if not payment.refundable:
            return payment
        now = now or datetime.now(timezone.utc)
        await self._subscriptions.reverse(
            tg_id,
            payment.stars,
            reason or f"refund:{charge_id}",
            actor,
            idempotency_key=f"refund:{charge_id}",
        )
        await self._subscriptions.mark_refunded(charge_id, now)
        return await self._subscriptions.payment(charge_id)

    async def payments(self, tg_id: int) -> tuple[StarsPayment, ...]:
        return await self._subscriptions.payments_for(tg_id)

    async def payment(self, charge_id: str) -> StarsPayment | None:
        return await self._subscriptions.payment(charge_id)

    @staticmethod
    def plans() -> tuple[Plan, ...]:
        return paid_plans()

    @staticmethod
    def plan(plan_code: str) -> Plan | None:
        try:
            return plan_for(plan_code)
        except KeyError:
            return None

__all__ = [
    "BillingService",
    "PaymentResult",
    "Quote",
    "quote",
    "quote_rejection",
]
