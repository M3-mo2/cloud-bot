"""D1: the admin migration applies to an EXISTING database (task §6 proof 4)."""

from __future__ import annotations

import aiosqlite
import pytest

from cloudbot.adapters.persistence.database import (
    MIGRATIONS_DIR,
    Database,
    discover_migrations,
    parse_sqlite_path,
)
from cloudbot.core.locale import Locale


def test_a_0002_migration_file_exists():
    versions = [v for v, _ in discover_migrations()]
    assert 2 in versions
    # Forward-only: 0001 is untouched.
    assert (MIGRATIONS_DIR / "0001_init.sql").exists()
    assert (MIGRATIONS_DIR / "0002_admin.sql").exists()


async def test_0001_then_0002_preserves_existing_rows(tmp_path):
    """Apply only 0001, insert a user and a workspace, then apply 0002 and assert
    the old rows survive and the new tables/columns exist."""
    path = str(tmp_path / "existing.db")

    # 1. A database that already holds Phase-1 data, migrated to 0001 only.
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        script = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
        await conn.executescript(script)
        await conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, datetime('now'))"
        )
        await conn.execute(
            "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at) "
            "VALUES (6508129575, 'en', 'free', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO user_workspaces (tg_id, ws_id, created_at) "
            "VALUES (6508129575, 'ws_OLDDATA00000000000000000000', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()

    # 2. Point the app's Database at it and migrate (applies every later file).
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.connect()
    applied = await db.migrate()
    # The exact tail grows as migrations are added (0005 = subscriptions);
    # this asserts "nothing before 0002 was re-applied and nothing was skipped".
    assert applied == [2, 3, 4, 5]

    conn = await db.connect()
    async with conn.execute("SELECT tg_id FROM users") as cur:
        rows = await cur.fetchall()
    assert [int(r["tg_id"]) for r in rows] == [6508129575]
    async with conn.execute("SELECT ws_id FROM user_workspaces") as cur:
        rows = await cur.fetchall()
    assert [str(r["ws_id"]) for r in rows] == ["ws_OLDDATA00000000000000000000"]
    # display_name column exists with the default.
    async with conn.execute("SELECT display_name FROM users") as cur:
        assert (await cur.fetchone())["display_name"] == ""

    # 3. New tables exist.
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        names = {r["name"] for r in await cur.fetchall()}
    for table in ("events", "event_subscriptions", "admin_notify_state", "admin_mode"):
        assert table in names

    # Indexes the task requires exist.
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ) as cur:
        indexes = {r["name"] for r in await cur.fetchall()}
    assert "idx_events_ts" in indexes
    assert "idx_events_tg" in indexes

    # 4. Idempotent: a second migrate does nothing.
    assert await db.migrate() == []
    await db.close()


async def test_wrong_prefix_dsn_is_accepted(tmp_path):
    # A bare path is a valid DSN (parse helper).
    path = str(tmp_path / "x.db")
    assert parse_sqlite_path(path) == path
