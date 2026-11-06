"""Terminal-input FSM state groups (Phase 1.5).

Separate from ``states/input.py`` on purpose: entering a terminal is an explicit
action, and these states are always paired with the visible terminal buttons so
there is always a way out. The FSM stores only ids and raw text, never rendered
content.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TerminalInput(StatesGroup):
    awaiting_command = State()
