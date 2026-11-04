"""Admin panel end-to-end: entered mode rendering + attribution (task §3.4)."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale

WS_OWNER = 6508129575
ADMIN = 777


class FakeBot:
    id = 42

    def __init__(self):
        self.sent: list[str] = []
        self.deleted: list[int] = []
        #: When set, every edit raises it (to simulate Telegram's refusal).
        self.raise_on_edit = None

    async def delete_message(self, chat_id, message_id):
        """RichSender.delete() calls this for the one-time carrier."""
        self.deleted.append(message_id)
        return True

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(rich_message.html)
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        if self.raise_on_edit is not None:
            raise self.raise_on_edit
        self.sent.append(getattr(rich_message, "html", ""))
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def __call__(self, method):
        # Answer any Bot API method the dispatcher's shortcut plumbing awaits
        # (e.g. AnswerCallbackQuery from ``cb.answer()``).
        return True


def _callback(uid: int, user_id: int, data: str) -> Update:
    msg = Message(
        message_id=uid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text="screen",
    )
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c",
            message=msg,
            data=data,
        ),
    )


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


async def _container_with_owner():
    settings = Settings(
        mode="production",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids=str(ADMIN),
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    engine = FakeEngine()
    container.engine = engine
    admin = container.services.admin
    admin._engine = engine
    # Seed the owner with a workspace.
    await container.users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    await container.users.set_display_name(WS_OWNER, "Mohamed")
    ws = (await engine.list_workspaces())[0]
    await container.users.add_workspace(WS_OWNER, ws.id)
    return container, ws


async def test_enter_workspace_shows_the_banner_and_leave():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # The admin enters the owner's workspace.
        await container.services.admin.enter_workspace(ADMIN, str(ws.id), WS_OWNER)
        await dispatcher.feed_update(bot, _callback(1, ADMIN, "adm:sec:workspace"))
        assert bot.sent, "no screen rendered"
        html = bot.sent[-1]
        assert "Admin mode" in html or "وضع المشرف" in html
        assert str(ws.id) in html
    finally:
        await container.aclose()


async def test_entered_action_is_logged_as_the_admin():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.services.admin.enter_workspace(ADMIN, str(ws.id), WS_OWNER)
        # Any interaction in entered mode is recorded as the admin, on the subject.
        await dispatcher.feed_update(bot, _message(1, ADMIN, "hello"))
        events = await container.event_log.events_after(0)
        admin_events = [e for e in events if e.category.value == "admin.action"]
        assert admin_events, "no admin.action logged"
        row = admin_events[-1]
        assert row.actor == f"admin:{ADMIN}"
        assert row.ws_id == str(ws.id)
        assert row.tg_id == WS_OWNER
    finally:
        await container.aclose()


async def test_leave_workspace_clears_entered_mode():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.services.admin.enter_workspace(ADMIN, str(ws.id), WS_OWNER)
        await dispatcher.feed_update(bot, _callback(1, ADMIN, "adm:leave"))
        assert await container.services.admin.entered(ADMIN) is None
        assert bot.sent and "Admin panel" in bot.sent[-1]
    finally:
        await container.aclose()


async def test_admin_suspend_action_affects_and_names_the_workspace():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Ask first (confirmation), then proceed.
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:ask:suspend:{ws.id}"))
        assert bot.sent and str(ws.id) in bot.sent[-1]
        await dispatcher.feed_update(bot, _callback(2, ADMIN, f"adm:suspend:{ws.id}"))
        refreshed = await container.engine.get_workspace(ws.id)
        assert refreshed.status.value == "suspended"
        events = await container.event_log.events_after(0)
        admin_events = [e for e in events if e.category.value == "admin.action"]
        assert admin_events[-1].actor == f"admin:{ADMIN}"
    finally:
        await container.aclose()


# -- Task 07 Bug A: a malformed/stale callback must not crash -----------------
async def test_short_audit_callback_renders_the_stale_screen_not_a_crash():
    """The exact live callback: ``adm:audit_user:<tg_id>`` — no page component,
    which used to raise ``IndexError`` and render nothing."""
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:audit_user:{WS_OWNER}"))
        assert bot.sent, "a malformed callback rendered nothing"
        assert "out of date" in bot.sent[-1].lower() or "reopen" in bot.sent[-1].lower()
    finally:
        await container.aclose()


async def test_unknown_admin_callback_renders_the_stale_screen():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, ADMIN, "adm:no_such_action:1:2"))
        assert bot.sent
        assert "out of date" in bot.sent[-1].lower() or "reopen" in bot.sent[-1].lower()
    finally:
        await container.aclose()


# -- Task 07 Bug B: resume then back to the owner page, same screen ----------
async def test_resume_then_back_to_owner_identical_screen_does_not_raise():
    container, ws = await _container_with_owner()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # The admin opens the owner's page — the very screen resume returns to.
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:user:{WS_OWNER}"))
        assert bot.sent and "Mohamed" in bot.sent[-1]

        # Telegram refuses the identical re-render; it must not escape.
        bot.raise_on_edit = TelegramBadRequest(
            method=None,  # type: ignore[arg-type]
            message=(
                "Bad Request: message is not modified: specified new message content "
                "and reply markup are exactly the same as a current content and reply markup"
            ),
        )
        await dispatcher.feed_update(bot, _callback(2, ADMIN, f"adm:resume:{ws.id}"))
        # Reached here without an exception: the intended state is already on screen.
        assert await container.services.admin.entered(ADMIN) is None
    finally:
        await container.aclose()

