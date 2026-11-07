"""``SubscriptionService`` — the paid-plan gate **at the service layer**.

This is the mirror of the moderation rule the owner stated for bans: *blocking
lives in the service layer — every path must fail.* It is deliberately **not** a
middleware: a scheduled job, a future admin action, a script or any new entry
point reaches the services directly, and a middleware would cover only Telegram
updates. Every work method on the workspace-scoped services calls
:meth:`assert_usable` after the ownership gate, so an expired (or absent) plan
refuses compute no matter how the call arrived.

Reads are **not** gated: the engine allows reads, usage, file reads and delete on
a locked workspace (INTEGRATION-GUIDE §8.4), and the user's files stay safe. Only
the operations that start compute or mutate stored state are refused.

The admin allowlist is exempt for the same anti-lockout reason the ban and
force-join gates exempt it: a plan bug must never lock the operator out of the
panel that would fix it.

The engine is also told the term (``EnginePort.extend_subscription``) so its own
``403 subscription_expired`` can fire; that sync is **best-effort** — the local
store remains the source of truth, so a failed sync is logged, never fatal.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from cloudbot.core.errors import SubscriptionExpired
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.subscriptions import Subscriptions
from cloudbot.domain.subscription import SubscriptionStatus, derive_status
from cloudbot.domain.value_objects import WorkspaceId

log = structlog.get_logger("cloudbot.subscription_gate")

class SubscriptionService:
    def __init__(
        self,
        subscriptions: Subscriptions,
        *,
        admin_ids: "frozenset[int] | set[int] | None" = None,
    ) -> None:
        self._subscriptions = subscriptions
        self._admin_ids = frozenset(admin_ids or ())

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self._admin_ids

    async def status(
        self, tg_id: int, ws_id: WorkspaceId | str, *, now: datetime | None = None
    ) -> SubscriptionStatus:
        now = now or datetime.now(timezone.utc)
        return derive_status(await self._subscriptions.get(tg_id, str(ws_id)), now)

    async def assert_usable(
        self, tg_id: int, ws_id: WorkspaceId | str, *, now: datetime | None = None
    ) -> None:
        """Refuse **work** on a workspace whose plan is not active.

        The admin allowlist is exempt (anti-lockout). A missing row is ``none`` —
        not "unlimited" — so a planless workspace is refused exactly like an
        expired one. This is the bite: it holds with no middleware in the path.
        """
        if tg_id in self._admin_ids:
            return
        now = now or datetime.now(timezone.utc)
        subscription = await self._subscriptions.get(tg_id, str(ws_id))
        if not derive_status(subscription, now).is_active:
            raise SubscriptionExpired(str(ws_id))

    async def sync_engine(
        self,
        engine: EnginePort,
        ws_id: WorkspaceId | str,
        expires_at: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Tell the engine the new expiry. Best-effort; never raises.

        The engine refuses a *shorter* term and a non-future instant, so this is
        only called on **forward** extensions (create, purchase, admin grant). A
        shorten/revoke is not pushed (the engine has no way to accept one); the
        service gate still refuses work immediately because the bot's store is
        the source of truth.
        """
        now = now or datetime.now(timezone.utc)
        if expires_at <= now:
            return False
        try:
            await engine.extend_subscription(ws_id, expires_at=expires_at)
            return True
        except Exception as exc:  # noqa: BLE001 - the local store remains truth
            log.warning(
                "subscription.engine_sync_failed",
                ws_id=str(ws_id),
                error=type(exc).__name__,
            )
            return False

__all__ = ["SubscriptionService"]
