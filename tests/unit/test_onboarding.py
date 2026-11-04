"""Onboarding / start screen: the welcome board carries its inline Start button.

Regression for the live bug where ``on_start`` built the welcome screen and then
sent it with ``markup=reply_menu(...)``, silently discarding ``screen.markup`` —
so the inline "Start" button never appeared. A rich message carries exactly one
reply_markup (a reply keyboard OR an inline one), so the empty welcome board must
keep its inline keyboard.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale


class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append({"html": rich_message.html, "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def __call__(self, method):
        return True


def _message(uid: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=uid,
        message=Message(
            message_id=uid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _callback(uid: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c",
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text="screen",
            ),
            data=data,
        ),
    )


def _inline_actions(markup) -> set[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return {b.callback_data for row in rows for b in row if b.callback_data}


def _container():
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    container = build(settings)
    return container, container.engine


async def test_first_start_shows_the_welcome_board_with_an_inline_start_button():
    container, _ = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _message(1, 111, "/start"))
        assert bot.sent, "no screen rendered"
        last = bot.sent[-1]
        actions = _inline_actions(last["markup"])
        assert "ws:new" in actions, "the Start button was dropped"
        assert "nav:help" in actions
        assert "cloud hosting" in last["html"].lower() or "استضافة سحابية" in last["html"]
    finally:
        await container.aclose()


async def test_returning_user_with_a_workspace_gets_the_persistent_reply_menu():
    container, engine = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.users.get_or_create(222, default_locale=Locale.EN)
        ws = (await engine.list_workspaces())[0]
        await container.users.add_workspace(222, ws.id)
        await dispatcher.feed_update(bot, _message(1, 222, "/start"))
        last = bot.sent[-1]
        # The menu send carries the reply keyboard, not the inline quick-actions.
        assert getattr(last["markup"], "keyboard", None), "reply menu missing"
        assert not _inline_actions(last["markup"])
    finally:
        await container.aclose()


async def test_language_switch_with_no_workspaces_keeps_the_inline_start_button():
    container, _ = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, 333, "lang:en"))
        # The most recent payload must still offer the inline Start action.
        actions = set()
        for entry in bot.sent:
            actions |= _inline_actions(entry["markup"])
        assert "ws:new" in actions, "language switch dropped the Start button"
    finally:
        await container.aclose()


async def test_menu_callback_with_no_workspaces_still_renders_the_welcome_board():
    container, _ = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, 444, "nav:menu"))
        assert "ws:new" in _inline_actions(bot.sent[-1]["markup"])
    finally:
        await container.aclose()
