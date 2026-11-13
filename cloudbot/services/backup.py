"""``BackupService`` — one backup implementation, one restore implementation.

The admin panel and the scheduler are **triggers**: both call
:meth:`BackupService.make_backup`; both restore paths call
:meth:`BackupService.restore`. Nothing about the artifact shape lives in a screen
or a handler.

The artifact is a single ``.zip`` holding

* ``bot.db`` — a consistent copy made with SQLite's own backup API, and
* ``manifest.json`` — scope, created_at, the code schema version, the database
  schema version and row counts.

A zip (not a bare ``.db``) is chosen because one artifact has to carry the
manifest that restore validates against, and Telegram's document size limit is
more comfortably met by a deflated database. The settings scope is a ``.json``
file, which is its own manifest — there is no database copy in it at all.

**Never included:** ``.env``, the bot token, the engine key, any MTProto session
file, or a log. None of those live in ``bot.db``; this module reads the database
and nothing else. ``tests/unit/test_backup.py`` greps every artifact for the
project's own secret shapes and asserts the value is absent.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import datetime, timezone

import structlog

from cloudbot.core.scrub import scrub_error
from cloudbot.domain.backup import (
    FULL_PREFIX,
    SETTINGS_PREFIX,
    BackupArtifact,
    BackupConfig,
    BackupFile,
    BackupScope,
    RestoreValidation,
    after_run,
    due,
    next_run_after,
    with_enabled,
)
from cloudbot.domain.ports.backup import BackupStore

log = structlog.get_logger("cloudbot.backup")

DB_MEMBER = "bot.db"
MANIFEST_MEMBER = "manifest.json"
#: What separates a preview token from the user-chosen restore scope.
SCOPE_SUFFIX = {"full": ".zip", "settings": ".json"}

class BackupError(Exception):
    """A refusal the panel renders. ``reason`` is a stable code, never English."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

