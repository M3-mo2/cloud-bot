# Keyboards Update in aiogram 3.31.0

In release **3.31.0** (published on August 26, 2026), keyboards received three long-awaited additions that give developers more precise control over button behavior and the reply interface.

## 1. `disabled` Field in `InlineKeyboardButton`

A new `disabled` field has been added to `aiogram.types.inline_keyboard_button.InlineKeyboardButton`.

- **Purpose:** When set to `True`, the button is still displayed in the inline keyboard but **does nothing** when pressed (it becomes inactive).
- **Benefit:** Allows you to create "disabled" buttons for display purposes only, without needing to remove or hide them.

## 2. `force_reply` Field in Keyboards

A new `force_reply` field has been added to both:

- `aiogram.types.inline_keyboard_markup.InlineKeyboardMarkup`
- `aiogram.types.reply_keyboard_markup.ReplyKeyboardMarkup`

- **Purpose:** When set to `True`, the interface shows the **Reply Interface** to the user directly, as if they manually selected the bot's message and pressed "Reply".
- **Benefit:** Forces the user to reply to a specific message smoothly, without needing to use a separate `ForceReply` object.

## 3. `LINK` Member in `ButtonStyle`

The `LINK` member has been added to the `aiogram.enums.button_style.ButtonStyle` enum.

- **Purpose:** When used, the callback button is displayed as a **Regular Link** instead of an interactive button.
- **Benefit:** Allows links to be embedded in inline keyboards with a consistent appearance alongside other buttons, while still opening links directly.

## Example

```python
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ButtonStyle

async def send_keyboard_example(bot: Bot, chat_id: int):
    # Disabled button
    disabled_btn = InlineKeyboardButton(
        text="Disabled button",
        callback_data="disabled_action",
        disabled=True  # Displayed but inactive
    )

    # Link-style button
    link_btn = InlineKeyboardButton(
        text="Visit website",
        url="https://example.com",
        style=ButtonStyle.LINK  # Displayed as a regular link
    )

    # Inline keyboard with force_reply
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[[disabled_btn, link_btn]],
        force_reply=True  # Show the reply interface with the keyboard
    )

    await bot.send_message(
        chat_id=chat_id,
        text="Choose one of the options:",
        reply_markup=inline_kb
    )
```

## Notes

- These features are available starting from version `3.31.0` only.
- Make sure to update the library: `pip install -U aiogram`.
- See the [official documentation](https://docs.aiogram.dev/en/v3.31.0/) for more details.
