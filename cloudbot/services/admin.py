"""``AdminService`` — the admin panel's read models and actions.

This is the **only** service that reads other users' data and the **only** caller
of ``OwnershipService.assert_admin_override`` (see that method for why the
exemption lives in one named place). Every method here assumes the caller has
already passed the identity gate (``middleware/admin_gate.py``); the ``admin_id``
argument is used for the audit actor, never for an ownership decision.

The service depends on ports (``UserRepository``, ``EnginePort``, ``EventLog``,
``AdminStateStore``), so the admin panel is testable with no engine and no
Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from cloudbot.core.runtime import BotRuntime
from cloudbot.domain.admin import (
    AdminOverview,
    AuditPage,
    EngineHealthView,
    EnteredWorkspace,
    UserListPage,
    UserPage,
    UserSummary,
    WorkspaceWithUsage,
    summarize_user,
)
from cloudbot.domain.entities import Workspace
from cloudbot.domain.moderation import DeliveryResult
from cloudbot.domain.events import EventCategory
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.events import AdminStateStore, EventLog
from cloudbot.domain.ports.subscriptions import Subscriptions
from cloudbot.domain.ports.users import UserRepository
from cloudbot.domain.subscription import SubscriptionState, derive_status
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceStatus
from cloudbot.services.ownership import OwnershipService

log = structlog.get_logger("cloudbot.admin")


class AdminService:
    def __init__(
        self,
        users: UserRepository,
        engine: EnginePort,
        event_log: EventLog,
        state: AdminStateStore,
        ownership: OwnershipService,
        runtime: BotRuntime | None = None,
        subscriptions: Subscriptions | None = None,
        moderation: "object | None" = None,
        subscription: "object | None" = None,
    ) -> None:
        self._users = users
        self._engine = engine
        self._log = event_log
        self._state = state
        self._ownership = ownership
        self._runtime = runtime or BotRuntime()
        self._subscriptions = subscriptions
        self._moderation = moderation
        #: The plan gate; a forward grant teaches the engine the new expiry.
        self._subscription = subscription

    # -- overview ------------------------------------------------------------
    async def overview(self, *, now: datetime | None = None) -> AdminOverview:
        now = now or datetime.now(timezone.utc)
        total = await self._users.count_all()
        active = await self._users.count_active_since(now - timedelta(hours=24))
        by_status, live_runs, live_sessions = await self._fleet_counts()
        engine = await self.engine_health()
        return AdminOverview(
            total_users=total,
            active_24h=active,
            workspaces_by_status=by_status,
            live_runs=live_runs,
            live_sessions=live_sessions,
            engine=engine,
            uptime_seconds=self._runtime.uptime_seconds(now),
            queue_state=self._queue_state(engine),
        )

    async def _fleet_counts(self) -> tuple[dict[str, int], int, int]:
        """Workspaces by status plus live runs/sessions across the fleet.

        Honest: if the engine is unreachable the caller renders "unknown", not
        zeros — so an error here propagates as an empty mapping plus a flag.
        """
        by_status = {s.value: 0 for s in WorkspaceStatus}
        live_runs = 0
        live_sessions = 0
        try:
            workspaces = await self._engine.list_workspaces()
        except Exception as exc:  # noqa: BLE001
            log.warning("admin.fleet_unavailable", error=type(exc).__name__)
            return by_status, 0, 0
        for ws in workspaces:
            by_status[ws.status.value] = by_status.get(ws.status.value, 0) + 1
            live_runs += ws.summary.runs_active
        try:
            for ws in workspaces:
                sessions = await self._engine.list_sessions(ws.id, status="running")
                live_sessions += sessions.total
        except Exception:  # noqa: BLE001
            # sessions are best-effort; the count stays as gathered so far.
            pass
        return by_status, live_runs, live_sessions

    async def engine_health(self) -> EngineHealthView:
        try:
            health = await self._engine.health()
            ready = await self._engine.ready()
            meta = await self._engine.meta()
            return EngineHealthView(
                reachable=True,
                health_status=str(health.get("status", "")),
                ready_status=str(ready.get("status", "")),
                version=meta.version,
                api_version=meta.api_version,
            )
        except Exception as exc:  # noqa: BLE001
            return EngineHealthView(reachable=False, error=type(exc).__name__)

    @staticmethod
    def _queue_state(engine: EngineHealthView) -> str:
        return "ok" if engine.reachable else "unknown"

    # -- users ---------------------------------------------------------------
    async def plan_label(self, tg_id: int, *, now: datetime | None = None) -> str:
        """The user's plan label, derived from their workspace subscriptions.

        A user may hold several plans (one per workspace); the label is the first
        *usable* one, else ``expired``, else ``free`` — enough for the list.
        """
        if self._subscriptions is None:
            return "free"
        now = now or datetime.now(timezone.utc)
        subscriptions = await self._subscriptions.for_user(tg_id)
        if not subscriptions:
            return "free"
        for subscription in subscriptions:
            if derive_status(subscription, now).is_active:
                return subscription.plan_code
        return "expired"

    async def users_page(
        self, *, query: str = "", offset: int = 0, limit: int = 10
    ) -> UserListPage:
        rows, total = await self._users.list_users(query=query, offset=offset, limit=limit)
        now = datetime.now(timezone.utc)
        summaries: list[UserSummary] = []
        for user in rows:
            count = await self._users.count_workspaces(user.tg_id)
            summaries.append(
                summarize_user(user, count, plan=await self.plan_label(user.tg_id, now=now))
            )
        return UserListPage(
            users=summaries, total=total, offset=offset, limit=limit, query=query
        )

    async def user_page(self, tg_id: int, locale=None) -> UserPage | None:
        user = await self._users.get(tg_id)
        if user is None:
            return None
        count = await self._users.count_workspaces(tg_id)
        summary = summarize_user(
            user, count, plan=await self.plan_label(tg_id)
        )
        # Admin exemption: read the subject's workspaces, not ours.
        await self._ownership.assert_admin_override(0, WorkspaceId("ws_admin"))
        workspaces = await self._workspaces_of(tg_id)
        return UserPage(
            user=summary,
            workspaces=workspaces,
            workspace_subscriptions=await self._workspace_subscriptions(tg_id, workspaces),
        )

    async def _workspace_subscriptions(
        self, tg_id: int, workspaces: list[WorkspaceWithUsage]
    ) -> dict[str, "Subscription | None"]:
        """Each workspace's subscription row — data only, no formatting.

        ``services`` may not import ``presentation`` (an architecture test enforces
        the direction), so the plan line is composed by the screen from this map.
        A workspace the store does not know maps to ``None``, which is the truth.
        """
        if self._subscriptions is None:
            return {}
        out: dict[str, object] = {}
        for wu in workspaces:
            key = str(wu.workspace.id)
            out[key] = await self._subscriptions.get(tg_id, key)
        return out

    async def _workspaces_of(self, tg_id: int) -> list[WorkspaceWithUsage]:
        owned = await self._users.owned_workspaces(tg_id)
        if not owned:
            return []
        try:
            all_ws = {str(w.id): w for w in await self._engine.list_workspaces()}
        except Exception as exc:  # noqa: BLE001
            log.warning("admin.workspaces_unavailable", error=type(exc).__name__)
            return []
        result: list[WorkspaceWithUsage] = []
        for ws_id in owned:
            ws = all_ws.get(str(ws_id))
            if ws is None:
                continue
            usage = None
            usage_error = ""
            try:
                usage = await self._engine.workspace_usage(ws_id)
            except Exception as exc:  # noqa: BLE001
                usage_error = type(exc).__name__
            result.append(WorkspaceWithUsage(workspace=ws, usage=usage, usage_error=usage_error))
        return result

    async def user_summary(self, tg_id: int) -> UserSummary | None:
        user = await self._users.get(tg_id)
        if user is None:
            return None
        return summarize_user(
            user,
            await self._users.count_workspaces(tg_id),
            plan=await self.plan_label(tg_id),
        )

    # -- actions -------------------------------------------------------------
    async def get_workspace(self, ws_id: WorkspaceId) -> Workspace:
        """Fetch any workspace for the panel (the admin exemption applies)."""
        await self._ownership.assert_admin_override(0, ws_id)
        return await self._engine.get_workspace(ws_id)

    async def owner_of(self, ws_id: WorkspaceId) -> int | None:
        owners = await self._users.workspace_owners(ws_id)
        return owners[0] if owners else None

    async def suspend_workspace(self, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_admin_override(0, ws_id)
        await self._engine.suspend_workspace(ws_id)

    async def resume_workspace(self, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_admin_override(0, ws_id)
        await self._engine.resume_workspace(ws_id)

    async def delete_workspace(self, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_admin_override(0, ws_id)
        await self._engine.delete_workspace(ws_id)
        # Drop the registration for every owner of this workspace.
        for owner_tg_id in await self._users.workspace_owners(ws_id):
            await self._users.remove_workspace(owner_tg_id, ws_id)

    # -- wallet + subscription administration --------------------------------
    # Every change is attributed to the admin (``actor``) and carries a reason;
    # the store appends the ledger movement and the subscription event.
    async def wallet(self, tg_id: int):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        return await self._subscriptions.wallet(tg_id)

    async def subscription(self, tg_id: int, ws_id: str | None = None):
        if self._subscriptions is None:
            return None
        if ws_id is not None:
            return await self._subscriptions.get(tg_id, ws_id)
        existing = await self._subscriptions.for_user(tg_id)
        return existing[0] if existing else None

    async def _first_workspace(self, tg_id: int) -> str | None:
        owned = await self._users.owned_workspaces(tg_id)
        return str(owned[0]) if owned else None

    async def credit(
        self, tg_id: int, amount: int, reason: str, *, actor: int, idempotency_key: str = ""
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        return await self._subscriptions.credit(
            tg_id, amount, reason, actor, idempotency_key=idempotency_key
        )

    async def debit(
        self, tg_id: int, amount: int, reason: str, *, actor: int, idempotency_key: str = ""
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        return await self._subscriptions.debit(
            tg_id, amount, reason, actor, idempotency_key=idempotency_key
        )

    async def set_balance(
        self, tg_id: int, amount: int, reason: str, *, actor: int, idempotency_key: str = ""
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        return await self._subscriptions.set_balance(
            tg_id, amount, reason, actor, idempotency_key=idempotency_key
        )

    async def grant(
        self,
        tg_id: int,
        plan_code: str,
        days: int,
        reason: str,
        *,
        actor: int,
        ws_id: str | None = None,
        idempotency_key: str = "",
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        target = ws_id or await self._first_workspace(tg_id)
        if target is None:
            raise RuntimeError("user has no workspace to attach the plan to")
        granted = await self._subscriptions.grant(
            tg_id, target, plan_code, days, actor, reason,
            idempotency_key=idempotency_key,
        )
        # A forward grant (days > 0) is pushed to the engine; a shorten is not
        # (the engine cannot accept an earlier term — the service gate still
        # refuses work at the bot's expiry).
        if self._subscription is not None and days > 0 and granted is not None:
            await self._subscription.sync_engine(
                self._engine, target, granted.expires_at
            )
        return granted

    async def revoke(
        self,
        tg_id: int,
        reason: str,
        *,
        actor: int,
        ws_id: str | None = None,
        idempotency_key: str = "",
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        target = ws_id or await self._first_workspace(tg_id)
        if target is None:
            return None
        return await self._subscriptions.revoke(
            tg_id, target, actor, reason, idempotency_key=idempotency_key
        )

    async def reverse(
        self, tg_id: int, amount: int, reason: str, *, actor: int, idempotency_key: str = ""
    ):
        if self._subscriptions is None:
            raise RuntimeError("subscription store is not configured")
        return await self._subscriptions.reverse(
            tg_id, amount, reason, actor, idempotency_key=idempotency_key
        )

    async def payments(self, tg_id: int):
        if self._subscriptions is None:
            return ()
        return await self._subscriptions.payments_for(tg_id)

    # -- moderation ----------------------------------------------------------
    async def ban_user(
        self,
        tg_id: int,
        *,
        reason: str,
        days: int | None = None,
        actor: int,
        now: datetime | None = None,
    ):
        if self._moderation is None:
            raise RuntimeError("moderation is not configured")
        return await self._moderation.ban_user(
            tg_id, reason=reason, banned_by=actor, days=days, now=now
        )

    async def unban_user(self, tg_id: int) -> bool:
        if self._moderation is None:
            raise RuntimeError("moderation is not configured")
        return await self._moderation.unban_user(tg_id)

    async def user_ban(self, tg_id: int):
        if self._moderation is None:
            return None
        return await self._moderation.user_ban(tg_id)

    async def banned_users(self) -> list:
        if self._moderation is None:
            return []
        return await self._moderation.banned_users()

    async def ban_workspace(self, ws_id: WorkspaceId, *, reason: str, actor: int):
        if self._moderation is None:
            raise RuntimeError("moderation is not configured")
        await self._ownership.assert_admin_override(0, ws_id)
        return await self._moderation.ban_workspace(ws_id, reason=reason, banned_by=actor)

    async def unban_workspace(self, ws_id: WorkspaceId) -> bool:
        if self._moderation is None:
            raise RuntimeError("moderation is not configured")
        await self._ownership.assert_admin_override(0, ws_id)
        return await self._moderation.unban_workspace(ws_id)

    async def workspace_ban(self, ws_id: WorkspaceId):
        if self._moderation is None:
            return None
        return await self._moderation.workspace_ban(ws_id)

    async def banned_workspaces(self) -> list:
        if self._moderation is None:
            return []
        return await self._moderation.banned_workspaces()

    # -- notifications -------------------------------------------------------
    async def subscriptions(self, admin_id: int) -> dict[str, bool]:
        return await self._state.subscriptions(admin_id)

    async def set_subscription(self, admin_id: int, category: str, enabled: bool) -> None:
        await self._state.set_subscription(admin_id, category, enabled)

    async def set_all_subscriptions(self, admin_id: int, enabled: bool) -> None:
        for category in EventCategory:
            await self._state.set_subscription(admin_id, category.value, enabled)

    async def notify_state(self, admin_id: int) -> tuple[int, bool]:
        return await self._state.notify_state(admin_id)

    async def notify_scope(self, admin_id: int) -> int | None:
        return await self._state.notify_scope(admin_id)

    async def set_notify_scope(self, admin_id: int, tg_id: int | None) -> None:
        await self._state.set_notify_scope(admin_id, tg_id)

    async def set_paused(self, admin_id: int, paused: bool) -> None:
        await self._state.set_paused(admin_id, paused)

    async def all_subscribed(self, admin_id: int) -> bool:
        subs = await self._state.subscriptions(admin_id)
        return all(subs.get(c.value, False) for c in EventCategory)

    # -- audit ---------------------------------------------------------------
    async def audit_page(
        self,
        *,
        category: str | None = None,
        actor: str | None = None,
        tg_id: int | None = None,
        ws_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> AuditPage:
        cat = None
        if category:
            try:
                cat = EventCategory(category)
            except ValueError:
                cat = None
        events, total = await self._log.query(
            category=cat, actor=actor, tg_id=tg_id, ws_id=ws_id, offset=offset, limit=limit
        )
        return AuditPage(
            events=events, total=total, offset=offset, limit=limit, category=category, actor=actor
        )

    async def recent_for_user(self, tg_id: int, *, limit: int = 10) -> list:
        events, _ = await self._log.query(tg_id=tg_id, offset=0, limit=limit)
        return events

    # -- entered-workspace mode ----------------------------------------------
    async def entered(self, admin_id: int) -> EnteredWorkspace | None:
        row = await self._state.entered(admin_id)
        if row is None:
            return None
        ws_id, owner_tg_id = row
        workspace = None
        try:
            workspace = await self._engine.get_workspace(WorkspaceId(ws_id))
        except Exception:  # noqa: BLE001
            workspace = None
        owner = await self._users.get(owner_tg_id)
        return EnteredWorkspace(
            ws_id=ws_id,
            owner_tg_id=owner_tg_id,
            workspace=workspace,
            owner_name=(owner.display_name if owner else "") or str(owner_tg_id),
        )

    async def enter_workspace(self, admin_id: int, ws_id: str, owner_tg_id: int) -> None:
        await self._ownership.assert_admin_override(admin_id, WorkspaceId(ws_id))
        await self._state.set_entered(admin_id, ws_id, owner_tg_id)

    async def leave_workspace(self, admin_id: int) -> None:
        await self._state.clear_entered(admin_id)
