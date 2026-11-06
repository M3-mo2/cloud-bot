"""Input-awaiting FSM states for the admin panel.

Same rule as the rest of the bot: the FSM stores only raw input; each state is
paired with visible buttons on the prompt screen so there is always a way out.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminInput(StatesGroup):
    awaiting_user_search = State()
    #: A wallet mutation waiting for its number (Stars / days).
    awaiting_wallet_amount = State()
    #: A wallet mutation's optional reason (``/skip`` leaves it empty).
    awaiting_wallet_reason = State()
    #: The charge id to refund.
    awaiting_wallet_charge = State()
