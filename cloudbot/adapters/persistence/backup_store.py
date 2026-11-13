"""SQLite + filesystem implementation of ``BackupStore``.

Two responsibilities, both deliberately primitive:

* **Database copies** use SQLite's own ``Connection.backup`` — never ``cp`` of a
  live file, which can capture a torn page or a half-written WAL. The source is
  the process's own aiosqlite connection (its worker thread drives the copy), the
  destination a throw-away in-memory ``sqlite3.Connection`` that is serialised
  with ``serialize()`` the instant the copy finishes.

* **Files** live in ``<database dir>/backups`` beside the live database, so the
  artifact and its safety copy are on the same volume as the thing they protect.
  Retention deletes only files carrying this feature's prefix.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import structlog

from cloudbot.adapters.persistence.database import Database
from cloudbot.domain.backup import (
    FULL_PREFIX,
    OWNED_PREFIXES,
    SETTINGS_PREFIX,
    BackupConfig,
    BackupFile,
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

#: Derived at import time to keep the settings scope in one place.
def _settings_sql() -> str:
    return ", ".join(f"'{name}'" for name in SETTINGS_TABLES)

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

class SqliteBackupStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._path = Path(db.path)

    def _require_sqlite(self) -> None:
        """Database copies are a SQLite file operation; say so instead of failing obscurely.

        The settings scope and every table read below work on either backend
        through the facade; only ``snapshot``/``install`` (a whole-file copy and
        swap) are inherently SQLite. A Postgres deployment backs up with
        ``pg_dump`` outside the bot, so the refusal names the backend.
        """
        if self._db.is_postgres:
            raise NotImplementedError(
                "database file backups are SQLite-only; back up Postgres with pg_dump"
            )

    # -- database copies -----------------------------------------------------
    async def snapshot(self) -> bytes:
        self._require_sqlite()
        source = await self._db.connect()
        target = sqlite3.connect(":memory:")
        try:
            await source.backup(target)
            return target.serialize()
        finally:
            target.close()

    async def install(self, data: bytes) -> None:
        """Swap the file under the live connection, then re-open the store.

        The write is atomic (write beside, ``os.replace``), so a crash mid-write
        leaves the old database in place, never a half-written one.
        """
        self._require_sqlite()
        target = Path(self._path)
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
        """A consistent copy of the live DB beside it; a bad restore is one tap
        from undone. Uses the backup API too, so it cannot be a torn copy."""
        target = _base_dir(self._path) / f"cloudbot-pre-restore-{_stamp()}.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        data = await self.snapshot()
        target.write_bytes(data)
        return str(target)

    async def probe(self, data: bytes) -> DbProbe:
        if not data:
            return DbProbe(sqlite=False, detail="empty")
        if b"SQLite format 3\x00" not in data[:100]:
            return DbProbe(sqlite=False, detail="not_sqlite")
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
            )
        except sqlite3.DatabaseError as exc:
            return DbProbe(sqlite=False, detail=f"sqlite_error:{type(exc).__name__}")
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    async def schema_version(self) -> int:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
        ) as cur:
            row = await cur.fetchone()
        return int(row["v"]) if row else 0

    async def counts(self) -> dict[str, int]:
        names = (
            "users",
            "user_workspaces",
            "terminal_sessions",
            "events",
            "subscriptions",
            "wallet_ledger",
            "stars_payments",
            "forcejoin_chats",
        )
        conn = await self._db.connect()
        out: dict[str, int] = {}
        for name in names:
            try:
                async with conn.execute(f"SELECT COUNT(*) AS n FROM {name}") as cur:
                    row = await cur.fetchone()
                out[name] = int(row["n"]) if row else 0
            except Exception:  # noqa: BLE001 - a table absent in an old schema is 0
                out[name] = 0
        return out

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
        return str(_base_dir(self._path) / "backups")

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
            stat = path.stat()
            found.append(
                BackupFile(
                    name=path.name,
                    size_bytes=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    scope=scope,
                )
            )
        return sorted(found, key=lambda f: (f.created_at, f.name), reverse=True)

    async def prune(self, keep: int) -> list[str]:
        keep = max(1, int(keep))
        owned = await self.files()
        # Retention counts **full** artifacts only (settings JSONs are tiny and
        # are never pruned). The newest full artifact is always kept, whatever
        # ``keep`` says — that is the artifact the operator would restore.
        fulls = [f for f in owned if f.scope == "full"]
        if not fulls:
            return []
        doomed = [f.name for f in fulls[keep:]]
        protected = fulls[0].name
        doomed = [name for name in doomed if name != protected]
        directory = Path(self.backup_dir())
        for name in reversed(doomed):
            try:
                (directory / name).unlink()
            except OSError:
                log.warning("backup.prune_failed", file=name)
        return doomed

    # -- pending file staging (an upload between screens) ---------------------
    def pending_dir(self) -> Path:
        return _base_dir(self._path) / "backup-pending"

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

    @property
    def _db_path(self) -> Path:
        return _base_dir(self._path)

# -- module helpers -----------------------------------------------------------
def _base_dir(db_path: str) -> Path:
    """Where this database's companions live.

    For a real file it is that file's directory (the artifact sits beside the
    thing it protects). An in-memory database has no directory, so tests get an
    isolated temporary one instead of writing ``backups/`` into the CWD.
    """
    if db_path == ":memory:" or not db_path:
        import tempfile

        return Path(tempfile.mkdtemp(prefix="cloudbot-mem-backup-"))
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
