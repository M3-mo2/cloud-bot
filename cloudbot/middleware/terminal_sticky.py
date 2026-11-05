"""Sticky terminal input — a slash-command must never be swallowed.

Entering the terminal's command mode is **sticky**: once the state is set, plain
text keeps going to the session (R1). That makes one rule load-bearing (R4): a
slash-command is *never* terminal input. A **known** command (one a router
handles, e.g. ``/start``, ``/help``, ``/settings``, ``/admin``) must clear the
sticky state and do its own thing, so the user can always leave by command as
well as by the "stop typing" button.

An **unknown** command is deliberately left alone: the terminal router's message
handler still catches it (the state is untouched) and simply re-renders the
terminal, so an unknown slash command does not silently vanish either.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from cloudbot.states.terminal_input import TerminalInput

log = structlog.get_logger("cloudbot.terminal_sticky")

#: The terminal states a slash-command leaves behind. Both are explicit modes
#: entered by a button; a command in either must return the user to the normal
#: flow rather than being parsed as a command line or a PTY size.
_STICKY_STATES: frozenset[str] = frozenset(
    {TerminalInput.awaiting_command.state, TerminalInput.awaiting_resize.state}
)

#: Commands a router handles while a terminal state is active. A command here
#: clears the sticky state before dispatch; the router then renders its own
#: screen. Kept explicit (not derived) because aiogram has no global command
#: registry; an unknown command is intentionally not listed so it still reaches
#: the terminal handler and re-renders the terminal.
LEAVE_COMMANDS: frozenset[str] = frozenset(
    {
        "/start",
        "/help",
        "/settings",
        # admin panel
        "/admin",
        "/notifications",
        # DEV/DEBUG diagnostics (mode-gated, but still a way out)
        "/diag",
        "/health",
        "/raw",
        "/mode",
        "/replay",
    }
)


def _command_of(text: str) -> str:
    return text.split()[0].split("@")[0].lower()


class TerminalStickyMiddleware(BaseMiddleware):
    """Clear the sticky terminal state for a known slash-command."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.text:
            return await handler(event, data)
        if not event.text.startswith("/"):
            return await handler(event, data)
        state = data.get("state")
        if state is None:
            return await handler(event, data)
        current = await state.get_state()
        if current not in _STICKY_STATES:
            return await handler(event, data)
        command = _command_of(event.text)
        if command in LEAVE_COMMANDS:
            await state.clear()
            log.info("terminal.sticky_cleared_by_command", command=command)
        return await handler(event, data)
