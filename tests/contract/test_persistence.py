"""Persistence contract: migrations, schema, repository behaviour."""

from __future__ import annotations

import pytest

from cloudbot.adapters.persistence.database import Database, parse_sqlite_path
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId


async def test_migrations_apply_and_are_idempotent():
    db = Database(":memory:")
    await db.connect()
    applied = await db.migrate()
    assert applied == [1]
    again = await db.migrate()
    assert again == []  # forward-only, idempotent
    await db.close()


async def test_schema_tables_exist():
    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    conn = await db.connect()
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        names = {row["name"] for row in await cur.fetchall()}
    for table in ("users", "user_workspaces", "terminal_sessions", "schema_migrations"):
        assert table in names
    await db.close()


async def test_get_or_create_single_transaction():
    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)
    a = await repo.get_or_create(42, default_locale=Locale.EN)
    b = await repo.get_or_create(42, default_locale=Locale.AR)
    assert a.tg_id == b.tg_id
    assert b.locale is Locale.EN
    await db.close()


async def test_ownership_registry():
    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)
    await repo.get_or_create(1, default_locale=Locale.EN)
    ws = WorkspaceId("ws_abc")
    assert not await repo.owns(1, ws)
    await repo.add_workspace(1, ws)
    assert await repo.owns(1, ws)
    assert await repo.owned_workspaces(1) == [ws]
    await repo.remove_workspace(1, ws)
    assert not await repo.owns(1, ws)
    await db.close()


def test_dsn_parsing():
    assert parse_sqlite_path("sqlite+aiosqlite:///./data/bot.db") == "./data/bot.db"
    assert parse_sqlite_path("sqlite+aiosqlite:///:memory:") == ":memory:"
