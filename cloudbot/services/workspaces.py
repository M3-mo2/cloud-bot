"""Workspace use-cases: list, create, get, patch, lifecycle, usage.

Policy lives here: ownership checks, limit pre-validation against
``EngineMeta.limits_maxima``, and call ordering. No IO libraries.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from cloudbot.core.errors import (
    EngineRefused,
    InvalidInput,
    NotOwned,
    PlanRequired,
    TrialUsed,
    InsufficientFunds,
)
from cloudbot.domain import plans
from cloudbot.domain.entities import Workspace
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.subscriptions import Subscriptions
from cloudbot.domain.ports.users import UserRepository
from cloudbot.domain.subscription import SubscriptionState, derive_status
from cloudbot.domain.value_objects import LifecycleResult, Limits, ResourceUsage, WorkspaceId, WorkspaceStatus
from cloudbot.services.ownership import OwnershipService

log = structlog.get_logger("cloudbot.workspaces")

MAX_NAME_LEN = 128

_LIMIT_MAX_ATTRS = {
    "cpu_cores": "cpu_cores",
    "memory_mb": "memory_mb",
    "disk_mb": "disk_mb",
    "max_wall_time_sec": "max_wall_time_sec",
    "max_runs": "max_runs",
}


class WorkspacesService:
    def __init__(
        self,
        engine: EnginePort,
        users: UserRepository,
        ownership: OwnershipService,
        subscriptions: Subscriptions | None = None,
        moderation: "object | None" = None,
    ) -> None:
        self._engine = engine
        self._users = users
        self._ownership = ownership
        self._subscriptions = subscriptions
        self._moderation = moderation

    async def meta(self, refresh: bool = False) -> EngineMeta:
        return await self._engine.meta(refresh=refresh)

    def validate_name(self, name: str) -> str:
        stripped = (name or "").strip()
        if not stripped:
            raise InvalidInput("name", "empty")
        if len(stripped) > MAX_NAME_LEN:
            raise InvalidInput("name", "too_long")
        return stripped

    def validate_limits(self, limits: Limits | None, maxima: Limits) -> Limits | None:
        """Refuse values above maxima *before* calling the engine (never clamp)."""
        if limits is None:
            return None
        for field, attr in _LIMIT_MAX_ATTRS.items():
            value = getattr(limits, field)
            maximum = getattr(maxima, attr)
            if value > maximum:
                raise InvalidInput(field, "above_max")
        return limits

    async def plan_maxima(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        engine_maxima: Limits,
        *,
        now: datetime | None = None,
    ) -> Limits:
        """The ceiling **this workspace's** plan allows, capped by the engine.

        A plan sets the ceiling a workspace may request. Each workspace reads its
        own subscription, so two workspaces on different plans cannot borrow each
        other's allowance. With no active subscription the engine's own maxima
        apply unchanged.
        """
        if self._subscriptions is None:
            return engine_maxima
        now = now or datetime.now(timezone.utc)
        subscription = await self._subscriptions.get(tg_id, str(ws_id))
        if subscription is None or not derive_status(subscription, now).is_active:
            return engine_maxima
        plan = plans.PLANS.get(subscription.plan_code)
        if plan is None:
            return engine_maxima
        return plan.to_limits(engine_maxima)

    async def plan_options(self, tg_id: int) -> tuple[plans.Plan, ...]:
        """The plans the picker may offer: the trial (once) then the paid ones."""
        if self._subscriptions is None:
            return plans.paid_plans()
        granted = await self._subscriptions.trial_granted(tg_id)
        paid = plans.paid_plans()
        return paid if granted else (plans.plan("trial"),) + paid

    async def list(self, tg_id: int) -> list[Workspace]:
        owned = set(await self._users.owned_workspaces(tg_id))
        if not owned:
            return []
        workspaces = await self._engine.list_workspaces()
        by_id = {str(w.id): w for w in workspaces}
        result: list[Workspace] = []
        for ws_id in owned:
            ws = by_id.get(str(ws_id))
            if ws is not None:
                result.append(self._with_owner(ws, tg_id))
        return await self._with_subscription_state(tg_id, result)

    async def _with_subscription_state(
        self, tg_id: int, workspaces: list[Workspace]
    ) -> list[Workspace]:
        """Derive ``LOCKED`` from the subscription — never a second stored lock.

        The engine owns state changes; the bot only reads. A workspace the engine
        reports ``active`` is presented as ``locked`` when the owner's plan is not
        active. This is what the screens and the lock banner read.
        """
        result: list[Workspace] = []
        now = datetime.now(timezone.utc)
        for ws in workspaces:
            banned = await self._ban(ws.id)
            if banned is not None:
                # An admin ban outranks the subscription state: the owner sees a
                # locked workspace carrying the admin's reason, never a "renew".
                result.append(self._locked(ws, ban_reason=banned))
                continue
            if self._subscriptions is None:
                result.append(ws)
                continue
            subscription = await self._subscriptions.get(tg_id, str(ws.id))
            if derive_status(subscription, now).is_active:
                result.append(ws)
            else:
                result.append(self._locked(ws))
        return result

    async def _ban(self, ws_id) -> str | None:
        """The admin's ban reason, or ``None`` when the workspace is not banned."""
        if self._moderation is None:
            return None
        ban = await self._moderation.workspace_ban(ws_id)
        return ban.reason if ban is not None else None

    @staticmethod
    def _locked(ws: Workspace, *, ban_reason: str = "") -> Workspace:
        """Present a workspace as locked: by an expired plan or by an admin ban.

        A non-``ACTIVE`` status other than ``LOCKED`` (``SUSPENDED``, ``DELETED``,
        ``UNKNOWN``) is left exactly as the engine reported it — only an active
        workspace is downgraded to ``LOCKED`` by a lapsed plan, and a ban forces
        ``LOCKED`` regardless because it outranks every other state.
        """
        if ban_reason or ws.status is WorkspaceStatus.ACTIVE:
            return Workspace(
                id=ws.id,
                owner_tg_id=ws.owner_tg_id,
                name=ws.name,
                profile=ws.profile,
                status=WorkspaceStatus.LOCKED,
                limits=ws.limits,
                summary=ws.summary,
                created_at=ws.created_at,
                last_activity_at=ws.last_activity_at,
                ban_reason=ban_reason or ws.ban_reason,
            )
        return ws

    async def create(
        self,
        tg_id: int,
        name: str,
        *,
        plan_code: str,
        profile: str | None = None,
        limits: Limits | None = None,
    ) -> Workspace:
        """Create a workspace **with a plan attached**.

        The plan is mandatory: it is the source of the workspace's limits and its
        expiry. A trial is granted at once; a paid plan is charged from the wallet
        *before* the engine is called, so a short balance leaves no workspace
        behind. ``limits``, when given, only carries the wizard's network toggles;
        cpu/memory/disk always come from the plan.
        """
        if self._subscriptions is None:
            raise PlanRequired()
        try:
            plan = plans.plan(plan_code)
        except KeyError as exc:
            raise PlanRequired() from exc

        meta = await self._engine.meta()
        clean_name = self.validate_name(name)
        plan_limits = plan.to_limits(meta.limits_maxima)
        if limits is not None:
            plan_limits = Limits(
                cpu_cores=plan_limits.cpu_cores,
                memory_mb=plan_limits.memory_mb,
                disk_mb=plan_limits.disk_mb,
                max_wall_time_sec=plan_limits.max_wall_time_sec,
                max_runs=plan_limits.max_runs,
                allow_net=limits.allow_net and plan_limits.allow_net,
                allow_unlimited_runs=plan_limits.allow_unlimited_runs,
                allow_tunnels=limits.allow_tunnels and plan_limits.allow_tunnels,
                write_rate_mb_s=plan_limits.write_rate_mb_s,
            )
        self.validate_limits(plan_limits, meta.limits_maxima)

        # Never call the engine when the money is already known to be short.
        if plan.purchasable:
            wallet = await self._subscriptions.wallet(tg_id)
            if wallet.balance_stars < plan.amount_stars:
                raise InsufficientFunds(plan.amount_stars, wallet.balance_stars)

        selected_profile = profile or meta.default_profile
        ws = await self._engine.create_workspace(
            name=clean_name, profile=selected_profile, limits=plan_limits
        )
        try:
            await self._users.add_workspace(tg_id, ws.id)
        except Exception:
            # Compensate: delete the orphan we just created, then re-raise.
            log.error("workspace.registration_failed", ws_id=str(ws.id))
            try:
                await self._engine.delete_workspace(ws.id)
            except Exception:  # noqa: BLE001
                log.error("workspace.compensation_failed", ws_id=str(ws.id))
            raise

        try:
            if plan.code == "trial":
                subscription = await self._subscriptions.start_trial(tg_id, str(ws.id))
                if subscription is None or subscription.plan_code != "trial":
                    raise TrialUsed()
            else:
                await self._subscriptions.purchase(tg_id, str(ws.id), plan.code)
        except Exception:
            # The plan could not be attached (trial already used, or a concurrent
            # spend took the balance): do not leave a planless workspace behind.
            try:
                await self._engine.delete_workspace(ws.id)
                await self._users.remove_workspace(tg_id, ws.id)
            except Exception:  # noqa: BLE001
                log.error("workspace.plan_compensation_failed", ws_id=str(ws.id))
            raise
        return self._with_owner(ws, tg_id)

    async def get(self, tg_id: int, ws_id: WorkspaceId) -> Workspace:
        await self._ownership.assert_owned(tg_id, ws_id)
        try:
            ws = await self._engine.get_workspace(ws_id)
        except EngineRefused as exc:
            if exc.engine_code in ("not_found", "forbidden"):
                # Lazy reconcile: the engine no longer has it.
                await self._users.remove_workspace(tg_id, ws_id)
                raise
            raise
        result = self._with_owner(ws, tg_id)
        return (await self._with_subscription_state(tg_id, [result]))[0]

    async def patch(
        self, tg_id: int, ws_id: WorkspaceId, *, name: str | None = None, limits: Limits | None = None
    ) -> Workspace:
        await self._ownership.assert_owned(tg_id, ws_id)
        if name is not None:
            name = self.validate_name(name)
        if limits is not None:
            meta = await self._engine.meta()
            ceiling = await self.plan_maxima(tg_id, ws_id, meta.limits_maxima)
            self.validate_limits(limits, ceiling)
        ws = await self._engine.patch_workspace(ws_id, name=name, limits=limits)
        return self._with_owner(ws, tg_id)

    async def suspend(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.suspend_workspace(ws_id)

    async def resume(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        # A workspace ban is an admin decision: the owner can never resume past
        # it, and the refusal carries the admin's reason (not a raw engine code).
        if self._moderation is not None:
            await self._moderation.assert_executable(ws_id)
        return await self._engine.resume_workspace(ws_id)

    async def stop(self, tg_id: int, ws_id: WorkspaceId) -> LifecycleResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.stop_workspace(ws_id)

    async def delete(self, tg_id: int, ws_id: WorkspaceId) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._engine.delete_workspace(ws_id)
        await self._users.remove_workspace(tg_id, ws_id)

    async def usage(self, tg_id: int, ws_id: WorkspaceId) -> ResourceUsage:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.workspace_usage(ws_id)

    @staticmethod
    def _with_owner(ws: Workspace, tg_id: int) -> Workspace:
        if ws.owner_tg_id == tg_id:
            return ws
        return Workspace(
            id=ws.id,
            owner_tg_id=tg_id,
            name=ws.name,
            profile=ws.profile,
            status=ws.status,
            limits=ws.limits,
            summary=ws.summary,
            created_at=ws.created_at,
            last_activity_at=ws.last_activity_at,
            ban_reason=ws.ban_reason,
        )
