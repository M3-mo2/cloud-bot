"""``python -m cloudbot`` entry point.

The operator starts the bot; this process builds everything, runs startup checks
(migrations + ``/v1/meta``), registers commands, and starts polling. It refuses to
start without a configured token.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from cloudbot.core.config import ConfigError, Settings
from cloudbot.core.logging import configure_logging, startup_banner
from cloudbot.core.modes import RunMode


def _load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


async def _run() -> int:
    try:
        settings = _load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.mode, settings.effective_log_level)
    startup_banner(settings.mode)

    if not settings.bot_token.get_secret_value():
        print("configuration error: CLOUDBOT_BOT_TOKEN is not set", file=sys.stderr)
        return 2

    from cloudbot.bootstrap import app as app_module
    from cloudbot.bootstrap import lifecycle
    from cloudbot.bootstrap.container import build

    container = build(settings)
    bot = app_module.build_bot(settings)
    dispatcher = app_module.build_dispatcher(container, bot)

    try:
        await lifecycle.startup(container)
    except ConfigError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        await container.aclose()
        return 3

    try:
        await app_module.set_commands(bot, settings)
        await dispatcher.start_polling(bot)
    finally:
        await lifecycle.shutdown(container)
    return 0


def run() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
