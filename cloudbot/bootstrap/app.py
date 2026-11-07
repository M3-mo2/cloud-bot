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
from cloudbot.middleware.admin_gate import AdminGateMiddleware
from cloudbot.middleware.context_mw import ContextMiddleware
from cloudbot.middleware.error_guard import ErrorGuardMiddleware
from cloudbot.middleware.events_source import EventSourceMiddleware
from cloudbot.middleware.forcejoin import ForceJoinMiddleware
from cloudbot.middleware.inject import InjectMiddleware
from cloudbot.middleware.mode_gate import ModeGateMiddleware
from cloudbot.middleware.observability import ObservabilityMiddleware
from cloudbot.middleware.terminal_sticky import TerminalStickyMiddleware
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
    # The force-join gate needs the real Bot (``getChatMember``). It is bound here,
    # once the bot exists, and runs first in the chain so a gated user can never
    # reach any handler — a new handler cannot forget it.
    from cloudbot.adapters.telegram.directory import TelegramDirectory

    directory = TelegramDirectory(bot)
    container.services.forcejoin.bind(directory, getattr(bot, "id", 0))
    dispatcher.update.outer_middleware(ObservabilityMiddleware())
    dispatcher.update.outer_middleware(
        ContextMiddleware(
            container.services.account, settings, admin_state=container.admin_state
        )
    )
    dispatcher.update.outer_middleware(InjectMiddleware(container.services, sender))
    dispatcher.update.outer_middleware(ThrottleMiddleware(settings))
    dispatcher.update.outer_middleware(ModeGateMiddleware())
    # The event source must run outside the error guard so ``engine.unreachable``
    # intents recorded by the guard are flushed; the guard wraps the router so it
    # catches a DomainError raised anywhere below.
    dispatcher.update.middleware(EventSourceMiddleware(container.services.events))
    dispatcher.update.middleware(ErrorGuardMiddleware(sender))
    # Identity-based admin gate: one seam, on the message/callback observers (an
    # update-level middleware never sees a Message). A non-admin is swallowed here
    # so ``/admin`` and ``adm:`` callbacks reveal nothing.
    dispatcher.message.outer_middleware(AdminGateMiddleware())
    dispatcher.callback_query.outer_middleware(AdminGateMiddleware())
    # The mandatory-subscription gate. Registered on the message/callback
    # observers (an update-level middleware only sees the ``Update`` wrapper), and
    # **after** the admin gate so a non-admin's ``/admin`` is still swallowed
    # silently. It exempts ``/start`` and its own ``fj:`` re-check callbacks.
    dispatcher.message.outer_middleware(ForceJoinMiddleware())
    dispatcher.callback_query.outer_middleware(ForceJoinMiddleware())
    # A known slash-command must leave the sticky terminal state (R4). Registered
    # after the admin gate so the gate still decides identity first.
    dispatcher.message.outer_middleware(TerminalStickyMiddleware())

    from cloudbot.handlers import (
        admin,
        admin_panel,
        billing,
        callbacks,
        common,
        execution,
        features,
        files,
        forcejoin,
        terminal,
        workspaces,
    )

    dispatcher.include_router(common.router)
    # The gate's own re-check buttons: registered near the front so the two
    # ``fj:`` calls always reach their handler (they are gate-exempt too).
    dispatcher.include_router(forcejoin.router)
    # Billing before the account routers: the Stars handshake (an invoice, a
    # pre-checkout answer, a successful payment) is never user navigation.
    dispatcher.include_router(billing.router)
    dispatcher.include_router(callbacks.navigation.router)
    # Admin panel before the workspace/file routers so a ``/admin`` command and its
    # ``adm:`` callbacks are never mistaken for user navigation.
    dispatcher.include_router(admin_panel.router)
    dispatcher.include_router(workspaces.router)
    dispatcher.include_router(files.router)
    dispatcher.include_router(execution.router)
    dispatcher.include_router(features.router)
    dispatcher.include_router(admin.router)
    # Developer tools: a second, explicit opt-in on top of the admin gate. The
    # router is only attached when the switch is on, so no route exists at all in
    # production, and the gate is the outer seam that would refuse it anyway.
    if settings.dev_tools:
        from cloudbot.handlers import dev

        dispatcher.include_router(dev.router)
    # Terminal last: a slash-command in the terminal FSM must reach the command
    # routers first; plain text falls through to the terminal message handler.
    dispatcher.include_router(terminal.router)
    return dispatcher


async def set_commands(bot: Bot, settings: Settings) -> None:
    commands = [
        BotCommand(command="start", description="Open the menu"),
        BotCommand(command="settings", description="Language & preferences"),
        BotCommand(command="help", description="Help"),
        BotCommand(command="paysupport", description="Payment support"),
    ]
    await bot.set_my_commands(commands)
