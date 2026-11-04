"""Input-awaiting FSM state group for Phase 2 text prompts.

Separate from ``states/input.py`` on purpose, but the same rule holds: the FSM
stores only ids and raw user input — never rendered text. Each state is always
paired with visible buttons on the prompt screen so there is always a way out.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeatureInput(StatesGroup):
    awaiting_package_install = State()
    awaiting_package_remove = State()
    awaiting_port = State()
