"""Composition root: the only place a concrete adapter is named.

Builds ``Settings`` → adapters → services, wiring ports to implementations.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.core.clock import SystemClock
from cloudbot.core.config import Settings
from cloudbot.core.modes import mock_engine_allowed
from cloudbot.domain.ports.engine import EnginePort
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


@dataclass
class Container:
    settings: Settings
    engine: EnginePort
    database: Database
    users: SqliteUserRepository
    sessions: SqliteSessionStore
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
    clock = SystemClock()
    ownership = OwnershipService(users)

    services = Services(
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
    return Container(
        settings=settings,
        engine=engine,
        database=database,
        users=users,
        sessions=sessions,
        services=services,
    )
