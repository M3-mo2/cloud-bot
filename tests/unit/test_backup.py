"""TASK backup: one implementation, four capabilities, and the proofs he asked for.

Every test here uses a **file-backed** database, because a backup of ``:memory:``
would prove nothing about the file a restore replaces.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cloudbot.adapters.persistence.backup_store import SqliteBackupStore
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.domain.backup import (
    BackupConfig,
    due,
    next_run_after,
    with_enabled,
)
from cloudbot.services.backup import BackupService

NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
TOKEN = "7981234567:AAFakeTokenValueForTheBackupTestOnly_do_not_use"
ENGINE_KEY = "engine-key-fake-value-0123456789abcdef"
SESSION_TEXT = "MTProtoSessionVeryLongFakeString_0123456789abcdef"

#: Values that must never appear in any artifact, by content (not by filename).
SECRETS = (TOKEN, ENGINE_KEY, SESSION_TEXT, "CLOUDBOT_BOT_TOKEN", "CLOUDBOT_ENGINE_API_KEY")

async def _open(tmp_path: Path, name: str = "bot.db") -> Database:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / name}")
    await db.connect()
    await db.migrate()
    return db

async def _seed_user(db: Database, tg_id: int, name: str = "sara") -> None:
    repo = SqliteUserRepository(db)
    await repo.get_or_create(tg_id, default_locale=__import__("cloudbot.core.locale", fromlist=["Locale"]).Locale.EN)
    await repo.set_display_name(tg_id, name)
    await repo.add_workspace(tg_id, __import__("cloudbot.domain.value_objects", fromlist=["WorkspaceId"]).WorkspaceId(f"ws_{tg_id:026d}"))

@pytest.fixture
async def world(tmp_path):
    """A live database, a backup store beside it, and a service over both."""
    db = await _open(tmp_path)
    await _seed_user(db, 111, "first")
    store = SqliteBackupStore(db)
    service = BackupService(store)
    yield db, store, service, tmp_path
    await db.close()

# -- 1. instant backup --------------------------------------------------------
async def test_instant_backup_is_a_valid_sqlite_database(world):
    db, store, service, _ = world
    artifact = await service.make_backup("full")
    assert artifact.filename.endswith(".zip")

    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
        assert set(archive.namelist()) == {"bot.db", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        dumped = archive.read("bot.db")

    assert manifest["scope"] == "full"
    assert manifest["database_schema_version"] == await store.schema_version()
    assert manifest["counts"]["users"] == 1

    path = store.backup_dir() + "/_probe.db"
    Path(path).write_bytes(dumped)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = conn.execute("SELECT display_name FROM users WHERE tg_id = 111").fetchone()
        assert row == ("first",)
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] >= 1
    finally:
        conn.close()

async def test_backup_never_contains_a_credential(world, tmp_path):
    db, store, service, _ = world
    # The operator's real secret lives in .env, beside the database — the exact
    # file a naive "zip the directory" backup would leak.
    (tmp_path / ".env").write_text(
        f"CLOUDBOT_BOT_TOKEN={TOKEN}\nCLOUDBOT_ENGINE_API_KEY={ENGINE_KEY}\n",
        encoding="utf-8",
    )
    # A session file, too, in the same directory.
    (tmp_path / "bot.session").write_text(SESSION_TEXT, encoding="utf-8")

    full = await service.make_backup("full", persist=False)
    settings = await service.make_backup("settings", persist=False)
    for artifact in (full, settings):
        for secret in SECRETS:
            assert secret.encode() not in artifact.data, secret
    # And inside the database member, not just the zip wrapper.
    with zipfile.ZipFile(io.BytesIO(full.data)) as archive:
        inner = archive.read("bot.db")
    for secret in SECRETS:
        assert secret.encode() not in inner, secret

# -- 2. schedule arithmetic + persistence across a restart --------------------
def test_next_run_is_computed_from_the_last_run_not_from_now():
    daily = BackupConfig(period_kind="daily", daily_time="03:00", tz_name="Africa/Cairo")
    base = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)  # 03:00 Cairo
    assert next_run_after(daily, base) == datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    hourly = BackupConfig(period_kind="hours", period_hours=6)
    assert next_run_after(hourly, base) == base + timedelta(hours=6)

async def test_scheduler_advances_next_run_across_a_simulated_restart(world):
    db, store, service, _ = world
    start = NOW
    config = await service.update_schedule(
        actor=999, period_kind="hours", period_hours=6, daily_time="03:00",
        retention_keep=7, destination_chat_id=None, enabled=True, now=start,
    )
    assert config.next_run_at == start + timedelta(hours=6)
    assert not due(config, start + timedelta(hours=5))

    # A restart: a brand-new service over the same database re-reads the stored
    # anchor rather than recomputing it from "now".
    restarted = BackupService(SqliteBackupStore(db))
    reread = await restarted.config()
    assert reread.next_run_at == config.next_run_at

    # Nothing happens before the anchor, even a day after the restart.
    assert await restarted.run_scheduled(now=start + timedelta(hours=5, minutes=59)) is None
    assert (await restarted.config()).next_run_at == config.next_run_at

    fired_at = start + timedelta(hours=6, seconds=1)
    artifact = await restarted.run_scheduled(now=fired_at)
    assert artifact is not None and artifact.scope == "full"
    after = await restarted.config()
    assert after.last_run_at is not None
    assert after.next_run_at == after.last_run_at + timedelta(hours=6)
    assert due(after, after.next_run_at) and not due(after, after.next_run_at - timedelta(minutes=1))

def test_enabling_anchors_the_first_run_without_waiting_forever():
    config = BackupConfig(period_kind="hours", period_hours=6, next_run_at=None)
    enabled = with_enabled(config, True, NOW)
    assert enabled.enabled and enabled.next_run_at == NOW + timedelta(hours=6)

# -- 3. restore validation ----------------------------------------------------
async def _artifact_bytes(service: BackupService) -> bytes:
    return (await service.make_backup("full", persist=False)).data

async def test_non_sqlite_inner_member_is_refused(world):
    db, store, service, _ = world
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bot.db", b"this is not a database at all")
        archive.writestr("manifest.json", json.dumps({"scope": "full"}))
    verdict = await service.validate(buffer.getvalue())
    assert not verdict.ok
    assert verdict.reason == "not_sqlite"

async def test_a_non_zip_file_is_refused(world):
    _, _, service, _ = world
    verdict = await service.validate(b"\x00\x01 definitely not a zip")
    assert not verdict.ok and verdict.reason == "not_a_backup"

async def test_a_truncated_database_is_refused(world):
    _, _, service, _ = world
    data = await _artifact_bytes(service)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        database = archive.read("bot.db")
    truncated = database[: len(database) // 3]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bot.db", truncated)
        archive.writestr("manifest.json", json.dumps({"scope": "full"}))
    verdict = await service.validate(buffer.getvalue())
    assert not verdict.ok
    assert verdict.reason in ("truncated", "corrupt")

async def test_a_newer_schema_is_refused(world, tmp_path):
    _, _, service, _ = world
    # A "future" database: the expected table, a migration version beyond this code.
    path = tmp_path / "future.db"
    disk = sqlite3.connect(path)
    disk.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    disk.execute("INSERT INTO schema_migrations VALUES (999, '2030')")
    disk.commit()
    disk.close()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bot.db", path.read_bytes())
        archive.writestr("manifest.json", json.dumps({"scope": "full"}))
    verdict = await service.validate(buffer.getvalue())
    assert not verdict.ok
    assert verdict.reason == "newer_schema"
    assert verdict.schema_version == 999

async def test_refused_restore_replaces_nothing(world):
    db, store, service, _ = world
    before = Path(db.path).read_bytes()
    verdict = await service.validate(b"not a backup")
    assert not verdict.ok
    with pytest.raises(Exception):
        await service.restore(verdict)
    assert Path(db.path).read_bytes() == before
    assert (await store.config()).last_run_at is None

# -- 4. restore works without a restart + safety copy -------------------------
async def test_restore_serves_the_restored_data_in_the_same_process(tmp_path):
    source_db = await _open(tmp_path, "source.db")
    await _seed_user(source_db, 111, "from-the-backup")
    source_service = BackupService(SqliteBackupStore(source_db))
    data = (await source_service.make_backup("full", persist=False)).data
    await source_db.close()

    live = await _open(tmp_path, "live.db")
    await _seed_user(live, 222, "live-user")
    users = SqliteUserRepository(live)
    assert await users.get(111) is None

    store = SqliteBackupStore(live)
    service = BackupService(store)
    verdict = await service.validate(data)
    assert verdict.ok
    safety = await service.restore(verdict)

    # No restart: the same Database/connection handle now serves the restored file.
    assert await users.get(111) is not None
    assert (await users.get(111)).display_name == "from-the-backup"
    assert await users.get(222) is None
    assert Path(safety).exists()
    check = sqlite3.connect(safety)
    try:
        assert check.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        check.close()
    await live.close()

async def test_settings_restore_does_not_touch_a_single_user_row(tmp_path):
    db = await _open(tmp_path, "web.db")
    await _seed_user(db, 111, "first")
    await _seed_user(db, 222, "second")
    store = SqliteBackupStore(db)
    service = BackupService(store)

    # Back up settings while only the default schedule exists.
    settings_blob = (await service.make_backup("settings", persist=False)).data

    # Then change settings and add a user + a workspace.
    await service.update_schedule(
        actor=999, period_kind="daily", period_hours=24, daily_time="04:00",
        retention_keep=30, destination_chat_id=555, enabled=True,
    )
    await _seed_user(db, 333, "third")
    users = SqliteUserRepository(db)
    before = [await users.get(tg) for tg in (111, 222, 333)]

    verdict = await service.validate(settings_blob)
    assert verdict.ok and verdict.scope == "settings"
    await service.restore(verdict)

    # Every user row is byte-for-byte the same, and the whole table count is unchanged.
    after = [await users.get(tg) for tg in (111, 222, 333)]
    assert after == before
    conn = await db.connect()
    async with conn.execute("SELECT COUNT(*) AS n FROM users") as cur:
        assert int((await cur.fetchone())["n"]) == 3
    async with conn.execute("SELECT COUNT(*) AS n FROM user_workspaces") as cur:
        assert int((await cur.fetchone())["n"]) == 3
    # And the settings actually round-tripped.
    restored = await service.config()
    assert restored.daily_time == "03:00" and restored.retention_keep == 7
    await db.close()

# -- 5. retention -------------------------------------------------------------
async def test_retention_deletes_only_the_oldest_files_it_owns(world):
    _, store, service, _ = world
    directory = Path(store.backup_dir())
    directory.mkdir(parents=True, exist_ok=True)
    # Five files this feature owns, plus one a human put there.
    for index in range(5):
        (directory / f"cloudbot-backup-2026010{index + 1}T000000Z.zip").write_bytes(b"x")
    stranger = directory / "important-notes.txt"
    stranger.write_text("not ours", encoding="utf-8")

    deleted = await store.prune(2)
    assert len(deleted) == 3
    left = sorted(p.name for p in directory.iterdir())
    assert stranger.name in left
    assert len([n for n in left if n.startswith("cloudbot-backup-")]) == 2
    assert all("20260105" in n or "20260104" in n for n in left if n.startswith("cloudbot-backup-"))

# -- 6. artifact + screen hygiene --------------------------------------------
async def test_settings_artifact_is_the_manifest_and_has_no_secrets(world):
    _, _, service, _ = world
    artifact = await service.make_backup("settings", persist=False)
    assert artifact.filename.endswith(".json")
    payload = json.loads(artifact.data)
    assert payload["scope"] == "settings"
    for table in service._store.settings_tables():
        assert table in payload["tables"], table
    # The settings artifact never carries a user, a workspace or the ledger.
    for excluded in ("users", "user_workspaces", "wallet_ledger", "subscriptions", "events"):
        assert excluded not in payload["tables"]

@pytest.mark.parametrize("locale", ["en", "ar"])
async def test_backup_screens_are_pure_and_use_one_label_per_destination(locale):
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.catalog import _admin_view
    from cloudbot.presentation.screens.admin import backup as screen_mod

    view = _admin_view(Locale(locale))
    for screen in (
        screen_mod.backup_screen(view),
        screen_mod.schedule_screen(view),
        screen_mod.restore_prompt_screen(view),
        screen_mod.restore_confirm_screen(view, token="t", scope="full", label="f.zip"),
        screen_mod.restore_result_screen(view, ok=False, reason="newer_schema"),
        screen_mod.restore_result_screen(view, ok=True, scope="settings"),
    ):
        # ``backup_screen`` builds in **blocks** (its files carry in-body buttons);
        # ``InputRichMessage`` allows either blocks or html, never both, so the
        # body check reads whichever form the screen chose.
        built = screen.doc.build()
        body = built.html if built.html is not None else built.model_dump_json(exclude_none=True)
        assert "<h2>" in body or '"type":"heading"' in body
        buttons = [b for row in screen.markup.inline_keyboard for b in row]
        assert buttons
        # One label per destination: two buttons never share a callback destination
        # on the same screen with different text.
        labelled: dict[str, str] = {}
        for button in buttons:
            data = button.callback_data
            if data is None:
                continue
            assert labelled.setdefault(data, button.text) == button.text, data

async def test_the_backup_screen_digits_localize(world):
    """His rule: no Arabic-Indic digit in generated output; the screens localize."""
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.catalog import _admin_view
    from cloudbot.presentation.screens.admin import backup as screen_mod

    # The screen builds in blocks; the JSON form still contains every rendered
    # string, so the digit and Arabic checks read the same content.
    built = screen_mod.backup_screen(_admin_view(Locale.AR)).doc.build()
    arabic = built.html if built.html is not None else built.model_dump_json(exclude_none=True)
    assert not any("\u0660" <= ch <= "\u0669" for ch in arabic)
    assert any("\u0600" <= ch <= "\u06ff" for ch in arabic)
