"""Moderation: user bans (middleware), workspace bans (service boundary), messaging.

The tests prove the boundaries, not the buttons:

* a banned user is refused by a **middleware**, so a representative handler
  (``/start``) never renders its screen;
* a temporary ban expires by time, with no manual unban;
* an admin is never bannable (the documented anti-lockout choice);
* a banned workspace refuses execution **at the service layer**, with no UI;
* the owner cannot lift a workspace ban, an admin can;
* messaging reports the **real** Telegram outcome, including the refusal path;
* each mutation appends exactly one audit entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import CannotBanAdmin, WorkspaceBanned
from cloudbot.core.locale import Locale
from cloudbot.domain.moderation import DeliveryResult
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.presentation.mapping.delivery import deliver
from cloudbot.services.moderation import ModerationService

WS_OWNER = 6508129575
ADMIN = 777
NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)

class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(rich_message.html or "")
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append(getattr(rich_message, "html", ""))
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def __call__(self, method):
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

async def _container():
    settings = Settings(
        mode="production",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids=str(ADMIN),
        # The real panel flow sends several updates in a row; the throttle is a
        # production edge guard, not what these tests are about.
        throttle_rate=1000.0,
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    engine = FakeEngine()
    container.engine = engine
    container.services.admin._engine = engine
    await container.users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    await container.users.set_display_name(WS_OWNER, "Mohamed")
    ws = (await engine.list_workspaces())[0]
    await container.users.add_workspace(WS_OWNER, ws.id)
    return container, engine, ws

async def _admin_events(container) -> list:
    events = await container.event_log.events_after(0)
    return [e for e in events if e.category.value == "admin.action"]

# -- 1. the middleware refuses a banned user everywhere ----------------------
async def test_banned_user_is_refused_by_the_middleware_before_any_handler():
    container, engine, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Mark the user as banned through the domain service.
        await container.services.moderation.ban_user(
            WS_OWNER, reason="Abuse", banned_by=ADMIN
        )
        await dispatcher.feed_update(bot, _message(1, WS_OWNER, "/start"))
        # The one refusal screen, with the admin's reason — and NOT the menu, so
        # the /start handler never ran.
        html = bot.sent[-1]
        assert "You are banned" in html
        assert "Abuse" in html
        assert "Workspaces" not in html and "Main menu" not in html
    finally:
        await container.aclose()

async def test_a_temporary_ban_expires_by_time_without_an_unban():
    container, engine, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        moderation = container.services.moderation
        # A one-day ban that already ended: allowed with no manual unban.
        await moderation.ban_user(
            WS_OWNER,
            reason="Temporary",
            banned_by=ADMIN,
            days=1,
            now=datetime.now(timezone.utc) - timedelta(days=2),
        )
        await dispatcher.feed_update(bot, _message(1, WS_OWNER, "/start"))
        html = bot.sent[-1]
        assert "You are banned" not in html
        assert moderation.active_ban(await moderation.user_ban(WS_OWNER)) is None
    finally:
        await container.aclose()

async def test_temporary_ban_refusal_names_the_end():
    container, engine, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.services.moderation.ban_user(
            WS_OWNER,
            reason="Slow down",
            banned_by=ADMIN,
            days=7,
            now=datetime.now(timezone.utc),
        )
        await dispatcher.feed_update(bot, _message(1, WS_OWNER, "/start"))
        html = bot.sent[-1]
        assert "You are banned" in html
        # The ban ends at a concrete date (the "until" sentence is present).
        assert "The ban ends" in html
    finally:
        await container.aclose()

async def test_an_admin_is_never_bannable():
    from cloudbot.adapters.persistence.database import Database
    from cloudbot.adapters.persistence.sqlite_moderation import SqliteModerationStore

    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    try:
        moderation = ModerationService(SqliteModerationStore(db), protected_ids={ADMIN})
        with pytest.raises(CannotBanAdmin):
            await moderation.ban_user(ADMIN, reason="x", banned_by=ADMIN)
        assert await moderation.user_ban(ADMIN) is None
    finally:
        await db.close()

# -- 2. a workspace ban refuses execution at the service layer --------------
async def test_banned_workspace_refuses_execution_at_the_service_layer(services, users, engine):
    await users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await users.add_workspace(WS_OWNER, ws.id)
    await services.moderation.ban_workspace(ws.id, reason="Abuse", banned_by=ADMIN)

    # A direct service call — no handler, no button, no UI.
    with pytest.raises(WorkspaceBanned) as raised:
        await services.execution.run_command(WS_OWNER, ws.id, ["echo", "hi"])
    assert raised.value.reason == "Abuse"
    with pytest.raises(WorkspaceBanned):
        await services.execution.exec(WS_OWNER, ws.id, "echo hi")
    with pytest.raises(WorkspaceBanned):
        await services.execution.run_file(WS_OWNER, ws.id, "/main.py")
    with pytest.raises(WorkspaceBanned):
        await services.sessions.open(WS_OWNER, ws.id)

async def test_the_owner_cannot_lift_a_workspace_ban_but_an_admin_can(services, users, engine):
    await users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await users.add_workspace(WS_OWNER, ws.id)
    await services.moderation.ban_workspace(ws.id, reason="Abuse", banned_by=ADMIN)

    with pytest.raises(WorkspaceBanned):
        await services.workspaces.resume(WS_OWNER, ws.id)

    # The admin lifts it through the panel's service.
    await services.admin.unban_workspace(ws.id)
    await services.workspaces.resume(WS_OWNER, ws.id)
    assert (await engine.get_workspace(ws.id)).status.value == "active"

async def test_a_banned_workspace_reads_as_locked_with_the_reason(services, users, engine):
    await users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await users.add_workspace(WS_OWNER, ws.id)
    await services.moderation.ban_workspace(ws.id, reason="Abuse", banned_by=ADMIN)
    listed = await services.workspaces.list(WS_OWNER)
    assert listed and listed[0].status.value == "locked"
    assert listed[0].ban_reason == "Abuse"

# -- 3. messaging reports the real outcome ----------------------------------
class _SenderOk:
    def __init__(self, message_id: int = 99) -> None:
        self.message_id = message_id

    async def send(self, chat_id, doc, **kwargs):
        return Message.model_construct(chat={"id": chat_id}, message_id=self.message_id)

class _SenderBlocked:
    async def send(self, chat_id, doc, **kwargs):
        raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")

async def test_messaging_reports_delivery_with_the_message_id():
    result = await deliver(_SenderOk(99), 6508129575, "hello")
    assert result.delivered is True
    assert result.message_id == 99
    assert result.error == ""

async def test_messaging_reports_telegrams_real_refusal():
    result = await deliver(_SenderBlocked(), 6508129575, "hello")
    assert result.delivered is False
    assert result.error == "forbidden"
    assert result.message_id is None

# -- 4. audit: exactly one entry per mutation -------------------------------
async def test_ban_unban_ban_workspace_and_message_append_one_audit_entry_each():
    container, engine, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Ban the user (reason step, then the ban button).
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:user_ban:{WS_OWNER}"))
        await dispatcher.feed_update(bot, _message(2, ADMIN, "Abuse"))
        await dispatcher.feed_update(bot, _callback(3, ADMIN, f"adm:ban_go:user:{WS_OWNER}:0"))
        events = await _admin_events(container)
        assert [e for e in events if e.params.get("action") == "user_ban"], "ban not logged"
        assert len([e for e in events if e.params.get("action") == "user_ban"]) == 1

        # Unban.
        await dispatcher.feed_update(bot, _callback(4, ADMIN, f"adm:user_unban:{WS_OWNER}"))
        events = await _admin_events(container)
        assert len([e for e in events if e.params.get("action") == "user_unban"]) == 1

        # Ban the workspace.
        await dispatcher.feed_update(bot, _callback(5, ADMIN, f"adm:ws_ban:{ws.id}"))
        await dispatcher.feed_update(bot, _message(6, ADMIN, "Storage abuse"))
        await dispatcher.feed_update(bot, _callback(7, ADMIN, f"adm:ban_go:ws:{ws.id}:0"))
        events = await _admin_events(container)
        assert len([e for e in events if e.params.get("action") == "ws_ban"]) == 1

        # Message the user.
        await dispatcher.feed_update(bot, _callback(8, ADMIN, f"adm:user_msg:{WS_OWNER}"))
        await dispatcher.feed_update(bot, _message(9, ADMIN, "Please review the rules"))
        events = await _admin_events(container)
        messages = [e for e in events if e.params.get("action") == "user_message"]
        assert len(messages) == 1
        assert messages[0].params.get("delivered") is True
    finally:
        await container.aclose()

async def test_message_refusal_is_logged_honestly():
    """A refused message logs delivered=False and the real code — no fake success."""
    container, engine, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        original = bot.send_rich_message

        async def blocked(chat_id, rich_message, **kwargs):
            # Only the message to the *target* is refused; the panel's own result
            # screen to the admin still goes through.
            if chat_id == WS_OWNER:
                raise TelegramForbiddenError(
                    method=None, message="Forbidden: bot was blocked by the user"
                )
            return await original(chat_id, rich_message, **kwargs)

        bot.send_rich_message = blocked  # type: ignore[assignment]
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:user_msg:{WS_OWNER}"))
        await dispatcher.feed_update(bot, _message(2, ADMIN, "hello"))
        events = await _admin_events(container)
        messages = [e for e in events if e.params.get("action") == "user_message"]
        assert len(messages) == 1
        assert messages[0].params.get("delivered") is False
        assert messages[0].params.get("error") == "forbidden"
    finally:
        await container.aclose()

# -- 5. screen/callback shape ------------------------------------------------
def test_moderation_callbacks_are_at_most_64_bytes():
    from cloudbot.presentation.screens.admin import callbacks as admin_cb

    samples = [
        admin_cb.user_ban(6508129575),
        admin_cb.user_unban(6508129575),
        admin_cb.user_msg(6508129575),
        admin_cb.ban_go("user", "6508129575", 0),
        admin_cb.ws_ban("ws_01M6HJASCWM9Z1PF755GT89NK4"),
        admin_cb.ws_unban("ws_01M6HJASCWM9Z1PF755GT89NK4"),
    ]
    for data in samples:
        assert len(data.encode("utf-8")) <= 64
        assert admin_cb.parse(data).valid

def test_moderation_table_is_at_most_three_columns(ar, en):
    from cloudbot.core.locale import Locale
    from cloudbot.presentation.catalog import build
    from cloudbot.devtools import screens as screen_tools

    for locale in (Locale.EN, Locale.AR):
        screen = build("admin_moderation", locale)
        assert screen_tools.table_columns(screen.doc.html) <= 3
        html = screen.doc.html
        from cloudbot.presentation.i18n.formatters import has_arabic_digits

        assert not has_arabic_digits(html), "Arabic-Indic digit in a moderation screen"
