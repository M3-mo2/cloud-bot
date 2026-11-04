"""aiosqlite connection, migrations runner, and DSN parsing.

Migrations are forward-only, idempotent, and fail-fast: the bot refuses to start
if a migration fails (never run on a half-migrated DB).
"""

from __future__ import annotations

import re
from pathlib import Path

import aiosqlite
import structlog

log = structlog.get_logger("cloudbot.persistence")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d+)_")


def parse_sqlite_path(database_url: str) -> str:
    """Extract the on-disk path (or ``:memory:``) from a sqlite DSN.

    Accepts ``sqlite+aiosqlite:///./data/bot.db``, ``sqlite:///x.db`` and a bare
    path.
    """
    for prefix in ("sqlite+aiosqlite:///", "sqlite+aiosqlite://", "sqlite:///"):
        if database_url.startswith(prefix):
            return database_url[len(prefix):]
    return database_url


def discover_migrations() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return found


class Database:
    """Owns one aiosqlite connection for the process lifetime."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._path = parse_sqlite_path(database_url)
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        return self._path

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        self._conn = conn
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _applied_versions(self, conn: aiosqlite.Connection) -> set[int]:
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
        for version, path in discover_migrations():
            if version in applied_so_far:
                continue
            script = path.read_text(encoding="utf-8")
            try:
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