class BackupService:
    def __init__(self, store: BackupStore) -> None:
        self._store = store

    def _secrets(self) -> tuple[str, ...]:
        """The live secret values a failure message might echo, for the scrubber.

        The store's DSN is not readable through the port, but the settings are:
        the engine key and the bot token are the two values a failed HTTP call
        can carry into an exception string. They are passed to :func:`scrub_error`
        by exact value as well as by shape.
        """
        values: list[str] = []
        try:
            from cloudbot.core.config import get_settings

            settings = get_settings()
            values.append(settings.bot_token.get_secret_value())
            values.append(settings.engine_api_key.get_secret_value())
            values.append(settings.database_url)
        except Exception:  # noqa: BLE001 - scrubbing must work without settings
            pass
        return tuple(value for value in values if value)

    # -- configuration -------------------------------------------------------
    async def config(self) -> BackupConfig:
        return await self._store.config()

    async def files(self) -> list[BackupFile]:
        return await self._store.files()

    async def schema_version(self) -> int:
        return await self._store.schema_version()

    async def prune(self) -> list[str]:
        """Delete files past the retention limit; returns the names removed."""
        config = await self._store.config()
        return await self._store.prune(config.retention_keep)

    def directory(self) -> str:
        return self._store.backup_dir()

    async def save_config(self, config: BackupConfig, *, actor: int, now: datetime | None = None) -> BackupConfig:
        now = now or _utc_now()
        if config.next_run_at is None and config.enabled:
            anchor = config.last_run_at or now
            config = replace(config, next_run_at=next_run_after(config, anchor))
        await self._store.save_config(config, actor=actor)
        return await self._store.config()

    async def set_enabled(self, enabled: bool, *, actor: int, now: datetime | None = None) -> BackupConfig:
        now = now or _utc_now()
        updated = with_enabled(await self._store.config(), enabled, now)
        await self._store.save_config(updated, actor=actor)
        return await self._store.config()

    async def update_schedule(
        self,
        *,
        actor: int,
        period_kind: str,
        period_hours: int,
        daily_time: str,
        retention_keep: int,
        destination_chat_id: int | None,
        tz_name: str = "Africa/Cairo",
        enabled: bool | None = None,
        now: datetime | None = None,
    ) -> BackupConfig:
        now = now or _utc_now()
        current = await self._store.config()
        candidate = BackupConfig(
            enabled=current.enabled if enabled is None else enabled,
            period_kind="daily" if period_kind == "daily" else "hours",
            period_hours=max(1, int(period_hours)),
            daily_time=daily_time,
            tz_name=tz_name or current.tz_name,
            destination_chat_id=destination_chat_id,
            retention_keep=max(1, int(retention_keep)),
            last_run_at=current.last_run_at,
            last_error=current.last_error,
        )
        # A changed cadence (or a switch-on) re-anchors from the last run, never
        # from "now", so the interval cannot drift across the change either.
        anchor = current.last_run_at or now
        candidate = replace(candidate, next_run_at=next_run_after(candidate, anchor))
        if candidate.enabled:
            candidate = with_enabled(candidate, True, now)
        await self._store.save_config(candidate, actor=actor)
        return await self._store.config()

    # -- the one backup implementation --------------------------------------
    async def make_backup(
        self, scope: BackupScope = "full", *, persist: bool = True
    ) -> BackupArtifact:
        """Build the artifact. The panel sends it; the scheduler stores and sends it."""
        created_at = _utc_now()
        if scope == "settings":
            payload = await self._store.settings_export()
            payload["created_at"] = _iso(created_at)
            payload["code_schema_version"] = await self._store.schema_version()
            data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            artifact = BackupArtifact(
                scope="settings",
                filename=f"{SETTINGS_PREFIX}{created_at.strftime('%Y%m%dT%H%M%SZ')}.json",
                data=data,
                schema_version=int(payload["code_schema_version"]),
                created_at=created_at,
            )
        else:
            database = await self._store.snapshot()
            schema_version = await self._store.schema_version()
            counts = await self._store.counts()
            manifest = {
                "scope": "full",
                "created_at": _iso(created_at),
                "code_schema_version": schema_version,
                "database_schema_version": schema_version,
                "counts": counts,
                "sha256": hashlib.sha256(database).hexdigest(),
                "members": [DB_MEMBER, MANIFEST_MEMBER],
            }
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(DB_MEMBER, database)
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, sort_keys=True, indent=2))
            artifact = BackupArtifact(
                scope="full",
                filename=f"{FULL_PREFIX}{created_at.strftime('%Y%m%dT%H%M%SZ')}.zip",
                data=buffer.getvalue(),
                schema_version=schema_version,
                created_at=created_at,
            )
        if persist:
            await self._store.store(artifact.filename, artifact.data)
        return artifact

    async def run_scheduled(self, *, now: datetime | None = None) -> BackupArtifact | None:
        """The scheduler's one entry: make, store, prune, advance ``next_run_at``.

        Returns the artifact when a run happened, ``None`` when it is not due.
        The advance is anchored on ``ran_at`` (the real run), so a restart between
        runs cannot drift the cadence.
        """
        now = now or _utc_now()
        config = await self._store.config()
        if not due(config, now):
            return None
        try:
            artifact = await self.make_backup("full", persist=True)
        except Exception as exc:  # noqa: BLE001 - a failed run must not kill the loop
            # The stored string is the *real* failure, scrubbed and capped: a
            # literal "failed" told the operator and the panel nothing, which is
            # how two silent runs went unexplained. ``exc_info`` puts the whole
            # traceback in journald. The pointer is guarded separately below.
            message = scrub_error(exc, secrets=self._secrets())
            log.error(
                "backup.run_failed",
                error=type(exc).__name__,
                detail=message,
                exc_info=True,
            )
            failed = replace(
                config, last_error=message, last_run_at=now,
                next_run_at=next_run_after(config, now),
            )
            await self._store.save_config(failed, actor=0)
            return None
        # A failed prune must not masquerade as a failed backup: the artifact
        # exists and is valid, so it is recorded as a success. The prune problem
        # is logged loudly instead of silently swallowed.
        try:
            await self._store.prune(config.retention_keep)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "backup.prune_failed",
                detail=scrub_error(exc, secrets=self._secrets()),
                exc_info=True,
            )
        advanced = after_run(config, _utc_now())
        await self._store.save_config(advanced, actor=0)
        return artifact

    # -- the one validation implementation ----------------------------------
    async def validate(self, data: bytes, *, expected_scope: BackupScope | None = None) -> RestoreValidation:
        """Inspect bytes and return the one verdict every restore path uses.

        Refusals, each with a specific reason:

        * ``not_a_backup`` — the bytes are not a zip (full) / a settings JSON;
        * ``missing_database`` / ``missing_manifest`` — the zip is incomplete;
        * ``not_sqlite`` — the inner file is not a SQLite database;
        * ``truncated`` / ``corrupt`` — integrity check failed;
        * ``no_migrations`` — a real SQLite file that is not this bot's database;
        * ``newer_schema`` — the file's schema is newer than this code;
        * ``bad_settings`` — a settings file without the expected shape.
        """
        code_version = await self._store.schema_version()
        if not data:
            return RestoreValidation(ok=False, scope="full", reason="empty")
        if data.lstrip()[:1] == b"{":
            return await self._validate_settings(data, code_version)
        return await self._validate_full(data, code_version, expected_scope)

    async def _validate_settings(self, data: bytes, code_version: int) -> RestoreValidation:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return RestoreValidation(ok=False, scope="settings", reason="bad_settings")
        if not isinstance(payload, dict) or payload.get("scope") != "settings":
            return RestoreValidation(ok=False, scope="settings", reason="bad_settings")
        if not isinstance(payload.get("tables"), dict):
            return RestoreValidation(ok=False, scope="settings", reason="bad_settings")
        file_version = int(payload.get("code_schema_version") or 0)
        if file_version > code_version:
            return RestoreValidation(
                ok=False, scope="settings", reason="newer_schema",
                schema_version=file_version, manifest=payload,
            )
        return RestoreValidation(
            ok=True, scope="settings", schema_version=file_version, manifest=payload,
            database_bytes=data,
        )

    async def _validate_full(
        self, data: bytes, code_version: int, expected_scope: BackupScope | None
    ) -> RestoreValidation:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return RestoreValidation(ok=False, scope="full", reason="not_a_backup")
        names = set(archive.namelist())
        if DB_MEMBER not in names:
            return RestoreValidation(ok=False, scope="full", reason="missing_database")
        if MANIFEST_MEMBER not in names:
            return RestoreValidation(ok=False, scope="full", reason="missing_manifest")
        try:
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return RestoreValidation(ok=False, scope="full", reason="missing_manifest")
        database = archive.read(DB_MEMBER)
        expected_digest = str(manifest.get("sha256") or "")
        if expected_digest and hashlib.sha256(database).hexdigest() != expected_digest:
            return RestoreValidation(ok=False, scope="full", reason="corrupt", manifest=manifest)
        looks_sqlite = database[:16] == b"SQLite format 3\x00"
        if not database:
            return RestoreValidation(ok=False, scope="full", reason="missing_database", manifest=manifest)
        probe = await self._store.probe(database)
        if not probe.sqlite:
            reason = "truncated" if looks_sqlite else "not_sqlite"
            return RestoreValidation(ok=False, scope="full", reason=reason, manifest=manifest)
        if probe.integrity and probe.integrity.lower() != "ok":
            return RestoreValidation(
                ok=False, scope="full", reason="corrupt", detail=probe.integrity, manifest=manifest
            )
        if not probe.has_migrations:
            return RestoreValidation(
                ok=False, scope="full", reason="no_migrations", manifest=manifest
            )
        if probe.schema_version > code_version:
            return RestoreValidation(
                ok=False, scope="full", reason="newer_schema",
                schema_version=probe.schema_version, manifest=manifest,
            )
        if probe.schema_version <= 0:
            return RestoreValidation(
                ok=False, scope="full", reason="truncated", manifest=manifest
            )
        if expected_scope is not None and expected_scope != "full":
            return RestoreValidation(ok=False, scope="full", reason="not_a_backup")
        return RestoreValidation(
            ok=True, scope="full", schema_version=probe.schema_version,
            manifest=manifest, database_bytes=database,
        )

    # -- the one restore implementation -------------------------------------
    async def restore(self, validation: RestoreValidation) -> str:
        """Apply a validated artifact. Returns the safety-copy path (full scope).

        Order is what protects his data: a full restore makes the safety copy
        **before** replacing anything; if re-opening the restored file fails, the
        safety copy is put back and the error is raised, so a failed restore can
        never leave the bot pointing at a broken database.
        """
        if not validation.ok:
            raise BackupError(validation.reason)
        if validation.scope == "settings":
            payload = validation.manifest or {}
            try:
                await self._store.settings_import(payload)
            except Exception as exc:  # noqa: BLE001
                log.error("backup.settings_import_failed", error=type(exc).__name__)
                raise BackupError("settings_failed", detail=type(exc).__name__) from exc
            return ""
        safety = await self._store.safety_copy()
        try:
            await self._store.install(validation.database_bytes)
            # Prove the re-opened store is readable before declaring success.
            await self._store.schema_version()
        except Exception as exc:  # noqa: BLE001
            log.error("backup.restore_reopen_failed", error=type(exc).__name__)
            try:
                await self._store.install(await _read_file(safety))
                await self._store.schema_version()
                raise BackupError("restore_failed_recovered", detail=safety) from exc
            except BackupError:
                raise
            except Exception as second:  # noqa: BLE001
                log.error("backup.safety_restore_failed", error=type(second).__name__)
                raise BackupError("restore_failed_safety", detail=safety) from second
        return safety

    # -- staging an uploaded file between screens ---------------------------
    async def stage_upload(self, token: str, data: bytes) -> BackupScope:
        """Keep an uploaded artifact until the admin confirms; return its scope."""
        suffix = ".json" if data.lstrip()[:1] == b"{" else ".zip"
        await self._store.write_pending(token, suffix, data)
        return "settings" if suffix == ".json" else "full"

    async def read_owned(self, filename: str) -> bytes | None:
        return await self._store.read_owned(filename)

    async def staged(self, token: str) -> bytes | None:
        for suffix in (".zip", ".json"):
            try:
                return await self._store.read_pending(token, suffix)
            except OSError:
                continue
        return None

    async def discard_staged(self, token: str) -> None:
        for suffix in (".zip", ".json"):
            await self._store.drop_pending(token, suffix)

async def _read_file(path: str) -> bytes:
    from pathlib import Path

    return Path(path).read_bytes()
