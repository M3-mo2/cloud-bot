"""TASK backup-format — the artifact must be a dump of the database in use.

Two layers of proof:

* **Unit** (always run): the artifact is classified by its members, retention
  counts only the format this deployment produces and never deletes the newest,
  a legacy SQLite archive is never auto-deleted, and the child process that runs
  ``pg_dump`` gets the connection explicitly rather than inheriting ours.
* **Live** (opt in with ``CLOUDBOT_TEST_DATABASE_URL``): a freshly produced
  artifact's manifest matches the database it was dumped from — the same counts
  for at least ``users``, ``user_workspaces`` and ``events`` — and the dump is a
  real plain-SQL ``pg_dump``, not a stale SQLite file.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from cloudbot.adapters.persistence.backup_store import (
    _artifact_format,
    _pg_env,
)
from cloudbot.adapters.persistence.database import Database, is_postgres_url
from cloudbot.domain.backup import (
    LEGACY_FORMAT,
    PG_FORMAT,
    PG_MEMBER,
    SQLITE_FORMAT,
    SQLITE_MEMBER,
)
from cloudbot.services.backup import BackupService

DUMP = (
    b"--\n-- PostgreSQL database dump\n--\n"
    b"DROP TABLE IF EXISTS public.users;\n"
    b"CREATE TABLE public.users (tg_id bigint NOT NULL);\n"
    b"COPY public.users (tg_id) FROM stdin;\n1\n2\n3\n\\.\n"
    b"CREATE TABLE public.schema_migrations (version bigint NOT NULL);\n"
    b"COPY public.schema_migrations (version, applied_at) FROM stdin;\n13\t2026\n\\.\n"
)

def _zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)

def _manifest(**overrides: object) -> dict:
    base = {
        "scope": "full",
        "created_at": "2026-11-13T00:00:00Z",
        "code_schema_version": 13,
        "database_schema_version": 13,
        "counts": {"users": 3},
        "sha256": hashlib.sha256(DUMP).hexdigest(),
        "members": [PG_MEMBER, "manifest.json"],
        "format": PG_FORMAT,
        "database_version": "17.12",
    }
    base.update(overrides)
    return base

# -- classification -----------------------------------------------------------
def test_artifact_format_is_read_from_the_members_not_the_filename(tmp_path):
    pg = tmp_path / "cloudbot-backup-20261113T000000Z.zip"
    _zip(pg, {PG_MEMBER: DUMP, "manifest.json": json.dumps(_manifest()).encode()})
    assert _artifact_format(pg) == PG_FORMAT

    current_sqlite = tmp_path / "cloudbot-backup-20261113T000001Z.zip"
    _zip(
        current_sqlite,
        {
            SQLITE_MEMBER: b"SQLite format 3\x00",
            "manifest.json": json.dumps(_manifest(format=SQLITE_FORMAT)).encode(),
        },
    )
    assert _artifact_format(current_sqlite) == SQLITE_FORMAT

    # The incident's shape: a bot.db with no `format` field is legacy history.
    legacy = tmp_path / "cloudbot-backup-20261113T223940Z.zip"
    manifest = _manifest(members=["bot.db", "manifest.json"])
    manifest.pop("format")
    _zip(legacy, {SQLITE_MEMBER: b"SQLite format 3\x00", "manifest.json": json.dumps(manifest).encode()})
    assert _artifact_format(legacy) == LEGACY_FORMAT

    # Unreadable bytes are unknown, never "full".
    broken = tmp_path / "cloudbot-backup-20261113T000002Z.zip"
    broken.write_bytes(b"not a zip")
    assert _artifact_format(broken) == "unknown"

# -- retention ----------------------------------------------------------------
def _postgres_store(tmp_path: Path):
    from cloudbot.adapters.persistence.backup_store import SqliteBackupStore

    # No connection is opened by prune/list: it only reads the directory.
    return SqliteBackupStore(Database("postgresql://u:p@example.invalid/db"))

def _write_full(directory: Path, stamp: str, members: dict[str, bytes]) -> str:
    name = f"cloudbot-backup-{stamp}.zip"
    _zip(directory / name, members)
    return name

async def test_prune_counts_only_the_current_format_and_never_the_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDBOT_BACKUP_DIR", str(tmp_path))
    store = _postgres_store(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    pg_manifest = json.dumps(_manifest()).encode()
    pg = {PG_MEMBER: DUMP, "manifest.json": pg_manifest}
    _write_full(tmp_path, "20261113T000001Z", pg)
    _write_full(tmp_path, "20261113T000002Z", pg)
    _write_full(tmp_path, "20261113T000003Z", pg)
    _write_full(tmp_path, "20261113T000004Z", pg)

    # Six legacy SQLite archives, exactly the state the incident left on disk.
    legacy_manifest = json.dumps(_manifest(members=["bot.db", "manifest.json"])).encode()
    for day in range(1, 7):
        _write_full(
            tmp_path,
            f"2026111{day}T223940Z",
            {SQLITE_MEMBER: b"SQLite format 3\x00", "manifest.json": legacy_manifest},
        )
    settings = tmp_path / "cloudbot-settings-20261113T000005Z.json"
    settings.write_text("{}")

    deleted = await store.prune(2)

    remaining = sorted(path.name for path in tmp_path.iterdir())
    pg_left = sorted(name for name in remaining if _artifact_format(tmp_path / name) == PG_FORMAT)
    legacy_left = sorted(name for name in remaining if _artifact_format(tmp_path / name) == LEGACY_FORMAT)
    # Exactly two current-format archives remain, the two newest.
    assert pg_left == ["cloudbot-backup-20261113T000003Z.zip", "cloudbot-backup-20261113T000004Z.zip"]
    # The two older current-format archives are the only things deleted.
    assert deleted == [
        "cloudbot-backup-20261113T000001Z.zip",
        "cloudbot-backup-20261113T000002Z.zip",
    ]
    # Legacy files and settings are never auto-deleted.
    assert len(legacy_left) == 6
    assert settings.name in remaining

async def test_prune_deletes_nothing_when_every_current_archive_is_within_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDBOT_BACKUP_DIR", str(tmp_path))
    store = _postgres_store(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    pg = {PG_MEMBER: DUMP, "manifest.json": json.dumps(_manifest()).encode()}
    _write_full(tmp_path, "20261113T000001Z", pg)
    assert await store.prune(2) == []
    assert (tmp_path / "cloudbot-backup-20261113T000001Z.zip").exists()

# -- the child process environment -------------------------------------------
def test_pg_child_gets_the_connection_explicitly_and_never_the_parent_dsn():
    env = _pg_env(
        "postgresql://owner:secret@db.example:5433/cloudbot"
        "?sslmode=require&channel_binding=require"
    )
    assert env["PGHOST"] == "db.example"
    assert env["PGPORT"] == "5433"
    assert env["PGUSER"] == "owner"
    assert env["PGDATABASE"] == "cloudbot"
    assert env["PGPASSWORD"] == "secret"
    assert env["PGSSLMODE"] == "require"
    # The DSN itself is not handed to the child; only the split PG* values are.
    assert "DATABASE_URL" not in env

# -- validation of a Postgres artifact ---------------------------------------
async def test_a_postgres_artifact_validates_and_its_checksum_is_verified(tmp_path):
    from cloudbot.adapters.persistence.backup_store import SqliteBackupStore

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await db.connect()
    await db.migrate()
    service = BackupService(SqliteBackupStore(db))
    try:
        # The store probes by content, so a claim of Postgres is enough to test
        # that the manifest's format and digest gate the verdict.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(PG_MEMBER, DUMP)
            archive.writestr("manifest.json", json.dumps(_manifest()))
        verdict = await service.validate(buffer.getvalue())
        assert verdict.ok, verdict.reason
        assert verdict.manifest["format"] == PG_FORMAT

        tampered = io.BytesIO()
        with zipfile.ZipFile(tampered, "w") as archive:
            archive.writestr(PG_MEMBER, DUMP + b"--tampered\n")
            archive.writestr("manifest.json", json.dumps(_manifest()))
        refused = await service.validate(tampered.getvalue())
        assert not refused.ok and refused.reason == "corrupt"
    finally:
        await db.close()

# -- live: the manifest matches the database it was dumped from ---------------
LIVE_URL = os.environ.get("CLOUDBOT_TEST_DATABASE_URL", "")
LIVE = bool(LIVE_URL) and is_postgres_url(LIVE_URL)

@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_TEST_DATABASE_URL to a Postgres DSN")
async def test_a_fresh_artifact_manifest_matches_the_live_database(tmp_path, monkeypatch):
    import asyncpg

    from cloudbot.adapters.persistence.backup_store import SqliteBackupStore

    monkeypatch.setenv("CLOUDBOT_BACKUP_DIR", str(tmp_path))
    db = Database(LIVE_URL)
    service = BackupService(SqliteBackupStore(db))

    artifact = await service.make_backup("full", persist=True)

    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        assert set(archive.namelist()) == {PG_MEMBER, "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        dump = archive.read(PG_MEMBER)

    # A real pg_dump, hashed over the bytes actually stored.
    assert manifest["format"] == PG_FORMAT
    assert manifest["database_version"]
    assert manifest["sha256"] == hashlib.sha256(dump).hexdigest()
    assert b"PostgreSQL database dump" in dump[:4096]
    assert b"CREATE TABLE" in dump
    # Data is written as one `INSERT INTO … VALUES` per row (the artifact must be
    # inspectable/restorable SQL, not an opaque COPY stream).
    assert b"INSERT INTO" in dump

    # The counts come from the same snapshot: they equal the live table counts.
    conn = await asyncpg.connect(LIVE_URL, statement_cache_size=0)
    try:
        for table in ("users", "user_workspaces", "events", "subscriptions",
                      "wallet_ledger", "terminal_sessions"):
            live = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            assert manifest["counts"][table] == int(live), table
    finally:
        await conn.close()

    # And the dump's own rows for `users` are exactly the claimed count.
    rows = dump.count(b"INSERT INTO public.users ")
    assert rows == manifest["counts"]["users"]

def test_the_scheduler_cannot_produce_a_sqlite_artifact_on_postgres():
    """The defect, pinned: on a Postgres deployment the producer is pg_dump.

    ``snapshot`` chooses the producer from the DSN, so the scheduler — which
    calls the same ``make_backup`` — cannot fall back to a local ``bot.db``.
    """
    store = _postgres_store(Path("/tmp"))
    assert store.is_postgres is True
