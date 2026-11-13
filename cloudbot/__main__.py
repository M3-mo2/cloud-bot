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
    from cloudbot.bootstrap.backup_scheduler import BackupScheduler, enabled_from_env
    from cloudbot.bootstrap.notify import NotificationDispatcher, NotificationPoller
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.rich.sender import RichSender

    container = build(settings)
    bot = app_module.build_bot(settings)
    dispatcher = app_module.build_dispatcher(container, bot)

    try:
        await lifecycle.startup(container)
    except ConfigError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        await container.aclose()
        return 3

    # The notification delivery runs inside this same process (never a second
    # long-poller); it is stopped when the bot stops.
    sender = RichSender(bot)

    async def _send(admin_id: int, doc) -> None:
        await sender.send(admin_id, doc)

    async def _locale_for(admin_id: int) -> Locale:
        return settings.default_locale

    notify = NotificationDispatcher(
        container.services.notifier,
        admin_ids=lambda: sorted(settings.admin_ids),
        send=_send,
        locale_for=_locale_for,
    )
    poller = NotificationPoller(notify)

    async def _send_backup(chat_id: int, filename: str, data: bytes) -> None:
        await sender.send_document(chat_id, filename, data)

    # Scheduled backups have exactly one owner: the panel's cron tick. This
    # scheduler is the fallback for a bot-only deployment and is off unless the
    # operator opted in; either way the manual Telegram action still works.
    backup_scheduler = BackupScheduler(
        container.services.backup,
        admin_ids=lambda: sorted(settings.admin_ids),
        send=_send_backup,
        enabled=enabled_from_env(),
    )

    try:
        await app_module.set_commands(bot, settings)
        poller.start()
        backup_scheduler.start()
        await dispatcher.start_polling(bot)
    finally:
        await backup_scheduler.stop()
        await poller.stop()
        await lifecycle.shutdown(container)
    return 0


def run() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
