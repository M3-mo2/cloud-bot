"""Keyboard composition helpers.

Only inline keyboards exist. ``remove_reply`` is the single, documented
exception: the one-time ``ReplyKeyboardRemove`` sent on ``/start`` to clear a
legacy persistent reply keyboard a user may still have installed (see
``docs/DECISIONS.md`` CB-25).
"""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove


def inline_markup(
    rows: Sequence[Sequence[InlineKeyboardButton]], *, force_reply: bool = False
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[list(row) for row in rows], force_reply=force_reply
    )


def remove_reply() -> ReplyKeyboardRemove:
    """The one-time migration button: clear a legacy reply keyboard."""
    return ReplyKeyboardRemove()
