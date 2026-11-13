"""Shared test fixtures: in-memory DB, fake engine, frozen clock, services."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def _isolate_backup_dir(tmp_path, monkeypatch):
    """Never let a test write an artifact into the repository's ``backups/``.

    A backup artifact is a snapshot of whatever database the test built; leaving
    one behind looks exactly like a real backup and (measured) four stale SQLite
    archives ended up in the directory the scheduler reads. ``CLOUDBOT_BACKUP_DIR``
    is the one switch the adapter honours, so pointing it at a per-test temp
    directory keeps the production directory untouched.
    """
    monkeypatch.setenv("CLOUDBOT_BACKUP_DIR", str(tmp_path / "backups"))

@pytest.fixture(autouse=True)
def _reset_global_routers():
    """Handler routers are process-global modules; detach them before each test so
    a second ``build_dispatcher`` does not trip aiogram's 'already attached' guard.
    Test scaffolding only — production builds the dispatcher once."""
    from cloudbot.handlers import (
        admin,
        admin_panel,
        billing,
        common,
        dev,
        execution,
        features,
        files,
        forcejoin,
        templates,
        terminal,
        workspaces,
    )
    from cloudbot.handlers.callbacks import navigation

    for module in (
        common,
        billing,
        navigation,
        admin_panel,
        workspaces,
        files,
        templates,
        execution,
        features,
        forcejoin,
        admin,
        dev,
        terminal,
    ):
        module.router._parent_router = None
    yield

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_events import (
    SqliteAdminStateStore,
    SqliteEventLog,
)
from cloudbot.adapters.persistence.sqlite_moderation import SqliteModerationStore
from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.adapters.persistence.backup_store import SqliteBackupStore
from cloudbot.adapters.persistence.broadcast_store import SqliteBroadcastStore
from cloudbot.adapters.store.sqlite_forcejoin import SqliteForceJoinStore
from cloudbot.adapters.store.sqlite_subscriptions import SqliteSubscriptions
from cloudbot.core.locale import Locale
from cloudbot.core.runtime import BotRuntime
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
from cloudbot.services.tunnels import TunnelsService
from cloudbot.services.workspaces import WorkspacesService

FROZEN_NOW = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)


@pytest.fixture
async def database():
    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    yield db
    await db.close()


@pytest.fixture
async def users(database):
    return SqliteUserRepository(database)


@pytest.fixture
async def sessions(database):
    return SqliteSessionStore(database)


@pytest.fixture
async def event_log(database):
    return SqliteEventLog(database)


@pytest.fixture
async def admin_state(database):
    return SqliteAdminStateStore(database)


@pytest.fixture
async def subscriptions(database):
    return SqliteSubscriptions(database)

#: The admin the fixtures act as. Production passes ``settings.admin_ids`` here;
#: a fixture that left it empty made **every** admin action raise
#: ``SubscriptionExpired``, because ``assert_usable`` exempts only ids it was
#: told about. Tests that drove the panel as the operator then failed on a gate
#: that was working correctly.
ADMIN_TG_ID = 6508129575


@pytest.fixture
async def subscription_gate(subscriptions):
    """The service-layer plan gate, wired exactly as production does."""
    from cloudbot.services.subscription import SubscriptionService

    return SubscriptionService(subscriptions, admin_ids=[ADMIN_TG_ID])

@pytest.fixture
def engine():
    return FakeEngine()


class FakeDirectory:
    """A configurable Telegram directory for tests: memberships by chat, admin by chat."""

    def __init__(self) -> None:
        self.memberships: dict[int, object] = {}
        self.admins: dict[int, bool] = {}
        self.chats: dict[str, dict] = {}

    async def resolve_chat(self, reference: str) -> dict:
        from cloudbot.domain.forcejoin import ChatResolutionError

        info = self.chats.get(reference)
        if info is None:
            raise ChatResolutionError("not_found", reference)
        return info

    async def is_admin_in(self, chat_id: int, bot_id: int) -> bool:
        return self.admins.get(chat_id, False)

    async def membership(self, chat_id: int, user_id: int):
        from cloudbot.domain.forcejoin import MembershipResult

        result = self.memberships.get(chat_id)
        if result is None:
            return MembershipResult.unknown_for(chat_id, "no_fixture")
        if callable(result):
            return result(chat_id, user_id)
        return result

@pytest.fixture
def fake_directory():
    return FakeDirectory()

@pytest.fixture
async def forcejoin_store(database):
    return SqliteForceJoinStore(database)

@pytest.fixture
async def moderation_store(database):
    return SqliteModerationStore(database)

@pytest.fixture
async def moderation(moderation_store):
    return ModerationService(moderation_store)

@pytest.fixture
async def services(
    engine, database, users, sessions, event_log, admin_state, subscriptions,
    fake_directory, moderation, subscription_gate,
):
    ownership = OwnershipService(users)
    return Services(
        account=AccountService(users),
        billing=BillingService(subscriptions, engine=engine, subscription=subscription_gate),
        backup=BackupService(SqliteBackupStore(database)),
        workspaces=WorkspacesService(
            engine, users, ownership, subscriptions, moderation, subscription_gate
        ),
        session=SessionService(users, engine, ownership),
        files=FilesService(engine, ownership, subscription=subscription_gate),
        execution=ExecutionService(
            engine, ownership, moderation, subscription=subscription_gate
        ),
        sessions=SessionsService(
            engine, sessions, ownership, moderation, subscription=subscription_gate
        ),
        ownership=ownership,
        packages=PackagesService(engine, ownership, subscription=subscription_gate),
        tunnels=TunnelsService(engine, ownership, subscription=subscription_gate),
        ports=PortsService(engine, ownership),
        metrics=MetricsService(engine, ownership),
        processes=ProcessesService(engine, ownership, subscription=subscription_gate),
        archives=ArchivesService(engine, ownership, subscription=subscription_gate),
        events=EventsService(event_log),
        admin=AdminService(
            users, engine, event_log, admin_state, ownership, BotRuntime(FROZEN_NOW),
            subscriptions=subscriptions, moderation=moderation,
            subscription=subscription_gate,
        ),
        notifier=NotifierService(event_log, admin_state),
        broadcast=BroadcastService(SqliteBroadcastStore(database)),
        forcejoin=ForceJoinService(
            SqliteForceJoinStore(database), fake_directory, bot_id=42
        ),
        moderation=moderation,
    )


@pytest.fixture
def en():
    return Locale.EN


@pytest.fixture
def ar():
    return Locale.AR
