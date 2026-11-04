"""Onboarding FSM state groups."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    choosing_language = State()
