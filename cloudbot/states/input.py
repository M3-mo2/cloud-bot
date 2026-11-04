"""Input-awaiting FSM state groups.

The FSM stores only state and raw user input — never rendered text.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Input(StatesGroup):
    waiting_name = State()
    waiting_profile = State()
    waiting_advanced = State()
    waiting_mkdir_path = State()
    waiting_rename_path = State()
    waiting_rename_same = State()
    waiting_upload = State()
    waiting_exec_command = State()
    waiting_job_command = State()
    waiting_job_args = State()
