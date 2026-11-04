# Colored Buttons in aiogram (Bot API 9.4+)

This guide explains how to add colors to keyboard buttons in aiogram using the `style` field. Button coloring was introduced in **Telegram Bot API 9.4** (February 9, 2026) and is available for both inline keyboards and custom reply keyboards.

## 1. Overview

Telegram clients that support Bot API 9.4 can render buttons with one of three predefined color styles. The style is specified via the `style` parameter, which accepts a string value defined by the `ButtonStyle` enum.

| Style Value | Color | Typical Use Case |
| :--- | :--- | :--- |
| `primary` | Blue | Main action, default option |
| `success` | Green | Confirmation, positive action |
| `danger` | Red | Destructive or critical action |

If `style` is omitted, the client uses its application-specific default style.

> **Note:** The `style` field is available on `InlineKeyboardButton`, `KeyboardButton`, and `RichMessageButton`. `RichMessageButton` additionally supports a `link` style.

## 2. The `ButtonStyle` Enum

aiogram provides the `aiogram.enums.button_style.ButtonStyle` enum for type-safe style selection.

```python
from aiogram.enums import ButtonStyle

print(ButtonStyle.PRIMARY)  # "primary"
print(ButtonStyle.SUCCESS)  # "success"
print(ButtonStyle.DANGER)   # "danger"
# ButtonStyle.LINK is also available for RichMessageButton
```

## 3. Coloring Inline Keyboard Buttons

Pass the `style` parameter to `InlineKeyboardButton`. It works alongside `callback_data`, `url`, or any other action field.

### Example: Three-Color Inline Keyboard

```python
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ButtonStyle

async def send_colored_inline_keyboard(bot: Bot, chat_id: int):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Confirm",
                    callback_data="confirm",
                    style=ButtonStyle.SUCCESS,  # Green
                ),
                InlineKeyboardButton(
                    text="Cancel",
                    callback_data="cancel",
                    style=ButtonStyle.DANGER,   # Red
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Learn More",
                    url="https://example.com",
                    style=ButtonStyle.PRIMARY,  # Blue
                ),
            ],
        ]
    )

    await bot.send_message(
        chat_id=chat_id,
        text="Choose an action:",
        reply_markup=keyboard,
    )
```

## 4. Coloring Reply Keyboard Buttons

The same `style` parameter is available on `KeyboardButton` for custom reply keyboards.

### Example: Colored Reply Keyboard

```python
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ButtonStyle

reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="Start",
                style=ButtonStyle.SUCCESS,  # Green
            ),
            KeyboardButton(
                text="Stop",
                style=ButtonStyle.DANGER,   # Red
            ),
        ],
        [
            KeyboardButton(
                text="Settings",
                style=ButtonStyle.PRIMARY,  # Blue
            ),
        ],
    ],
    resize_keyboard=True,
)
```

## 5. Important Constraints

*   **Client Support:** Colored buttons are rendered only in Telegram clients that support Bot API 9.4 or higher. Older clients will ignore the `style` field and display the default button style.
*   **Style + Custom Emoji:** `style` can be combined with `icon_custom_emoji_id` to add both a color and an icon to a button.
*   **Rich Message Buttons:** `RichMessageButton` also accepts `style`, including the extra `link` value (renders the button as a borderless link).

## 6. Quick Reference

| Object | Field | Allowed Values |
| :--- | :--- | :--- |
| `InlineKeyboardButton` | `style` | `"primary"`, `"success"`, `"danger"` |
| `KeyboardButton` | `style` | `"primary"`, `"success"`, `"danger"` |
| `RichMessageButton` | `style` | `"primary"`, `"success"`, `"danger"`, `"link"` |

## 7. References

*   **aiogram `InlineKeyboardButton` Documentation:** [https://docs.aiogram.dev/en/dev-3.x/api/types/inline_keyboard_button.html](https://docs.aiogram.dev/en/dev-3.x/api/types/inline_keyboard_button.html)
*   **aiogram `KeyboardButton` Documentation:** [https://docs.aiogram.dev/en/dev-3.x/api/types/keyboard_button.html](https://docs.aiogram.dev/en/dev-3.x/api/types/keyboard_button.html)
*   **aiogram `ButtonStyle` Enum:** [https://docs.aiogram.dev/en/v3.31.0/api/enums/button_style.html](https://docs.aiogram.dev/en/v3.31.0/api/enums/button_style.html)
*   **Telegram Bot API – February 9, 2026 Update (Bot API 9.4):** [https://core.telegram.org/bots/api#february-9-2026](https://core.telegram.org/bots/api#february-9-2026)
