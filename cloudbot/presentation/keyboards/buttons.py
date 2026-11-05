"""Button factories: inline, with styles and disabled.

``inline(..., disabled=True)`` builds a ``DisabledButton`` instance itself; a raw
``True`` would raise ``ValidationError`` (measured against aiogram 3.31.0).

There is no reply-button factory: the bot has no reply keyboard anywhere. Every
button is an inline button.
"""

from __future__ import annotations

from aiogram.types import DisabledButton, InlineKeyboardButton

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
        # No style fallback: "link" is not a value Telegram accepts (it refuses the whole
        # message). A URL button is visually distinct already. See styles.ALLOWED_STYLES.
        return InlineKeyboardButton(text=text, url=url, style=style)
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
