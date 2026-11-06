"""Input-awaiting FSM state groups.

The FSM stores only state and raw user input — never rendered text.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Input(StatesGroup):
    waiting_name = State()
    #: The create flow between the name and the plan picker. Deliberately has no
    #: message handler: stray text must not restart the wizard or become a name.
    creating = State()
    waiting_mkdir_path = State()
    waiting_rename_path = State()
    waiting_rename_same = State()
    waiting_upload = State()
    waiting_exec_command = State()
    waiting_job_command = State()
    waiting_job_args = State()
