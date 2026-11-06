"""Shared test fixtures: in-memory DB, fake engine, frozen clock, services."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


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
        execution,
        features,
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
from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.adapters.store.sqlite_subscriptions import SqliteSubscriptions
from cloudbot.core.locale import Locale
from cloudbot.core.runtime import BotRuntime
from cloudbot.services.account import AccountService
from cloudbot.services.admin import AdminService
from cloudbot.services.archives import ArchivesService
from cloudbot.services.billing import BillingService
from cloudbot.services.bundle import Services
from cloudbot.services.events import EventsService
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.metrics import MetricsService
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

@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
async def services(engine, users, sessions, event_log, admin_state, subscriptions):
    ownership = OwnershipService(users)
    return Services(
        account=AccountService(users),
        billing=BillingService(subscriptions),
        workspaces=WorkspacesService(engine, users, ownership, subscriptions),
        session=SessionService(users, engine, ownership),
        files=FilesService(engine, ownership),
        execution=ExecutionService(engine, ownership),
        sessions=SessionsService(engine, sessions, ownership),
        ownership=ownership,
        packages=PackagesService(engine, ownership),
        tunnels=TunnelsService(engine, ownership),
        ports=PortsService(engine, ownership),
        metrics=MetricsService(engine, ownership),
        processes=ProcessesService(engine, ownership),
        archives=ArchivesService(engine, ownership),
        events=EventsService(event_log),
        admin=AdminService(
            users, engine, event_log, admin_state, ownership, BotRuntime(FROZEN_NOW),
            subscriptions=subscriptions,
        ),
        notifier=NotifierService(event_log, admin_state),
    )


@pytest.fixture
def en():
    return Locale.EN


@pytest.fixture
def ar():
    return Locale.AR
