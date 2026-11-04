"""The intent → style convention.

Screens declare an **intent**, never a color. ``style_for`` maps intent to one of
the four allowed ``ButtonStyle`` values, validated locally because
``InlineKeyboardButton.style`` is typed ``str | None`` and a typo would not raise.
"""

from __future__ import annotations

from enum import Enum

from aiogram.enums import ButtonStyle

#: The only style strings Telegram accepts; a typo is caught here, not at Telegram.
ALLOWED_STYLES: frozenset[str] = frozenset(
    {
        ButtonStyle.PRIMARY.value,
        ButtonStyle.SUCCESS.value,
        ButtonStyle.DANGER.value,
        ButtonStyle.LINK.value,
    }
)


class Intent(str, Enum):
    PRIMARY = ButtonStyle.PRIMARY.value  # navigate / neutral / default
    SUCCESS = ButtonStyle.SUCCESS.value  # confirm / start / positive
    DANGER = ButtonStyle.DANGER.value  # delete / stop / destructive
    LINK = ButtonStyle.LINK.value  # external URL
    MUTED = "muted"  # shown, inert → disabled button


#: Backwards-compatible alias (docs use both names).
Intent.DISABLED = Intent.MUTED  # type: ignore[attr-defined]


class InvalidIntent(ValueError):
    pass


def style_for(intent: Intent | str) -> str | None:
    """Return the validated Telegram style string, or ``None`` for MUTED."""
    if isinstance(intent, Intent) and intent is Intent.MUTED:
        return None
    if isinstance(intent, Intent):
        value = intent.value
    else:
        value = str(intent)
    if value == "muted":
        return None
    if value not in ALLOWED_STYLES:
        raise InvalidIntent(f"{value!r} is not an allowed ButtonStyle")
    return value


def is_muted(intent: Intent) -> bool:
    return intent is Intent.MUTED
