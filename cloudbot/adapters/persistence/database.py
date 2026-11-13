"""Database facade: SQLite or Postgres chosen from the DSN, one surface either way.

Migrations are forward-only, idempotent, and fail-fast: the bot refuses to start
if a migration fails (never run on a half-migrated DB).

Backends
--------
* ``sqlite+aiosqlite:///./data/bot.db`` (the default, unchanged) and ``:memory:``
  — one ``aiosqlite`` connection for the process lifetime, opened in autocommit,
  exactly as before.
* ``postgresql://…`` / ``postgres://…`` — a small ``asyncpg`` pool wrapped by
  :class:`~cloudbot.adapters.persistence.pg_connection.PgConnection` so the
  stores keep their ``aiosqlite``-shaped call sites untouched. The pool is
  ``min_size=2``/``max_size=5``: the bot is a single process, but aiogram handles
  updates concurrently, so a second connection lets a read proceed while a write
  is in flight; five is comfortably under Neon's pooler cap (112) and leaves room
  for a restore/import task running beside the bot without exhausting it.

The DSN's ``channel_binding`` parameter (present in Neon URIs) is stripped before
``asyncpg`` sees it: asyncpg does not understand it and would reject the
connection. ``sslmode`` is left to ``asyncpg``'s default (SSL is negotiated when
the server advertises it; Neon does).

Migrations live beside this module: ``migrations/`` for SQLite and
``migrations_pg/`` for Postgres, with identical version numbers.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from cloudbot.adapters.persistence.pg_connection import PgConnection

log = structlog.get_logger("cloudbot.persistence")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATIONS_PG_DIR = Path(__file__).parent / "migrations_pg"
_MIGRATION_RE = re.compile(r"^(\d+)_")
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PG_PREFIXES = ("postgresql://", "postgresql+asyncpg://", "postgres://")

#: Public alias for tests and tooling that need to inspect the SQL files.
MIGRATIONS_DIR = _MIGRATIONS_DIR
MIGRATIONS_PG_DIR = _MIGRATIONS_PG_DIR

#: Pool bounds (see the module docstring for why these two numbers).
PG_POOL_MIN_SIZE = 2
PG_POOL_MAX_SIZE = 5


def is_postgres_url(database_url: str) -> bool:
    return database_url.startswith(_PG_PREFIXES)


def parse_sqlite_path(database_url: str) -> str:
    """Extract the on-disk path (or ``:memory:``) from a sqlite DSN.

    Accepts ``sqlite+aiosqlite:///./data/bot.db``, ``sqlite:///x.db`` and a bare
    path.
    """
    for prefix in ("sqlite+aiosqlite:///", "sqlite+aiosqlite://", "sqlite:///"):
        if database_url.startswith(prefix):
            return database_url[len(prefix):]
    return database_url


def pg_dsn(database_url: str) -> str:
    """The DSN handed to ``asyncpg``: ``postgres://`` alias fixed, ``channel_binding`` dropped.

    ``channel_binding=require`` is a libpq parameter asyncpg does not accept; Neon
    URIs carry it. ``sslmode`` is left untouched.
    """
    dsn = database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    elif dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://") :]
    parts = urllib.parse.urlsplit(dsn)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "channel_binding"
    ]
    if not query:
        return dsn
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def discover_migrations(directory: Path | None = None) -> list[tuple[int, Path]]:
    base = directory if directory is not None else _MIGRATIONS_DIR
    found: list[tuple[int, Path]] = []
    for path in sorted(base.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return found


class Database:
    """Owns the process's connection(s), whichever backend the DSN selects."""

    def __init__(self, database_url: str, *, search_path: str | None = None) -> None:
        self._url = database_url
        self._postgres = is_postgres_url(database_url)
        self._path = "" if self._postgres else parse_sqlite_path(database_url)
        self._conn: Any | None = None
        self._pool: Any | None = None
        #: Postgres schema to put first on the connection's ``search_path``.
        #: ``None`` keeps the server default; tests (and a deployment that keeps
        #: the bot's tables in a dedicated schema) pass a name.
        if search_path is not None and not _SCHEMA_RE.match(search_path):
            raise ValueError(f"invalid schema name {search_path!r}")
        self._search_path = search_path

    @property
    def path(self) -> str:
        """The SQLite file path; empty for a Postgres DSN (there is no file)."""
        return self._path

    @property
    def url(self) -> str:
        """The DSN this facade was built with.

        Used by the backup adapter to hand ``pg_dump`` the connection details it
        needs. It is the same value the process was configured with; callers
        must never log it.
        """
        return self._url

    @property
    def is_postgres(self) -> bool:
        return self._postgres

    @property
    def migrations_dir(self) -> Path:
        return _MIGRATIONS_PG_DIR if self._postgres else _MIGRATIONS_DIR

    async def connect(self) -> Any:
        if self._postgres:
            # Every caller gets its own facade over the shared pool. A facade can
            # enter a transaction (``BEGIN``) which pins one pooled connection;
            # returning a fresh handle per call keeps that transaction from
            # swallowing a concurrent store's statements, exactly like the
            # per-call ``connect()`` the SQLite stores already assume.
            if self._pool is None:
                await self._connect_postgres()
            return PgConnection(self._pool)
        if self._conn is not None:
            return self._conn
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # Autocommit (isolation_level=None): statements are individually atomic and
        # no implicit transaction is held on the shared connection. The bot
        # processes updates concurrently, so an explicit BEGIN (or an implicit
        # one) overlapping another would raise "cannot start a transaction within
        # a transaction". Idempotent upserts keep multi-statement operations safe.
        conn = await aiosqlite.connect(self._path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        self._conn = conn
        return conn

    async def _init_connection(self, connection: Any) -> None:
        await connection.execute(f'SET search_path TO "{self._search_path}"')

    async def _connect_postgres(self) -> None:
        import asyncpg

        pool = await asyncpg.create_pool(
            pg_dsn(self._url),
            min_size=PG_POOL_MIN_SIZE,
            max_size=PG_POOL_MAX_SIZE,
            timeout=30.0,
            # A pooler (Neon) rejects ``options=-csearch_path=...`` in the startup
            # packet, and ``init`` is not replayed on every pooled connection, so
            # the schema is set on each acquisition instead (``setup``). The
            # DSN is never modified to carry it.
            setup=self._init_connection if self._search_path else None,
            # A transaction-pooling proxy (Neon's ``-pooler`` host) does not keep
            # server-side prepared statements in the session it hands you: a
            # cached plan can be sent on a connection that never prepared it, and
            # the server answers "prepared statement does not exist". Disabling
            # the client-side statement cache makes every statement sent inline,
            # which a pooler accepts. It costs nothing on a direct connection.
            statement_cache_size=0,
        )
        self._pool = pool

    async def close(self) -> None:
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.close()
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def reload(self) -> Any:
        """Close and re-open the connection so the process serves the current data.

        This is what makes a restore work **without** a restart: every store here
        caches nothing but this one connection handle, so re-opening it is enough.
        For SQLite a restore swapped the file underneath, so re-opening reads the
        new one; for Postgres it reconnects the pool.
        """
        await self.close()
        return await self.connect()

    async def _applied_versions(self, conn: Any) -> set[int]:
        if self._postgres:
            async with conn.execute(
                "SELECT table_name AS name FROM information_schema.tables "
                "WHERE table_name = 'schema_migrations' "
                "AND table_schema = ANY (current_schemas(false))"
            ) as cur:
                row = await cur.fetchone()
        else:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return set()
        async with conn.execute("SELECT version FROM schema_migrations") as cur:
            rows = await cur.fetchall()
        return {int(r["version"]) for r in rows}

    async def migrate(self) -> list[int]:
        """Apply every pending migration in order; return the applied versions."""
        conn = await self.connect()
        applied_so_far = await self._applied_versions(conn)
        newly: list[int] = []
        for version, path in discover_migrations(self.migrations_dir):
            if version in applied_so_far:
                continue
            script = path.read_text(encoding="utf-8")
            try:
                if self._postgres:
                    # Postgres runs a migration as one transaction: a failure rolls
                    # the whole file back and leaves schema_migrations untouched.
                    await conn.execute("BEGIN")
                    await conn.executescript(script)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (?, to_char(now() at time zone 'utc', "
                        "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')) ON CONFLICT DO NOTHING",
                        (version,),
                    )
                    await conn.execute("COMMIT")
                else:
                    await conn.executescript(script)
                    await conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                        "VALUES (?, datetime('now'))",
                        (version,),
                    )
                    await conn.commit()
            except Exception:
                await conn.rollback()
                log.error("migration.failed", version=version, file=path.name)
                raise
            newly.append(version)
            log.info("migration.applied", version=version, file=path.name)
        return newly
