"""TASK backup-repair: the failure is readable, one scheduler owns the run, and
retention can never delete the artifact someone would restore.

Each test uses a file-backed database, like ``test_backup.py``: a backup of
``:memory:`` would not exercise the file the store actually snapshots.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cloudbot.adapters.persistence.backup_store import SqliteBackupStore
from cloudbot.adapters.persistence.database import Database
from cloudbot.bootstrap.backup_scheduler import BackupScheduler, enabled_from_env
from cloudbot.core.scrub import MAX_ERROR_CHARS
from cloudbot.domain.backup import BackupConfig
from cloudbot.services.backup import BackupService

DSN = "postgresql://owner:SuperSecretPass@db.example/cloudbot"
TOKEN = "7981234567:AAFakeTokenValueForTheBackupTestOnly_do_not_use"

async def _world(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await db.connect()
    await db.migrate()
    store = SqliteBackupStore(db)
    service = BackupService(store)
    return db, store, service

async def test_a_failed_scheduled_run_stores_the_real_scrubbed_reason(tmp_path):
    db, store, service = await _world(tmp_path)
    try:
        now = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
        await service.update_schedule(
            actor=1, period_kind="daily", period_hours=24, daily_time="03:00",
            retention_keep=2, destination_chat_id=6508129575, enabled=True, now=now,
        )

        async def boom(scope="full", *, persist=True):
            raise ConnectionError(f"could not connect: {DSN} (token {TOKEN})")

        service.make_backup = boom  # type: ignore[method-assign]
        # Force the stored anchor into the past: this is the exact state the
        # incident row was in (next_run_at 24h out, last attempt already failed).
        due_config = await store.config()
        await store.save_config(
            BackupConfig(**{**due_config.__dict__, "next_run_at": now - timedelta(minutes=1)}),
            actor=0,
        )
        assert await service.run_scheduled(now=now) is None

        stored = await store.config()
        # The literal "failed" is gone: the class, the reason, and no secret.
        assert stored.last_error != "failed"
        assert stored.last_error.startswith("ConnectionError: ")
        assert "could not connect" in stored.last_error
        assert "SuperSecretPass" not in stored.last_error
        assert "db.example" not in stored.last_error
        assert TOKEN not in stored.last_error
        assert len(stored.last_error) <= MAX_ERROR_CHARS
        # The run still advanced, so it cannot retry in a tight loop.
        assert stored.last_run_at is not None
        assert stored.next_run_at is not None
    finally:
        await db.close()

async def test_prune_counts_full_artifacts_only_and_never_the_newest(tmp_path):
    db, store, service = await _world(tmp_path)
    try:
        directory = Path(store.backup_dir())
        directory.mkdir(parents=True, exist_ok=True)
        for day in range(1, 5):
            (directory / f"cloudbot-backup-2026110{day}T000000Z.zip").write_bytes(b"x")
        (directory / "cloudbot-settings-20261101T000000Z.json").write_text("{}")

        deleted = await store.prune(1)
        left = sorted(path.name for path in directory.iterdir())
        # Three old fulls go; the newest full and every settings file stay.
        assert len([name for name in deleted if name.endswith(".zip")]) == 3
        assert "cloudbot-backup-20261104T000000Z.zip" in left
        assert not any(name.endswith(".zip") for name in deleted if "20261104" in name)
        assert "cloudbot-settings-20261101T000000Z.json" in left
    finally:
        await db.close()

def test_the_bot_scheduler_is_off_unless_the_switch_is_1():
    assert enabled_from_env({}) is False
    assert enabled_from_env({"CW_BOT_BACKUP_SCHEDULER": "0"}) is False
    assert enabled_from_env({"CW_BOT_BACKUP_SCHEDULER": ""}) is False
    assert enabled_from_env({"CW_BOT_BACKUP_SCHEDULER": "1"}) is True

async def test_a_disabled_scheduler_starts_no_task_and_keeps_manual_backups(tmp_path):
    db, store, service = await _world(tmp_path)
    try:
        async def send(*_args):
            raise AssertionError("a disabled scheduler must not deliver")

        scheduler = BackupScheduler(
            service, admin_ids=lambda: [1], send=send, enabled=False
        )
        scheduler.start()
        assert scheduler._task is None
        # The manual Telegram action is the service, not this class, and it works.
        artifact = await service.make_backup("settings", persist=False)
        assert artifact.scope == "settings"
        await scheduler.stop()
    finally:
        await db.close()

def test_a_due_row_is_not_skipped_because_a_fresh_artifact_exists():
    """The panel's old "handled_elsewhere" branch is gone; this pins the contract
    that only the *stored* schedule decides whether a run happens."""
    # Pure arithmetic, no I/O: due() is the one decision.
    from cloudbot.domain.backup import due

    now = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
    config = BackupConfig(
        enabled=True, period_kind="daily", daily_time="03:00",
        tz_name="Africa/Cairo", next_run_at=now - timedelta(minutes=1),
    )
    assert due(config, now)
    assert not due(config, now - timedelta(days=1))
