"""Dispatcher assembly: routers, middleware, FSM storage.

The bot is started by the operator; this module never starts Telegram polling on
its own. ``python -m cloudbot`` is the documented start command.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import BotCommand

from cloudbot.core.config import Settings
from cloudbot.core.modes import persistence_fsm
from cloudbot.bootstrap.container import Container
from cloudbot.middleware.context_mw import ContextMiddleware
from cloudbot.middleware.error_guard import ErrorGuardMiddleware
from cloudbot.middleware.inject import InjectMiddleware
from cloudbot.middleware.mode_gate import ModeGateMiddleware
from cloudbot.middleware.observability import ObservabilityMiddleware
from cloudbot.middleware.throttle import ThrottleMiddleware
from cloudbot.presentation.rich.sender import RichSender

log = structlog.get_logger("cloudbot.app")


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(),
    )


def build_dispatcher(container: Container, bot: Bot) -> Dispatcher:
    settings = container.settings
    # DEV/DEBUG use memory FSM; PRODUCTION would use SQLite (kept memory here so
    # the process has no external FSM dependency in Phase 1).
    storage: BaseStorage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)

    sender = RichSender(bot)
    dispatcher.update.outer_middleware(ObservabilityMiddleware())
    dispatcher.update.outer_middleware(ContextMiddleware(container.services.account, settings))
    dispatcher.update.outer_middleware(InjectMiddleware(container.services, sender))
    dispatcher.update.outer_middleware(ThrottleMiddleware(settings))
    dispatcher.update.outer_middleware(ModeGateMiddleware())
    dispatcher.update.middleware(ErrorGuardMiddleware(sender))

    from cloudbot.handlers import admin, callbacks, common, execution, features, files, terminal, workspaces

    dispatcher.include_router(common.router)
    dispatcher.include_router(callbacks.navigation.router)
    dispatcher.include_router(workspaces.router)
    dispatcher.include_router(files.router)
    dispatcher.include_router(execution.router)
    dispatcher.include_router(features.router)
    dispatcher.include_router(admin.router)
    # Terminal last: a slash-command in the terminal FSM must reach the command
    # routers first; plain text falls through to the terminal message handler.
    dispatcher.include_router(terminal.router)
    return dispatcher


async def set_commands(bot: Bot, settings: Settings) -> None:
    commands = [
        BotCommand(command="start", description="Open the menu"),
        BotCommand(command="settings", description="Language & preferences"),
        BotCommand(command="help", description="Help"),
    ]
    await bot.set_my_commands(commands)
