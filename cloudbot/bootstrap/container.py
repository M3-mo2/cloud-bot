"""Composition root: the only place a concrete adapter is named.

Builds ``Settings`` → adapters → services, wiring ports to implementations.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.backup_store import SqliteBackupStore
from cloudbot.adapters.persistence.broadcast_store import SqliteBroadcastStore
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.forcejoin_stats import ForceJoinStatsReader
from cloudbot.adapters.persistence.sqlite_events import (
    SqliteAdminStateStore,
    SqliteEventLog,
)
from cloudbot.adapters.persistence.sqlite_moderation import SqliteModerationStore
from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.adapters.store.sqlite_forcejoin import SqliteForceJoinStore
from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore
from cloudbot.adapters.store.sqlite_subscriptions import SqliteSubscriptions
from cloudbot.core.clock import SystemClock
from cloudbot.core.config import Settings
from cloudbot.core.modes import mock_engine_allowed
from cloudbot.core.runtime import BotRuntime
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.services.account import AccountService
from cloudbot.services.admin import AdminService
from cloudbot.services.archives import ArchivesService
from cloudbot.services.backup import BackupService
from cloudbot.services.billing import BillingService
from cloudbot.services.broadcast import BroadcastService
from cloudbot.services.bundle import Services
from cloudbot.services.events import EventsService
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.forcejoin import ForceJoinService
from cloudbot.services.metrics import MetricsService
from cloudbot.services.moderation import ModerationService
from cloudbot.services.notifier import NotifierService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.packages import PackagesService
from cloudbot.services.ports import PortsService
from cloudbot.services.processes import ProcessesService
from cloudbot.services.session import SessionService
from cloudbot.services.sessions import SessionsService
from cloudbot.services.subscription import SubscriptionService
from cloudbot.services.tunnels import TunnelsService
from cloudbot.services.workspaces import WorkspacesService


class _NoDirectory:
    """The pre-bot placeholder: every read is "unknown" (the gate allows through)."""

    async def resolve_chat(self, reference: str) -> dict:  # pragma: no cover - never used
        from cloudbot.domain.forcejoin import ChatResolutionError

        raise ChatResolutionError("api_unavailable", "bot not wired")

    async def is_admin_in(self, chat_id: int, bot_id: int) -> bool:  # pragma: no cover
        return False

    async def membership(self, chat_id: int, user_id: int):  # pragma: no cover
        from cloudbot.domain.forcejoin import MembershipResult

        return MembershipResult.unknown_for(chat_id, "bot_not_wired")

_NO_DIRECTORY = _NoDirectory()

@dataclass
class Container:
    settings: Settings
    engine: EnginePort
    database: Database
    users: SqliteUserRepository
    sessions: SqliteSessionStore
    event_log: SqliteEventLog
    admin_state: SqliteAdminStateStore
    subscriptions: SqliteSubscriptions
    forcejoin_store: SqliteForceJoinStore
    forcejoin_events: SqliteForceJoinEventStore
    backup_store: SqliteBackupStore
    moderation_store: SqliteModerationStore
    broadcast_store: SqliteBroadcastStore
    services: Services

    async def aclose(self) -> None:
        await self.engine.aclose()
        await self.database.close()


def build(settings: Settings) -> Container:
    use_fake = settings.mock_engine and mock_engine_allowed(settings.mode)
    if use_fake:
        engine: EnginePort = FakeEngine()
    else:
        engine = EngineAdapter.create(
            settings.engine_base_url,
            settings.engine_api_key.get_secret_value(),
            timeout_s=settings.engine_timeout_s,
            max_retries=settings.engine_max_retries,
        )

    database = Database(settings.database_url)
    users = SqliteUserRepository(database)
    sessions = SqliteSessionStore(database)
    event_log = SqliteEventLog(database)
    admin_state = SqliteAdminStateStore(database)
    subscriptions = SqliteSubscriptions(database)
    forcejoin_store = SqliteForceJoinStore(database)
    forcejoin_events = SqliteForceJoinEventStore(database)
    backup_store = SqliteBackupStore(database)
    moderation_store = SqliteModerationStore(database)
    broadcast_store = SqliteBroadcastStore(database)
    clock = SystemClock()
    ownership = OwnershipService(users)
    moderation = ModerationService(moderation_store, protected_ids=settings.admin_ids)
    # The paid-plan gate: one seam every work service calls, and the actor's
    # Telegram id is exempt when it is in the operator allowlist (anti-lockout).
    subscription = SubscriptionService(subscriptions, admin_ids=settings.admin_ids)

    services = Services(
        account=AccountService(users),
        billing=BillingService(subscriptions, engine=engine, subscription=subscription),
        backup=BackupService(backup_store),
        workspaces=WorkspacesService(
            engine, users, ownership, subscriptions, moderation, subscription
        ),
        session=SessionService(users, engine, ownership),
        files=FilesService(engine, ownership, subscription=subscription),
        execution=ExecutionService(engine, ownership, moderation, subscription=subscription),
        sessions=SessionsService(
            engine, sessions, ownership, moderation, subscription=subscription
        ),
        ownership=ownership,
        packages=PackagesService(engine, ownership, subscription=subscription),
        tunnels=TunnelsService(engine, ownership, subscription=subscription),
        ports=PortsService(engine, ownership),
        metrics=MetricsService(engine, ownership),
        processes=ProcessesService(engine, ownership, subscription=subscription),
        archives=ArchivesService(engine, ownership, subscription=subscription),
        events=EventsService(event_log),
        admin=AdminService(
            users, engine, event_log, admin_state, ownership, BotRuntime(),
            subscriptions=subscriptions, moderation=moderation,
            subscription=subscription,
        ),
        notifier=NotifierService(event_log, admin_state),
        broadcast=BroadcastService(broadcast_store),
        # The directory is wired with the real Bot in ``build_dispatcher``; this
        # placeholder keeps the service constructible (and keeps the gate's
        # "unknown ⇒ allow" path the safe default) until then.
        forcejoin=ForceJoinService(
            forcejoin_store,
            _NO_DIRECTORY,
            events=forcejoin_events,
            stats=ForceJoinStatsReader(database),
        ),
        moderation=moderation,
    )
    return Container(
        settings=settings,
        engine=engine,
        database=database,
        users=users,
        sessions=sessions,
        event_log=event_log,
        admin_state=admin_state,
        subscriptions=subscriptions,
        forcejoin_store=forcejoin_store,
        forcejoin_events=forcejoin_events,
        backup_store=backup_store,
        moderation_store=moderation_store,
        broadcast_store=broadcast_store,
        services=services,
    )
