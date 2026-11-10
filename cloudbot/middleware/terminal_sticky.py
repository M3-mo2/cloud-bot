"""Terminal input state — leaving it is automatic, on every path.

Opening a terminal *is* entering it (``term:open``/``term:new`` set the state), so
plain text reaches the session with no extra tap. That makes one invariant
load-bearing:

    the input state is set **if and only if** the user's last interaction was
    with a terminal.

Two middleware enforce the two ways an interaction can leave:

* :class:`TerminalStickyMiddleware` (messages) — a **known** slash-command (one a
  router handles, e.g. ``/start``, ``/help``, ``/settings``, ``/admin``) clears the
  state and does its own thing, so the user can always leave by command. An
  **unknown** command is deliberately left alone: the terminal router's message
  handler still catches it (the state is untouched) and re-renders the terminal.
* :class:`TerminalLeaveMiddleware` (callbacks) — **any** callback that is not a
  ``term:`` one clears the state. This is the one choke point for navigation: a
  user who taps any other screen has visually left the terminal, and their next
  plain-text message must not be swallowed into a session they are no longer
  looking at. Per-handler clearing was rejected: a single handler that forgets is a
  silent trap, and the failure only shows up as a message the user did not mean to
  send. One seam cannot miss one.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cloudbot.states.terminal_input import TerminalInput

log = structlog.get_logger("cloudbot.terminal_sticky")

#: The terminal states an interaction can leave. Only command mode is sticky now
#: (the resize state was retired with its screen in term26: no state ever awaits a
#: size any more).
_STICKY_STATES: frozenset[str] = frozenset(
    {TerminalInput.awaiting_command.state}
)

#: The callback-data namespace that stays inside the terminal. Every other prefix
#: leaves the input mode.
TERM_PREFIX = "term:"

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


class TerminalLeaveMiddleware(BaseMiddleware):
    """Leave the terminal input mode on **any** non-``term:`` callback.

    The state is set by opening a terminal and kept by ``term:`` interactions only.
    Every other callback is navigation to a different screen, so the state is
    cleared before the handler runs — see the module docstring for why this is one
    choke point rather than a rule each handler repeats.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        if (event.data or "").startswith(TERM_PREFIX):
            return await handler(event, data)
        state = data.get("state")
        if state is None:
            return await handler(event, data)
        if await state.get_state() not in _STICKY_STATES:
            return await handler(event, data)
        await state.clear()
        log.info("terminal.left_by_callback", data=(event.data or "")[:32])
        return await handler(event, data)
