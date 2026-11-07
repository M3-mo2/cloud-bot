"""The paid-plan gate must bite at the **service** layer.

The premise this guards against: ``domain.subscription`` being enforced only in a
presentation path, so a direct service call, a scheduled job or a new entry point
reaches the engine with no check at all. Before ``SubscriptionService`` existed
there was no plan middleware either — the ``fj:`` middleware is *channel*
membership, a different product feature — so the hole was total.

These tests call the service directly and never run any middleware, which is the
point: if the gate stops living in the service layer, they fail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cloudbot.core.errors import SubscriptionExpired
from cloudbot.domain.subscription import Subscription, SubscriptionSource
from cloudbot.services.subscription import SubscriptionService

ADMIN = 999
USER = 1
WS = "ws_01TEST"


def _row(*, expires_in_days: int) -> Subscription:
    now = datetime.now(timezone.utc)
    return Subscription(
        tg_id=USER,
        plan_code="trial",
        started_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=expires_in_days),
        source=SubscriptionSource.TRIAL,
        ws_id=WS,
    )


class _Subs:
    """The narrow read surface the gate needs."""

    def __init__(self, row: Subscription | None) -> None:
        self._row = row

    async def get(self, tg_id: int, ws_id: str) -> Subscription | None:
        return self._row


def _service(row: Subscription | None) -> SubscriptionService:
    return SubscriptionService(_Subs(row), admin_ids=[ADMIN])


async def test_expired_subscription_is_refused_without_any_middleware() -> None:
    """The bite: an expired plan fails at the service layer, on its own."""
    with pytest.raises(SubscriptionExpired):
        await _service(_row(expires_in_days=-2)).assert_usable(USER, WS)


async def test_active_subscription_passes() -> None:
    """A guard that refuses everything is not a gate, it is an outage."""
    await _service(_row(expires_in_days=5)).assert_usable(USER, WS)


async def test_no_subscription_at_all_is_refused() -> None:
    """A workspace with no row must not run free — that is the revenue hole."""
    with pytest.raises(SubscriptionExpired):
        await _service(None).assert_usable(USER, WS)


async def test_admin_is_exempt_from_the_gate() -> None:
    """The operator must not lock himself out of the panel that fixes things.

    He holds no subscription for every workspace he inspects, so a gate without
    this exemption would make the admin panel unusable the moment it was correct.
    """
    await _service(None).assert_usable(ADMIN, WS)
