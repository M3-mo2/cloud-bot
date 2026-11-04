"""RichSender: the single outbound gateway and the edit-suppression signature.

Bug B (TASK 07) was live: resuming a workspace then returning to the owner page
re-rendered the identical screen, Telegram refused the edit with
``message is not modified``, and the exception escaped as an unhandled error. The
old guard short-circuited only when ``markup is None``, so any screen carrying
buttons bypassed it. These tests pin the full-payload signature and the
``not modified`` → success translation.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from cloudbot.core.locale import Locale
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.drafted: list[dict] = []
        self.raise_on_edit: Exception | None = None

    async def send_rich_message(self, **kwargs: Any):
        self.sent.append(kwargs)
        return _Message(kwargs["chat_id"], 1)

    async def edit_message_text(self, **kwargs: Any):
        if self.raise_on_edit is not None:
            raise self.raise_on_edit
        self.edited.append(kwargs)
        return _Message(kwargs["chat_id"], kwargs["message_id"])

    async def send_rich_message_draft(self, **kwargs: Any):
        self.drafted.append(kwargs)
        return True


class _Message:
    def __init__(self, chat_id: int, message_id: int) -> None:
        self.chat = type("C", (), {"id": chat_id})()
        self.message_id = message_id


def _doc(locale: Locale) -> RichDoc:
    d = RichDoc(locale)
    d.h(2, "Hi")
    return d


def _markup(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data="adm:menu")]]
    )


def _not_modified() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=None,  # type: ignore[arg-type]
        message=(
            "Bad Request: message is not modified: specified new message content and "
            "reply markup are exactly the same as a current content and reply markup"
        ),
    )


async def test_send_uses_send_rich_message():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.send(5, _doc(Locale.EN))
    assert bot.sent[0]["chat_id"] == 5
    assert bot.sent[0]["rich_message"].html == "<h2>Hi</h2>"


async def test_edit_uses_edit_message_text_with_rich_message():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.AR))
    call = bot.edited[0]
    assert call["rich_message"].is_rtl is True
    assert call["message_id"] == 9


async def test_draft_uses_send_rich_message_draft():
    bot = FakeBot()
    sender = RichSender(bot)
    ok = await sender.draft(5, 77, _doc(Locale.EN), can_stop=True)
    assert ok is True
    assert bot.drafted[0]["draft_id"] == 77
    assert bot.drafted[0]["can_stop"] is True


async def test_identical_edits_are_suppressed():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.EN), markup=None)
    await sender.edit(5, 9, _doc(Locale.EN), markup=None)
    assert len(bot.edited) == 1  # second identical edit short-circuited


async def test_changed_edit_passes_through():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.EN))
    d = RichDoc(Locale.EN)
    d.h(2, "Different")
    await sender.edit(5, 9, d)
    assert len(bot.edited) == 2


async def test_same_text_with_a_different_markup_is_still_sent():
    """The old guard (`markup is None`) skipped every button-carrying screen. A
    changed keyboard with unchanged text must reach Telegram."""
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("B"))
    assert len(bot.edited) == 2, "a changed keyboard was suppressed"


async def test_identical_text_and_markup_is_suppressed():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
    assert len(bot.edited) == 1


async def test_a_changed_rtl_flag_is_still_sent():
    bot = FakeBot()
    sender = RichSender(bot)
    await sender.edit(5, 9, _doc(Locale.EN))
    await sender.edit(5, 9, _doc(Locale.AR))  # same body, different is_rtl
    assert len(bot.edited) == 2


async def test_not_modified_is_treated_as_success():
    """The authoritative defence: the cache is empty after a restart, so the
    exception must be caught and treated as the intended state."""
    bot = FakeBot()
    sender = RichSender(bot)
    bot.raise_on_edit = _not_modified()
    result = await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
    assert result.message_id == 9
    # And the payload is now cached, so a repeat is short-circuited.
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
    assert bot.edited == []


async def test_a_clear_cache_still_survives_not_modified():
    """A fresh sender (i.e. after a restart) has no cache entry, yet the exact
    live path must not raise."""
    bot = FakeBot()
    sender = RichSender(bot)
    bot.raise_on_edit = _not_modified()
    await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))  # must not raise


async def test_a_different_bad_request_still_propagates():
    bot = FakeBot()
    sender = RichSender(bot)
    bot.raise_on_edit = TelegramBadRequest(
        method=None,  # type: ignore[arg-type]
        message="Bad Request: chat not found",
    )
    with pytest.raises(TelegramBadRequest):
        await sender.edit(5, 9, _doc(Locale.EN), markup=_markup("A"))
