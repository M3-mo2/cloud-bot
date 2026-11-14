"""Startup/shutdown: fetch meta, run migrations, close resources.

Fails fast if the engine is unreachable or the DB cannot migrate.
"""

from __future__ import annotations

import structlog

from cloudbot.bootstrap.container import Container
from cloudbot.core.config import ConfigError
from cloudbot.core.modes import mock_engine_allowed

log = structlog.get_logger("cloudbot.lifecycle")


async def startup(container: Container) -> None:
    settings = container.settings
    applied = await container.database.migrate()
    log.info("db.migrated", applied=applied)

    # The gate's own boot work, both best-effort and never fatal:
    # * retention — keep 90 days of decisions, and log the row count removed;
    # * self-check — measure the bot's admin rights in every enabled chat and
    #   write ``is_admin``, so the panel shows "needs attention" before the first
    #   user is ever gated.
    try:
        removed = await container.forcejoin_events.prune(keep_days=90)
        log.info("forcejoin.retention", removed=removed, keep_days=90)
    except Exception as exc:  # noqa: BLE001 - retention must not stop the boot
        log.warning("forcejoin.retention_failed", error=type(exc).__name__)
    try:
        await container.services.forcejoin.self_check()
    except Exception as exc:  # noqa: BLE001 - a broken check must not stop the boot
        log.warning("forcejoin.self_check_failed", error=type(exc).__name__)

    try:
        meta = await container.engine.meta(refresh=True)
        log.info(
            "engine.meta",
            version=meta.version,
            api_version=meta.api_version,
            profiles=[p.name for p in meta.profiles.profiles],
        )
        if meta.api_version and meta.api_version != "v1":
            raise ConfigError(
                f"engine api_version {meta.api_version!r} is not the expected 'v1'"
            )
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        if settings.mock_engine and mock_engine_allowed(settings.mode):
            log.warning("engine.meta.unavailable_mock", error=str(exc))
        else:
            raise ConfigError(f"engine /v1/meta unreachable at startup: {exc}") from exc


async def shutdown(container: Container) -> None:
    await container.aclose()
    log.info("cloudbot.shutdown")
