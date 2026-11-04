"""Shared test fixtures: in-memory DB, fake engine, frozen clock, services."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.core.locale import Locale
from cloudbot.services.account import AccountService
from cloudbot.services.archives import ArchivesService
from cloudbot.services.bundle import Services
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.metrics import MetricsService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.packages import PackagesService
from cloudbot.services.ports import PortsService
from cloudbot.services.processes import ProcessesService
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
def engine():
    return FakeEngine()


@pytest.fixture
async def services(engine, users, sessions):
    ownership = OwnershipService(users)
    return Services(
        account=AccountService(users),
        workspaces=WorkspacesService(engine, users, ownership),
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
    )


@pytest.fixture
def en():
    return Locale.EN


@pytest.fixture
def ar():
    return Locale.AR
