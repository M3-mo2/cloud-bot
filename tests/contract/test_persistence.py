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
    # Updated when 0011_broadcast was added: forward-only, every file applies once.
    assert applied == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
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


async def test_concurrent_get_or_create_does_not_nest_transactions():
    """The bot processes updates concurrently on one shared connection. An
    explicit ``BEGIN`` in ``get_or_create`` raised "cannot start a transaction
    within a transaction" when two reads overlapped. The connection is autocommit
    and the upsert is idempotent, so concurrent first messages are race-safe."""
    import asyncio

    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)

    users = await asyncio.gather(
        *[repo.get_or_create(i, default_locale=Locale.EN) for i in range(1, 40)]
    )
    assert {u.tg_id for u in users} == set(range(1, 40))

    # Concurrent first messages from the *same* new user must not duplicate or crash.
    same = await asyncio.gather(
        *[repo.get_or_create(999, default_locale=Locale.EN) for _ in range(25)]
    )
    assert {u.tg_id for u in same} == {999}
    await db.close()


async def test_concurrent_mixed_writes_do_not_nest_transactions():
    """A write racing a write (both multi-statement) must also survive on the
    shared autocommit connection."""
    import asyncio

    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)
    ws = WorkspaceId("ws_abc")

    async def register(i: int) -> None:
        await repo.get_or_create(i, default_locale=Locale.EN)
        await repo.add_workspace(i, ws)

    await asyncio.gather(*[register(i) for i in range(1, 40)])
    for i in range(1, 40):
        assert await repo.owns(i, ws)
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


async def test_session_store_roundtrip():
    from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
    from cloudbot.domain.value_objects import SessionId, SessionStatus

    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)
    store = SqliteSessionStore(db)
    await repo.get_or_create(7, default_locale=Locale.EN)
    ws = WorkspaceId("ws_abc")
    ses = SessionId("ses_abc")
    await store.create(7, ws, ses, cols=100, rows=30)

    handle = await store.get(7, ses)
    assert handle is not None
    assert handle.workspace_id == ws
    assert handle.cols == 100 and handle.rows == 30
    assert handle.out_offset == 0
    # The store carries an unknown status; the engine owns the live status.
    assert handle.status is SessionStatus.UNKNOWN

    await store.set_offset(7, ses, 42)
    await store.update_size(7, ses, cols=120, rows=40)
    handle = await store.get(7, ses)
    assert handle.out_offset == 42
    assert handle.cols == 120 and handle.rows == 40

    listed = await store.list_for_workspace(7, ws)
    assert [h.id for h in listed] == ["ses_abc"]
    assert [h.id for h in await store.list_for_user(7)] == ["ses_abc"]

    await store.delete(7, ses)
    assert await store.get(7, ses) is None
    await db.close()

