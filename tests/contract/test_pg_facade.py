"""The Postgres facade, proved against a real server (skipped without ``TEST_PG_DSN``).

Set ``TEST_PG_DSN`` to a ``postgresql://`` URL to run this file; without it every
test skips with that reason, so the default (offline, SQLite) suite never needs a
server and never silently reaches the network. Each test runs in its own
throw-away schema, so the real database is never touched: the fixture creates
``cloudbot_test_<hex>``, the ``Database`` is pointed at it with ``search_path``,
and the schema is dropped afterwards. The DSN is never printed.

The point of this file is that ``tests/unit/test_pg_translation.py`` proves the
text translation offline while this one proves the *execution*: real ``asyncpg``
rows, real status tags, real ``lastval()``, real transactions.

Run it against the instance once; it is not part of the default suite (the
fixture skips when no DSN is available), so the suite stays 1899/7 offline.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("asyncpg")

from cloudbot.adapters.persistence.database import (  # noqa: E402
    Database,
    discover_migrations,
    pg_dsn,
)


RAW_DSN = os.environ.get("TEST_PG_DSN", "").strip()
pytestmark = pytest.mark.skipif(
    not RAW_DSN,
    reason="set TEST_PG_DSN to a postgresql:// URL to run the Postgres facade tests",
)


def _dsn() -> str:
    # Strip the libpq-only parameter asyncpg rejects; never echo the value.
    return pg_dsn(RAW_DSN)


@pytest.fixture
async def schema():
    import asyncpg

    name = f"cloudbot_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn())
    await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        await admin.execute(f'DROP SCHEMA "{name}" CASCADE')
        await admin.close()


@pytest.fixture
async def database(schema):
    db = Database(_dsn(), search_path=schema)
    await db.connect()
    await db.migrate()
    yield db
    await db.close()


@pytest.fixture
async def conn(database):
    return await database.connect()


# -- the backend is picked from the DSN --------------------------------------
async def test_database_reports_postgres_and_has_no_path(database):
    assert database.is_postgres is True
    assert database.path == ""
    assert database.migrations_dir.name == "migrations_pg"


# -- migrations ---------------------------------------------------------------
async def test_fresh_database_gets_all_thirteen_migrations(schema):
    db = Database(_dsn(), search_path=schema)
    try:
        await db.connect()
        applied = await db.migrate()
        expected = [version for version, _ in discover_migrations(db.migrations_dir)]
        assert applied == expected
        assert len(applied) == 13
        assert await db.migrate() == []

        connection = await db.connect()
        async with connection.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ) as cur:
            rows = await cur.fetchall()
        assert [int(row["version"]) for row in rows] == list(range(1, 14))
        # applied_at is RFC3339 UTC text, not a datetime object.
        assert isinstance(rows[0]["applied_at"], str)
        assert rows[0]["applied_at"].endswith("Z")
    finally:
        await db.close()


async def test_migrated_schema_holds_the_expected_tables(conn):
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ) as cur:
        names = {str(row["name"]) for row in await cur.fetchall()}
    for table in (
        "users",
        "user_workspaces",
        "terminal_sessions",
        "events",
        "subscriptions",
        "wallet_ledger",
        "broadcast_targets",
        "schema_migrations",
    ):
        assert table in names


# -- the cursor contract -----------------------------------------------------
async def test_async_with_and_plain_await_both_yield_a_usable_cursor(conn):
    await conn.execute("CREATE TABLE t_cursor (id INTEGER PRIMARY KEY, name TEXT)")
    await conn.execute("INSERT INTO t_cursor (id, name) VALUES (?, ?)", (1, "a"))

    async with conn.execute("SELECT name FROM t_cursor WHERE id = ?", (1,)) as cur:
        row = await cur.fetchone()
    assert dict(row) == {"name": "a"}

    cursor = await conn.execute("SELECT name FROM t_cursor WHERE id = ?", (1,))
    assert dict(await cursor.fetchone()) == {"name": "a"}
    assert await cursor.fetchone() is None


async def test_fetchone_exhausts_and_fetchall_returns_the_rest(conn):
    await conn.execute("CREATE TABLE t_rows (id INTEGER PRIMARY KEY, name TEXT)")
    for index in range(3):
        await conn.execute("INSERT INTO t_rows (id, name) VALUES (?, ?)", (index, f"n{index}"))
    async with conn.execute("SELECT * FROM t_rows ORDER BY id") as cur:
        first = await cur.fetchone()
        rest = await cur.fetchall()
    assert first["name"] == "n0"
    assert [row["name"] for row in rest] == ["n1", "n2"]
    assert await cur.fetchall() == []


async def test_rows_are_dict_like_by_name_and_position(conn):
    async with conn.execute("SELECT 7 AS seven, 'x' AS letter") as cur:
        row = await cur.fetchone()
    assert row["seven"] == 7
    assert row[0] == 7
    with pytest.raises(KeyError):
        _ = row["nope"]
    assert dict(row) == {"seven": 7, "letter": "x"}


# -- placeholder translation, executed ---------------------------------------
async def test_question_mark_in_a_literal_is_not_a_placeholder(conn):
    await conn.execute("CREATE TABLE t_lit (id INTEGER PRIMARY KEY, note TEXT)")
    await conn.execute("INSERT INTO t_lit (id, note) VALUES (?, ?)", (1, "what?"))
    async with conn.execute(
        "SELECT id FROM t_lit WHERE note = ? AND 'is ? here' <> ''", ("what?",)
    ) as cur:
        assert (await cur.fetchone())["id"] == 1


async def test_param_count_mismatch_names_the_sql(conn):
    from cloudbot.adapters.persistence.pg_connection import PgTranslationError

    with pytest.raises(PgTranslationError) as excinfo:
        await conn.execute("SELECT ? AS a, ? AS b", (1,))
    assert "$1" in str(excinfo.value)
    assert "$2" in str(excinfo.value)


# -- INSERT OR IGNORE / REPLACE ----------------------------------------------
async def test_insert_or_ignore_second_insert_changes_nothing(conn):
    await conn.execute(
        "CREATE TABLE t_ignore (id INTEGER PRIMARY KEY, name TEXT UNIQUE)"
    )
    first = await conn.execute(
        "INSERT OR IGNORE INTO t_ignore (id, name) VALUES (?, ?)", (1, "a")
    )
    assert first.rowcount == 1
    second = await conn.execute(
        "INSERT OR IGNORE INTO t_ignore (id, name) VALUES (?, ?)", (1, "a")
    )
    assert second.rowcount == 0
    async with conn.execute("SELECT COUNT(*) AS n FROM t_ignore") as cur:
        assert (await cur.fetchone())["n"] == 1


async def test_insert_or_replace_updates_instead_of_duplicating(conn):
    await conn.execute(
        "CREATE TABLE t_replace (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    await conn.execute(
        "INSERT OR REPLACE INTO t_replace (key, value) VALUES (?, ?)", ("k", "old")
    )
    await conn.execute(
        "INSERT OR REPLACE INTO t_replace (key, value) VALUES (?, ?)", ("k", "new")
    )
    async with conn.execute("SELECT value FROM t_replace") as cur:
        rows = await cur.fetchall()
    assert [row["value"] for row in rows] == ["new"]


# -- rowcount -----------------------------------------------------------------
async def test_rowcount_for_insert_update_delete(conn):
    await conn.execute("CREATE TABLE t_count (id INTEGER PRIMARY KEY, n INTEGER)")
    inserted = await conn.execute(
        "INSERT INTO t_count (id, n) VALUES (?, ?), (?, ?)", (1, 10, 2, 20)
    )
    assert inserted.rowcount == 2
    updated = await conn.execute("UPDATE t_count SET n = ? WHERE id = ?", (11, 1))
    assert updated.rowcount == 1
    missed = await conn.execute("UPDATE t_count SET n = ? WHERE id = ?", (99, 7))
    assert missed.rowcount == 0
    deleted = await conn.execute("DELETE FROM t_count WHERE id = ?", (2,))
    assert deleted.rowcount == 1


async def test_lastrowid_is_the_new_row_id(conn):
    async with conn.execute(
        "INSERT INTO events (ts, category, severity, summary_key, params, actor) "
        "VALUES (?, 'system', 'info', 'k', '{}', 'system')",
        ("2026-11-10T00:00:00Z",),
    ) as cursor:
        new_id = cursor.lastrowid
    assert new_id > 0
    async with conn.execute("SELECT MAX(id) AS n FROM events") as cur:
        assert (await cur.fetchone())["n"] == new_id


# -- datetime path ------------------------------------------------------------
async def test_timestamp_text_round_trips_as_text(conn):
    async with conn.execute("SELECT datetime('now') AS now_text") as cur:
        value = (await cur.fetchone())["now_text"]
    assert isinstance(value, str)
    assert value.endswith("Z")
    from cloudbot.adapters.persistence.models import parse_dt

    parsed = parse_dt(value)
    assert parsed.tzinfo is not None

    await conn.execute(
        "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at) "
        "VALUES (?, 'en', 'free', ?, ?)",
        (5, "2026-11-09T10:00:00Z", "2026-11-09T10:01:00Z"),
    )
    async with conn.execute("SELECT created_at FROM users WHERE tg_id = ?", (5,)) as cur:
        stored = (await cur.fetchone())["created_at"]
    assert stored == "2026-11-09T10:00:00Z"
    assert isinstance(stored, str)


async def test_date_now_is_text(conn):
    async with conn.execute("SELECT date('now') AS d") as cur:
        value = (await cur.fetchone())["d"]
    assert isinstance(value, str)
    assert len(value) == 10


# -- transactions -------------------------------------------------------------
async def test_begin_rollback_leaves_nothing(conn):
    await conn.execute("CREATE TABLE t_tx (id INTEGER PRIMARY KEY)")
    await conn.execute("BEGIN IMMEDIATE")
    await conn.execute("INSERT INTO t_tx (id) VALUES (?)", (1,))
    await conn.execute("ROLLBACK")
    async with conn.execute("SELECT COUNT(*) AS n FROM t_tx") as cur:
        assert (await cur.fetchone())["n"] == 0


async def test_begin_commit_persists(conn):
    await conn.execute("CREATE TABLE t_tx2 (id INTEGER PRIMARY KEY)")
    await conn.execute("BEGIN IMMEDIATE")
    await conn.execute("INSERT INTO t_tx2 (id) VALUES (?)", (1,))
    await conn.execute("COMMIT")
    async with conn.execute("SELECT COUNT(*) AS n FROM t_tx2") as cur:
        assert (await cur.fetchone())["n"] == 1


async def test_commit_without_begin_is_a_noop_in_autocommit(conn):
    await conn.execute("CREATE TABLE t_tx3 (id INTEGER PRIMARY KEY)")
    await conn.execute("INSERT INTO t_tx3 (id) VALUES (?)", (1,))
    await conn.commit()
    async with conn.execute("SELECT COUNT(*) AS n FROM t_tx3") as cur:
        assert (await cur.fetchone())["n"] == 1


async def test_statement_inside_a_transaction_sees_its_own_write(conn):
    await conn.execute("CREATE TABLE t_tx4 (id INTEGER PRIMARY KEY, name TEXT)")
    await conn.execute("BEGIN IMMEDIATE")
    await conn.execute("INSERT INTO t_tx4 (id, name) VALUES (?, ?)", (1, "a"))
    async with conn.execute("SELECT COUNT(*) AS n FROM t_tx4") as cur:
        assert (await cur.fetchone())["n"] == 1
    await conn.execute("ROLLBACK")


# -- executemany / executescript ---------------------------------------------
async def test_executemany_and_executescript(conn):
    await conn.executescript(
        "CREATE TABLE t_many (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE INDEX idx_t_many_name ON t_many(name);"
    )
    await conn.executemany(
        "INSERT INTO t_many (id, name) VALUES (?, ?)",
        [(1, "a"), (2, "b")],
    )
    async with conn.execute("SELECT COUNT(*) AS n FROM t_many") as cur:
        assert (await cur.fetchone())["n"] == 2


# -- the stores run unchanged on the facade ----------------------------------
async def test_the_real_stores_work_against_postgres(database):
    from cloudbot.adapters.persistence.models import utc_now_iso
    from cloudbot.adapters.persistence.sqlite_events import SqliteEventLog
    from cloudbot.adapters.persistence.sqlite_sessions import SqliteSessionStore
    from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
    from cloudbot.adapters.store.sqlite_forcejoin import SqliteForceJoinStore
    from cloudbot.core.locale import Locale
    from cloudbot.domain.events import EventCategory, NewEvent  # noqa: F401
    from cloudbot.domain.value_objects import SessionId, WorkspaceId

    users = SqliteUserRepository(database)
    user = await users.get_or_create(42, default_locale=Locale.EN)
    assert user.tg_id == 42
    workspace = WorkspaceId("ws_pg")
    await users.add_workspace(42, workspace)
    assert await users.owns(42, workspace)
    assert await users.owned_workspaces(42) == [workspace]

    sessions = SqliteSessionStore(database)
    session = SessionId("ses_pg")
    await sessions.create(42, workspace, session, cols=100, rows=30)
    stored = await sessions.get(42, session)
    assert stored is not None and stored.cols == 100
    await sessions.set_offset(42, session, 9)
    assert (await sessions.get(42, session)).out_offset == 9
    await sessions.delete(42, session)
    assert await sessions.get(42, session) is None

    log = SqliteEventLog(database)
    event_id = await log.append(
        NewEvent(
            category=EventCategory.USER_NEW,
            summary_key="k",
            params={"a": 1},
            actor="system",
        )
    )
    assert event_id > 0
    assert await log.latest_id() == event_id
    records, total = await log.query()
    assert total == 1 and records[0].id == event_id

    store = SqliteForceJoinStore(database)
    await store.set_flag("forcejoin.enabled", False)
    assert await store.flag("forcejoin.enabled", True) is False


async def test_the_subscription_transaction_is_atomic(database):
    from cloudbot.adapters.store.sqlite_subscriptions import SqliteSubscriptions

    store = SqliteSubscriptions(database)
    wallet = await store.credit(7, 100, "seed", 7)
    assert wallet.balance_stars == 100
    wallet = await store.debit(7, 40, "spend", 7)
    assert wallet.balance_stars == 60
    # A rolled-back purchase leaves no ledger row (the failure path inside the
    # BEGIN IMMEDIATE transaction).
    from cloudbot.core.errors import InsufficientFunds

    with pytest.raises(InsufficientFunds):
        await store.debit(7, 1000, "too much", 7)
    assert (await store.wallet(7)).balance_stars == 60


# -- the import script --------------------------------------------------------
async def test_import_script_is_rerunnable_and_counts_match(schema, tmp_path):
    from scripts.import_sqlite_to_pg import copy, describe_source, load_source

    source = Path("data/bot.db")
    if not source.exists():
        pytest.skip("data/bot.db is not present in this checkout")
    expected = {name: len(rows) for name, rows in load_source(source).items()}
    assert describe_source(source)

    first = await copy(source, _dsn(), schema=schema)
    assert {name: target for name, (_, target) in first.items()} == expected

    second = await copy(source, _dsn(), schema=schema)
    assert {name: target for name, (_, target) in second.items()} == expected
