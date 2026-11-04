"""Button factories: inline and reply, with styles, disabled and force_reply.

``inline(..., disabled=True)`` builds a ``DisabledButton`` instance itself; a raw
``True`` would raise ``ValidationError`` (measured against aiogram 3.31.0).
"""

from __future__ import annotations

from aiogram.types import DisabledButton, InlineKeyboardButton, KeyboardButton

from cloudbot.core.errors import DomainError
from cloudbot.presentation.keyboards.styles import Intent, is_muted, style_for


class CallbackTooLong(DomainError):
    code = "callback_too_long"


#: Telegram's hard cap on callback_data.
MAX_CALLBACK_BYTES = 64


def validate_callback(action: str) -> str:
    if len(action.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise CallbackTooLong(action)
    return action


def inline(
    text: str,
    action: str | None = None,
    *,
    intent: Intent = Intent.PRIMARY,
    disabled: bool = False,
    url: str | None = None,
) -> InlineKeyboardButton:
    style = style_for(intent)
    if url is not None:
        return InlineKeyboardButton(text=text, url=url, style=style or "link")
    if action is None:
        action = "nav:noop"
    action = validate_callback(action)
    disabled_obj = DisabledButton() if (disabled or is_muted(intent)) else None
    return InlineKeyboardButton(
        text=text,
        callback_data=action,
        style=style,
        disabled=disabled_obj,
    )


def reply(text: str, *, intent: Intent = Intent.PRIMARY) -> KeyboardButton:
    style = style_for(intent)
    return KeyboardButton(text=text, style=style)
