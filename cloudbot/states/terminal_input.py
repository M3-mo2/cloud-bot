"""Terminal-input FSM state groups (Phase 1.5).

Separate from ``states/input.py`` on purpose: opening a terminal **is** entering
it, and this state is always paired with the visible terminal buttons so there is
always a way out. The FSM stores only ids and raw text, never rendered content.

Invariant: ``awaiting_command`` is set **if and only if** the user's last
interaction was with a terminal. ``term:open``/``term:new`` set it; a known
slash-command and any non-``term:`` callback clear it (see
``middleware/terminal_sticky.py``), and ``⬅️ Back`` clears it explicitly.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TerminalInput(StatesGroup):
    awaiting_command = State()
