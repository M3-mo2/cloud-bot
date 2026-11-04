"""Onboarding / start screen: the welcome board carries its inline Start button.

The bot has no reply keyboard. ``/start`` for a returning user sends exactly one
one-time ``ReplyKeyboardRemove`` (clearing a legacy persistent reply menu from an
older build) and then the menu, whose navigation lives in the inline nav block.
The empty welcome board keeps its inline keyboard.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardRemove, Update, User

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


def _reply_keyboard_sends(sent: list[dict]) -> list:
    return [e["markup"] for e in sent if getattr(e["markup"], "keyboard", None) is not None]


def _removal_sends(sent: list[dict]) -> list:
    return [e for e in sent if isinstance(e["markup"], ReplyKeyboardRemove)]


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


async def test_returning_user_with_a_workspace_gets_the_inline_menu_and_one_removal():
    container, engine = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.users.get_or_create(222, default_locale=Locale.EN)
        ws = (await engine.list_workspaces())[0]
        await container.users.add_workspace(222, ws.id)
        await dispatcher.feed_update(bot, _message(1, 222, "/start"))
        # No reply keyboard is ever installed.
        assert _reply_keyboard_sends(bot.sent) == [], "a reply keyboard was sent"
        # Exactly one migration removal, and the menu carries the inline nav block.
        assert len(_removal_sends(bot.sent)) == 1, "expected exactly one ReplyKeyboardRemove"
        assert "ws:new" in _inline_actions(bot.sent[-1]["markup"])
        assert "nav:settings" in _inline_actions(bot.sent[-1]["markup"])
    finally:
        await container.aclose()


async def test_the_removal_fires_only_on_start_not_on_other_screens():
    container, engine = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.users.get_or_create(222, default_locale=Locale.EN)
        ws = (await engine.list_workspaces())[0]
        await container.users.add_workspace(222, ws.id)
        # /start fires it once.
        await dispatcher.feed_update(bot, _message(1, 222, "/start"))
        assert len(_removal_sends(bot.sent)) == 1
        # Other commands and a nav callback do not.
        await dispatcher.feed_update(bot, _message(2, 222, "/help"))
        await dispatcher.feed_update(bot, _message(3, 222, "/settings"))
        await dispatcher.feed_update(bot, _callback(4, 222, "nav:menu"))
        assert len(_removal_sends(bot.sent)) == 1, "the removal leaked onto another screen"
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


async def test_former_reply_button_texts_are_now_ordinary_and_do_nothing():
    container, _ = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        stale = ["📦 المساحات", "⚙️ الإعدادات", "🌐 اللغة", "❓ مساعدة",
                 "📦 Workspaces", "⚙️ Settings", "🌐 Language", "❓ Help"]
        for uid, text in enumerate(stale, start=1):
            # A plain message equal to the old reply-button label must not crash
            # and must not trigger the special screen it used to open.
            await dispatcher.feed_update(bot, _message(uid, 555, text))
        assert bot.sent == [], "a former reply-button text still opened a screen"
    finally:
        await container.aclose()
