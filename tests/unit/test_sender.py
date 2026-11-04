"""RichSender: the single outbound gateway and the edit-suppression cache."""

from __future__ import annotations

from typing import Any

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.drafted: list[dict] = []

    async def send_rich_message(self, **kwargs: Any):
        self.sent.append(kwargs)
        return _Message(kwargs["chat_id"], 1)

    async def edit_message_text(self, **kwargs: Any):
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
