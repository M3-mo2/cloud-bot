"""SQLite **and** Postgres implementation of ``BackupStore``.

Two responsibilities, both deliberately primitive:

* **A database dump** is whatever the live backend needs: for SQLite, SQLite's
  own ``Connection.backup`` (never ``cp`` of a live file, which can capture a torn
  page or a half-written WAL); for Postgres, a real ``pg_dump
  --no-owner --no-privileges --format=plain`` written as ``database.sql``. The
  row counts in the manifest are read from the **same Postgres snapshot** the
  dump was taken from (``pg_export_snapshot``), so a manifest never describes a
  different moment than its own bytes.

* **Files** live in ``<database dir>/backups`` beside the live database (for
  Postgres, beside the backup directory the operator configured), so the
  artifact and its safety copy are on the same volume as the thing they protect.
  Retention counts only artifacts of the format this deployment currently
  produces and never deletes a legacy SQLite archive or the newest artifact.

Why the scheduler must never reach the SQLite producer on a Postgres deployment:
the panel, the bot and the admin screens all read Postgres. An archive of a
stale local ``bot.db`` looks like a backup but restoring it loses every real
user. :meth:`snapshot` therefore selects the producer from the DSN, and
:meth:`prune` only ever counts the format that producer writes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import structlog

from cloudbot.adapters.persistence.database import Database, pg_dsn
from cloudbot.domain.backup import (
    COUNT_TABLES,
    LEGACY_FORMAT,
    MANIFEST_MEMBER,
    OWNED_PREFIXES,
    PG_FORMAT,
    PG_MEMBER,
    SETTINGS_FORMAT,
    SETTINGS_PREFIX,
    SQLITE_FORMAT,
    SQLITE_MEMBER,
    BackupConfig,
    BackupFile,
    BackupSnapshot,
    DbProbe,
    parse_daily_time,
)

log = structlog.get_logger("cloudbot.backup.store")

#: The tables a settings restore owns. Everything a user *did* — identity,
#: workspaces, sessions, the wallet, the ledger, payments, the audit trail — is
#: deliberately outside this set and is never written by a settings restore.
SETTINGS_TABLES: tuple[str, ...] = (
    "backup_config",       # the backup schedule itself is configuration
    "runtime_flags",       # operator switches (e.g. forcejoin.enabled)
    "forcejoin_chats",     # the gate's required chats
    "event_subscriptions", # per-admin notification toggles
    "admin_notify_state",  # per-admin quiet switch + scope + delivery cursor
)

#: A dump that takes longer than this is a failure, not a hung cron minute.
PG_DUMP_TIMEOUT_S = 10 * 60
#: A restore that takes longer than this is rolled back rather than left half-applied.
PG_PSQL_TIMEOUT_S = 10 * 60
_SQLITE_MAGIC = b"SQLite format 3\x00"
_PG_DUMP_MARKER = re.compile(rb"PostgreSQL database dump", re.IGNORECASE)
_PG_CREATE_TABLE = re.compile(rb"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
_PG_MIGRATIONS_COPY = re.compile(
    rb"COPY\s+(?:public\.)?\"?schema_migrations\"?\s*\(([^)]*)\)\s+FROM\s+stdin;",
    re.IGNORECASE,
)

def _message_id(row: object) -> str | None:
    """The stored Telegram message id, tolerating a pre-0013 row."""
    try:
        value = row["last_message_id"]  # type: ignore[index]
    except (KeyError, IndexError):
        return None
    text = str(value) if value is not None else ""
    return text or None

def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _artifact_format(path: Path) -> str:
    """Classify a full artifact by its members, never by its filename.

    Only the archive index and the small manifest are read, so listing an
    artifact directory never pulls a whole dump into memory.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            manifest: dict = {}
            if MANIFEST_MEMBER in names:
                try:
                    manifest = json.loads(archive.read(MANIFEST_MEMBER))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    manifest = {}
    except (OSError, zipfile.BadZipFile):
        return "unknown"
    if PG_MEMBER in names:
        return PG_FORMAT
    if SQLITE_MEMBER in names:
        declared = str(manifest.get("format") or "")
        return SQLITE_FORMAT if declared == SQLITE_FORMAT else LEGACY_FORMAT
    return "unknown"

class SqliteBackupStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._path = Path(db.path) if db.path else None

    @property
    def is_postgres(self) -> bool:
        return self._db.is_postgres

    # -- database dumps ------------------------------------------------------
    async def snapshot(self) -> BackupSnapshot:
        """A dump of the live database plus the counts of the same snapshot."""
        if self.is_postgres:
            return await self._postgres_snapshot()
        return await self._sqlite_snapshot()

    async def _sqlite_snapshot(self) -> BackupSnapshot:
        source = await self._db.connect()
        target = sqlite3.connect(":memory:")
        try:
            await source.backup(target)
            data = target.serialize()
        finally:
            target.close()
        version = await self.schema_version()
        counts = await self.counts()
        return BackupSnapshot(
            format=SQLITE_FORMAT,
            database=data,
            counts=counts,
            schema_version=version,
            database_version="",
            table_names=frozenset(idx["name"] for idx in await self._tables()),
        )

    async def _postgres_snapshot(self) -> BackupSnapshot:
        """``pg_dump`` and ``select count(*)`` from one exportable snapshot.

        The connection stays open across both steps: the dump child is started
        with ``--snapshot=<id>`` and the counts run on that same transaction, so
        the manifest counts describe exactly the bytes in ``database.sql``.
        """
        import asyncpg

        dsn = pg_dsn(self._db.url)
        # A Neon pooler rejects server-side prepared statements, so the cache is
        # off for the same reason the facade disables it.
        conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=30.0)
        try:
            await conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot_id = await conn.fetchval("SELECT pg_export_snapshot()")
            database = await asyncio.to_thread(
                _run_pg_dump, self._db.url, str(snapshot_id)
            )
            tables = await self._postgres_tables(conn)
            counts = await self._postgres_counts(conn, tables)
            version = await self._postgres_schema_version(conn)
            database_version = await conn.fetchval(
                "SELECT CURRENT_SETTING('server_version')"
            )
            await conn.execute("COMMIT")
        except BaseException:
            try:
                await conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001 - the original failure is what matters
                pass
            raise
        finally:
            await conn.close()
        return BackupSnapshot(
            format=PG_FORMAT,
            database=database,
            counts=counts,
            schema_version=version,
            database_version=str(database_version or ""),
            table_names=frozenset(tables),
        )

    async def _postgres_tables(self, conn: object) -> list[str]:
        rows = await conn.fetch(  # type: ignore[attr-defined]
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = ANY (current_schemas(false))"
        )
        return [str(row["tablename"]) for row in rows]

    async def _postgres_counts(self, conn: object, tables: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {name: 0 for name in COUNT_TABLES}
        present = set(tables)
        for name in COUNT_TABLES:
            if name not in present:
                continue
            try:
                value = await conn.fetchval(f'SELECT count(*) FROM "{name}"')  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - an unreadable table must not kill the dump
                value = 0
            counts[name] = int(value or 0)
        return counts

    async def _postgres_schema_version(self, conn: object) -> int:
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
            )
        except Exception:  # noqa: BLE001 - a schema without migrations is version 0
            return 0
        return int(row["v"]) if row else 0

    async def install(self, data: bytes) -> None:
        """Replace the live database with ``data`` and re-open it.

        SQLite writes atomically beside the file and renames (a crash mid-write
        leaves the old database in place). Postgres applies a plain-SQL dump
        through ``psql --single-transaction``; a failure rolls the whole file
        back, so the live database is never left half-restored.
        """
        if self.is_postgres:
            await asyncio.to_thread(_run_psql, self._db.url, data)
            await self._db.reload()
            return
        target = Path(str(self._path))
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".cloudbot-restore-", suffix=".db", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            await self._db.close()
            os.replace(temp_name, target)
            # WAL/SHM from the previous file must not shadow the restored one.
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(target) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            await self._db.reload()
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise

    async def safety_copy(self) -> str:
        """A consistent copy of the live DB before a restore is applied.

        SQLite writes a ``.db`` file beside the database; Postgres writes the
        dump as ``.sql`` in the backup directory. Either can be re-applied by
        :meth:`install`, so a failed restore is one step from undone.
        """
        if self.is_postgres:
            snapshot = await self.snapshot()
            target = Path(self.backup_dir()) / f"cloudbot-pre-restore-{_stamp()}.sql"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot.database)
            return str(target)
        target = _base_dir(str(self._path)) / f"cloudbot-pre-restore-{_stamp()}.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (await self.snapshot()).database
        target.write_bytes(data)
        return str(target)

    async def probe(self, data: bytes) -> DbProbe:
        """Inspect dump bytes without touching the live database."""
        if not data:
            return DbProbe(sqlite=False, detail="empty")
        if data[:16] == _SQLITE_MAGIC:
            return await self._sqlite_probe(data)
        if _PG_DUMP_MARKER.search(data[:8192]) or b"\nCREATE TABLE " in data:
            return self._postgres_probe(data)
        return DbProbe(sqlite=False, detail="unknown_format")

    async def _sqlite_probe(self, data: bytes) -> DbProbe:
        fd, name = tempfile.mkstemp(prefix=".cloudbot-probe-", suffix=".db")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            conn = sqlite3.connect(f"file:{name}?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                integrity = str(integrity[0]) if integrity else ""
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                tables = frozenset(str(r[0]) for r in rows)
                version = 0
                if "schema_migrations" in tables:
                    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
                    version = int(row[0]) if row else 0
            finally:
                conn.close()
            return DbProbe(
                sqlite=True,
                table_names=tables,
                schema_version=version,
                integrity=integrity,
                format=SQLITE_FORMAT,
            )
        except sqlite3.DatabaseError as exc:
            return DbProbe(sqlite=False, detail=f"sqlite_error:{type(exc).__name__}")
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    def _postgres_probe(self, data: bytes) -> DbProbe:
        tables = frozenset(
            match.group(1).decode("ascii", "replace")
            for match in _PG_CREATE_TABLE.finditer(data)
        )
        return DbProbe(
            sqlite=False,
            postgres=True,
            format=PG_FORMAT,
            table_names=tables,
            schema_version=_pg_schema_version(data),
            integrity="ok" if tables else "",
        )

    async def schema_version(self) -> int:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
        ) as cur:
            row = await cur.fetchone()
        return int(row["v"]) if row else 0

    async def counts(self) -> dict[str, int]:
        conn = await self._db.connect()
        out: dict[str, int] = {}
        for name in COUNT_TABLES:
            try:
                async with conn.execute(f"SELECT COUNT(*) AS n FROM {name}") as cur:
                    row = await cur.fetchone()
                out[name] = int(row["n"]) if row else 0
            except Exception:  # noqa: BLE001 - a table absent in an old schema is 0
                out[name] = 0
        return out

    async def _tables(self) -> list[dict[str, str]]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
        return [{"name": str(row["name"])} for row in rows]

    # -- settings ------------------------------------------------------------
    def settings_tables(self) -> tuple[str, ...]:
        return SETTINGS_TABLES

    async def settings_export(self) -> dict:
        conn = await self._db.connect()
        tables: dict[str, list[dict]] = {}
        for name in SETTINGS_TABLES:
            try:
                async with conn.execute(f"SELECT * FROM {name}") as cur:
                    rows = await cur.fetchall()
                tables[name] = [dict(row) for row in rows]
            except Exception:  # noqa: BLE001 - a table absent in an old schema
                tables[name] = []
        return {
            "scope": "settings",
            "created_at": _iso(datetime.now(timezone.utc)),
            "tables": tables,
        }

    async def settings_import(self, payload: dict) -> None:
        tables = payload.get("tables")
        if not isinstance(tables, dict):
            raise ValueError("settings artifact has no tables")
        conn = await self._db.connect()
        # Only the tables this scope owns are touched; every write is inside one
        # transaction that is rolled back whole if any table fails.
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for name in SETTINGS_TABLES:
                if name not in tables:
                    continue
                rows = tables[name]
                if not isinstance(rows, list):
                    raise ValueError(f"settings table {name!r} is not a list")
                await conn.execute(f"DELETE FROM {name}")
                if not rows:
                    continue
                columns = list(rows[0].keys())
                placeholders = ", ".join("?" for _ in columns)
                column_sql = ", ".join(columns)
                await conn.executemany(
                    f"INSERT INTO {name} ({column_sql}) VALUES ({placeholders})",
                    [tuple(row.get(column) for column in columns) for row in rows],
                )
            await conn.execute("COMMIT")
        except BaseException:
            await conn.execute("ROLLBACK")
            raise

    # -- configuration -------------------------------------------------------
    async def config(self) -> BackupConfig:
        conn = await self._db.connect()
        async with conn.execute("SELECT * FROM backup_config WHERE id = 1") as cur:
            row = await cur.fetchone()
        if row is None:
            return BackupConfig()
        return BackupConfig(
            enabled=bool(row["enabled"]),
            period_kind="daily" if str(row["period_kind"]) == "daily" else "hours",
            period_hours=int(row["period_hours"]),
            daily_time=str(row["daily_time"]),
            tz_name=str(row["tz_name"]),
            destination_chat_id=int(row["destination_chat_id"])
            if row["destination_chat_id"] is not None
            else None,
            retention_keep=int(row["retention_keep"]),
            next_run_at=_utc(row["next_run_at"]),
            last_run_at=_utc(row["last_run_at"]),
            last_error=str(row["last_error"] or ""),
            last_message_id=_message_id(row),
            updated_at=_utc(row["updated_at"]),
            updated_by=int(row["updated_by"]),
        )

    async def save_config(self, config: BackupConfig, *, actor: int) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO backup_config "
            "(id, enabled, period_kind, period_hours, daily_time, tz_name, "
            " destination_chat_id, retention_keep, next_run_at, last_run_at, "
            " last_error, last_message_id, updated_at, updated_by) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            " enabled = excluded.enabled, period_kind = excluded.period_kind, "
            " period_hours = excluded.period_hours, daily_time = excluded.daily_time, "
            " tz_name = excluded.tz_name, "
            " destination_chat_id = excluded.destination_chat_id, "
            " retention_keep = excluded.retention_keep, "
            " next_run_at = excluded.next_run_at, last_run_at = excluded.last_run_at, "
            " last_error = excluded.last_error, "
            " last_message_id = excluded.last_message_id, "
            " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (
                1 if config.enabled else 0,
                config.period_kind,
                max(1, int(config.period_hours)),
                _normalized_time(config.daily_time),
                config.tz_name or "Africa/Cairo",
                config.destination_chat_id,
                max(1, int(config.retention_keep)),
                _iso(config.next_run_at) if config.next_run_at else None,
                _iso(config.last_run_at) if config.last_run_at else None,
                config.last_error,
                config.last_message_id,
                _iso(datetime.now(timezone.utc)),
                int(actor),
            ),
        )
        await conn.commit()

    # -- files ---------------------------------------------------------------
    def backup_dir(self) -> str:
        configured = (os.environ.get("CLOUDBOT_BACKUP_DIR") or "").strip()
        if configured:
            return configured
        if self.is_postgres:
            # There is no database file to sit beside. The bot's working
            # directory is the repository (``WorkingDirectory`` in the unit),
            # which is exactly the directory the panel resolves by default
            # (``<panel>/../cloud-bot-real/backups``), so both writers share it.
            return str(Path.cwd() / "backups")
        return str(_base_dir(str(self._path)) / "backups")

    async def store(self, filename: str, data: bytes) -> str:
        target = Path(self.backup_dir()) / _safe_name(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        await _fsync_dir(target.parent)
        return str(target)

    async def read_owned(self, filename: str) -> bytes | None:
        name = _safe_name(filename)
        if not name.startswith(OWNED_PREFIXES):
            return None
        target = Path(self.backup_dir()) / name
        try:
            return target.read_bytes()
        except OSError:
            return None

    async def files(self) -> list[BackupFile]:
        directory = Path(self.backup_dir())
        if not directory.exists():
            return []
        found: list[BackupFile] = []
        for path in directory.iterdir():
            if not path.is_file() or not path.name.startswith(OWNED_PREFIXES):
                continue
            scope = "settings" if path.name.startswith(SETTINGS_PREFIX) else "full"
            fmt = (
                SETTINGS_FORMAT
                if scope == "settings"
                else await asyncio.to_thread(_artifact_format, path)
            )
            stat = path.stat()
            found.append(
                BackupFile(
                    name=path.name,
                    size_bytes=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    scope=scope,
                    format=fmt,
                )
            )
        return sorted(found, key=lambda f: (f.created_at, f.name), reverse=True)

    async def prune(self, keep: int) -> list[str]:
        """Delete old artifacts **of the format this deployment produces**.

        Legacy SQLite archives and settings JSONs are never auto-deleted: they
        are either unrestorable history or too small to matter, and deleting
        them would silently destroy the only copy of an old database. The newest
        artifact of the current format is protected whatever ``keep`` says. The
        deletion is logged once, with the names.
        """
        keep = max(1, int(keep))
        current = PG_FORMAT if self.is_postgres else SQLITE_FORMAT
        # On SQLite a ``bot.db`` archive is restorable whether or not it carries
        # this version's ``format`` field, so both labels count. On Postgres a
        # ``bot.db`` archive is unrestorable history and is never counted.
        countable = {current} | (
            {LEGACY_FORMAT, "unknown"} if current == SQLITE_FORMAT else set()
        )
        owned = await self.files()
        current_full = [f for f in owned if f.scope == "full" and f.format in countable]
        if not current_full:
            return []
        protected = current_full[0].name
        doomed = [f.name for f in current_full[keep:] if f.name != protected]
        if not doomed:
            return []
        directory = Path(self.backup_dir())
        removed: list[str] = []
        for name in reversed(doomed):
            try:
                (directory / name).unlink()
            except OSError:
                log.warning("backup.prune_delete_failed", file=name)
                continue
            removed.append(name)
        if removed:
            log.info("backup.pruned", deleted=removed, kept=keep, format=current)
        return removed

    # -- pending file staging (an upload between screens) ---------------------
    def pending_dir(self) -> Path:
        return _base_dir(str(self._path)) / "backup-pending"

    async def pending_path(self, token: str, suffix: str) -> str:
        return str(self.pending_dir() / _safe_name(f"{token}{suffix}"))

    async def write_pending(self, token: str, suffix: str, data: bytes) -> str:
        target = self.pending_dir() / _safe_name(f"{token}{suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    async def read_pending(self, token: str, suffix: str) -> bytes:
        target = self.pending_dir() / _safe_name(f"{token}{suffix}")
        return target.read_bytes()

    async def drop_pending(self, token: str, suffix: str) -> None:
        target = self.pending_dir() / _safe_name(f"{token}{suffix}")
        try:
            target.unlink()
        except OSError:
            pass

# -- Postgres child processes -------------------------------------------------
def _pg_env(database_url: str) -> dict[str, str]:
    """The ``PG*`` environment for ``pg_dump``/``psql``.

    The DSN is split into the standard libpq variables because a command line is
    visible in ``ps``; the password lives only in the child's environment. The
    parent's ``DATABASE_URL`` is deliberately not passed through — a child
    should not inherit a DSN it does not need. The environment is passed
    explicitly because a scheduler's child is not guaranteed to inherit the
    parent's.
    """
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(database_url)
    env = {
        "PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin",
        "PGHOST": parts.hostname or "",
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "PGDATABASE": unquote((parts.path or "").lstrip("/")),
        "PGAPPNAME": "cloudbot-backup",
        "PGCLIENTENCODING": "UTF8",
    }
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    for key, value in _query_pairs(parts.query):
        if key == "sslmode":
            env["PGSSLMODE"] = value
        elif key == "channel_binding":
            env["PGCHANNELBINDING"] = value
    return env

def _query_pairs(query: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl

    return parse_qsl(query, keep_blank_values=True)

def _server_major(database_url: str) -> int:
    """Best-effort server major version, for picking a matching ``pg_dump``."""
    import asyncpg  # noqa: F401 - the dependency is required for Postgres

    try:
        conn = asyncio.run(_fetch_server_version(database_url))
    except Exception:  # noqa: BLE001 - fall back to the newest installed client
        return 0
    match = re.match(r"(\d+)", conn or "")
    return int(match.group(1)) if match else 0

async def _fetch_server_version(database_url: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(pg_dsn(database_url), statement_cache_size=0, timeout=30.0)
    try:
        return str(await conn.fetchval("SHOW server_version"))
    finally:
        await conn.close()

def _pg_dump_bin(database_url: str) -> str:
    """The right ``pg_dump``: explicit, else a client at least the server's major.

    Ubuntu's ``/usr/bin/pg_dump`` is a wrapper that picks the *newest installed*
    client, not the server's, and an older client refuses a newer server. The
    versioned binaries are preferred so the dump cannot fail on that mismatch.
    """
    configured = (os.environ.get("CLOUDBOT_PG_DUMP_BIN") or "").strip()
    if configured:
        return configured
    major = _server_major(database_url)
    if major:
        root = Path("/usr/lib/postgresql")
        candidates = sorted(
            (int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit()),
            reverse=True,
        ) if root.exists() else []
        for version in candidates:
            if version >= major and (root / str(version) / "bin" / "pg_dump").is_file():
                return str(root / str(version) / "bin" / "pg_dump")
    return "pg_dump"

def _psql_bin() -> str:
    configured = (os.environ.get("CLOUDBOT_PSQL_BIN") or "").strip()
    return configured or "psql"

def _run_pg_dump(database_url: str, snapshot_id: str) -> bytes:
    """Run ``pg_dump`` in this process's child with an explicit environment."""
    command = [
        _pg_dump_bin(database_url),
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "--format=plain",
        # Data as one `INSERT INTO … VALUES` per row rather than a COPY stream:
        # the artifact must be readable as restorable SQL by inspection (and by
        # any client that does not speak COPY), and the size is trivial here.
        "--inserts",
        f"--snapshot={snapshot_id}",
    ]
    try:
        result = subprocess.run(
            command,
            env=_pg_env(database_url),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PG_DUMP_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pg_dump timed out after {PG_DUMP_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start pg_dump: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            f"pg_dump exited {result.returncode}: {detail[-1] if detail else 'no output'}"
        )
    if not result.stdout:
        raise RuntimeError("pg_dump produced an empty dump")
    return result.stdout

def _run_psql(database_url: str, sql: bytes) -> None:
    """Apply a plain-SQL dump to the live database in one transaction."""
    command = [
        _psql_bin(),
        "--no-psqlrc",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "--single-transaction",
    ]
    try:
        result = subprocess.run(
            command,
            env=_pg_env(database_url),
            input=sql,
            capture_output=True,
            timeout=PG_PSQL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"psql timed out after {PG_PSQL_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start psql: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            f"psql exited {result.returncode}: {detail[-1] if detail else 'no output'}"
        )

def _pg_schema_version(data: bytes) -> int:
    """The max ``schema_migrations.version`` inside a plain-SQL dump.

    Both data spellings are handled: a ``COPY`` block (until pg_dump 16 ran
    without ``--inserts``) and one ``INSERT INTO … VALUES`` per row (what this
    bot now emits).
    """
    match = _PG_MIGRATIONS_COPY.search(data)
    if match:
        start = match.end()
        end = data.find(b"\n\\.", start)
        block = data[start : end if end >= 0 else start]
        highest = 0
        for line in block.splitlines():
            first = line.strip().split(b"\t", 1)[0]
            try:
                highest = max(highest, int(first))
            except ValueError:
                continue
        return highest
    insert = re.compile(
        rb"INSERT\s+INTO\s+(?:public\.)?\"?schema_migrations\"?\s*"
        rb"\((?:[^)]*?)\bversion\b(?:[^)]*)\)\s+VALUES\s*\(\s*(\d+)",
        re.IGNORECASE,
    )
    highest = 0
    for found in insert.finditer(data):
        highest = max(highest, int(found.group(1)))
    return highest

# -- module helpers -----------------------------------------------------------
def _base_dir(db_path: str) -> Path:
    """Where this database's companions live.

    For a real file it is that file's directory (the artifact sits beside the
    thing it protects). An in-memory database has no directory, so tests get an
    isolated temporary one instead of writing ``backups/`` into the CWD.
    """
    if db_path in (":memory:", "", "None"):
        return Path(os.environ.get("CLOUDBOT_BACKUP_DIR") or tempfile.mkdtemp(prefix="cloudbot-mem-backup-"))
    return Path(db_path).parent

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _normalized_time(value: str) -> str:
    hour, minute = parse_daily_time(value)
    return f"{hour:02d}:{minute:02d}"

def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned or "artifact"

async def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
