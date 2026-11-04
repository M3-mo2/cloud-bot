"""Keyboard composition helpers."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from cloudbot.presentation.keyboards.buttons import reply


def inline_markup(
    rows: Sequence[Sequence[InlineKeyboardButton]], *, force_reply: bool = False
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[list(row) for row in rows], force_reply=force_reply
    )


def reply_markup(
    rows: Sequence[Sequence[KeyboardButton]],
    *,
    placeholder: str | None = None,
    force_reply: bool = False,
) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[list(row) for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=placeholder,
        force_reply=force_reply,
    )


def remove_reply() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
