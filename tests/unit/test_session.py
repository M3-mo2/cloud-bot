"""Task 10 — the persisted workspace session.

The central decision is **column, not FSM**: the session must survive a restart,
a redeploy and a new process, so it is a column on ``users`` and these tests
prove it end to end (including over a real on-disk database reopened by a second
``Database`` instance).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import NotOwned
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.session import SessionService

TG = 777
FOREIGN_WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


# -- adapter + port ----------------------------------------------------------
async def test_repository_roundtrip_set_and_current(users):
    ws = WorkspaceId("ws_abc")
    await users.get_or_create(TG, default_locale=Locale.EN)
    assert await users.current_workspace(TG) is None
    await users.set_current_workspace(TG, ws)
    assert await users.current_workspace(TG) == ws
    await users.set_current_workspace(TG, None)
    assert await users.current_workspace(TG) is None


async def test_repository_set_is_idempotent_and_replaces(users):
    await users.get_or_create(TG, default_locale=Locale.EN)
    a, b = WorkspaceId("ws_a"), WorkspaceId("ws_b")
    await users.set_current_workspace(TG, a)
    await users.set_current_workspace(TG, a)
    assert await users.current_workspace(TG) == a
    await users.set_current_workspace(TG, b)
    assert await users.current_workspace(TG) == b


# -- ownership on enter ------------------------------------------------------
async def test_enter_refuses_foreign_and_nonexistent_identically(services, engine):
    await services.account.get_or_create(TG, default_locale=Locale.EN)
    # A foreign workspace the user does not own (exists in the engine).
    with pytest.raises(NotOwned):
        await services.session.enter(TG, FOREIGN_WS)
    # A nonexistent workspace id: refused the same way, indistinguishably.
    with pytest.raises(NotOwned):
        await services.session.enter(TG, WorkspaceId("ws_does_not_exist"))
    # Nothing was persisted for either attempt.
    assert await services.session.current(TG) is None


async def test_enter_persists_when_owned(services, users):
    await users.get_or_create(TG, default_locale=Locale.EN)
    ws = await services.workspaces.create(TG, "mine", plan_code="trial")
    await services.session.enter(TG, ws.id)
    assert await users.current_workspace(TG) == ws.id


# -- self-healing ------------------------------------------------------------
async def test_current_self_heals_when_workspace_deleted(services, users):
    await users.get_or_create(TG, default_locale=Locale.EN)
    ws = await services.workspaces.create(TG, "doomed", plan_code="trial")
    await services.session.enter(TG, ws.id)
    assert await services.session.current(TG) is not None
    # Delete the workspace after entering (as the user or the admin panel would).
    await services.workspaces.delete(TG, ws.id)
    # No raise; the stale id is cleared and "not inside" is returned.
    assert await services.session.current(TG) is None
    assert await users.current_workspace(TG) is None


async def test_current_self_heals_when_engine_lost_it(services, users, engine):
    """Ownership row survives, but the engine no longer has the workspace."""
    await users.get_or_create(TG, default_locale=Locale.EN)
    ws = await services.workspaces.create(TG, "vanished", plan_code="trial")
    await services.session.enter(TG, ws.id)
    await engine.delete_workspace(ws.id)  # engine-side delete, row still present
    assert await services.session.current(TG) is None
    assert await users.current_workspace(TG) is None
    assert not await users.owns(TG, ws.id)  # ownership reconciled too


async def test_leave_is_idempotent(services, users):
    await users.get_or_create(TG, default_locale=Locale.EN)
    # Leaving while not inside is a success, not an error.
    await services.session.leave(TG)
    ws = await services.workspaces.create(TG, "mine", plan_code="trial")
    await services.session.enter(TG, ws.id)
    await services.session.leave(TG)
    await services.session.leave(TG)  # again: still a success
    assert await services.session.current(TG) is None


# -- survives a process restart ----------------------------------------------
async def test_session_survives_a_process_restart(tmp_path):
    """The column-not-FSM proof: a second Database over the same file still finds
    the user inside."""
    path = str(tmp_path / "session.db")

    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.connect()
    await db.migrate()
    repo = SqliteUserRepository(db)
    engine = FakeEngine()
    svc = SessionService(repo, engine, OwnershipService(repo))
    await repo.get_or_create(TG, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await repo.add_workspace(TG, ws.id)
    await svc.enter(TG, ws.id)
    await db.close()

    # A brand-new connection/process over the same file.
    db2 = Database(f"sqlite+aiosqlite:///{path}")
    await db2.connect()
    await db2.migrate()
    repo2 = SqliteUserRepository(db2)
    engine2 = FakeEngine()
    svc2 = SessionService(repo2, engine2, OwnershipService(repo2))
    current = await svc2.current(TG)
    assert current is not None
    assert current.id == ws.id
    await db2.close()


# -- handler-level behaviour -------------------------------------------------
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.deleted: list[dict] = []

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append({"html": rich_message.html, "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def delete_message(self, chat_id, message_id):
        """RichSender.delete() calls this for the one-time keyboard-removal carrier."""
        self.deleted.append({"chat_id": chat_id, "message_id": message_id})
        return True

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


def _actions(markup) -> set[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return {b.callback_data for row in rows for b in row if b.callback_data}


def _has_action(markup, prefix: str) -> bool:
    return any(a == prefix or a.startswith(prefix + ":") for a in _actions(markup))


def _container():
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    return build(settings)


async def test_start_while_inside_renders_the_workspace_not_the_chooser():
    container = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
        await container.services.session.enter(TG, ws.id)
        await dispatcher.feed_update(bot, _message(1, TG, "/start"))
        html = bot.sent[-1]["html"]
        # The workspace home, not the chooser / welcome board.
        assert ws.name in html
        assert _has_action(bot.sent[-1]["markup"], "ws:logout")
        assert "the workspace home" not in html
        assert "My Workspaces" not in html  # not the chooser list title
    finally:
        await container.aclose()


async def test_start_while_inside_with_exactly_one_workspace_still_resumes():
    """The tempting shortcut is to skip the session when the user owns one
    workspace; do not — the session is the source of truth."""
    container = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        ws = await container.services.workspaces.create(TG, "onlyone", plan_code="trial")
        await container.services.session.enter(TG, ws.id)
        # Exactly one owned workspace.
        assert len(await container.services.workspaces.list(TG)) == 1
        await dispatcher.feed_update(bot, _message(1, TG, "/start"))
        assert _has_action(bot.sent[-1]["markup"], "ws:logout")
    finally:
        await container.aclose()


async def test_start_twice_is_idempotent():
    container = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
        await container.services.session.enter(TG, ws.id)
        await dispatcher.feed_update(bot, _message(1, TG, "/start"))
        first_actions = _actions(bot.sent[-1]["markup"])
        await dispatcher.feed_update(bot, _message(2, TG, "/start"))
        # Still inside; the second /start renders the same workspace screen.
        assert _has_action(bot.sent[-1]["markup"], "ws:logout")
        assert first_actions == _actions(bot.sent[-1]["markup"])
        assert await container.services.session.current(TG) is not None
    finally:
        await container.aclose()


async def test_logout_then_start_shows_the_chooser_again():
    container = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
        await container.services.session.enter(TG, ws.id)
        # Press the log-out button (its ws:logout callback carries no key).
        await dispatcher.feed_update(bot, _callback(1, TG, "ws:logout"))
        assert await container.services.session.current(TG) is None
        # /start now behaves as before: the menu/chooser, not the workspace.
        await dispatcher.feed_update(bot, _message(2, TG, "/start"))
        html = bot.sent[-1]["html"]
        assert not _has_action(bot.sent[-1]["markup"], "ws:logout")
        assert "ws:new" in _actions(bot.sent[-1]["markup"])  # the menu, not a session
    finally:
        await container.aclose()


async def test_user_with_no_workspaces_keeps_the_welcome_board():
    container = _container()
    await container.database.migrate()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _message(1, 999, "/start"))
        actions = _actions(bot.sent[-1]["markup"])
        assert "ws:new" in actions, "the inline Start button regressed"
        assert "cloud hosting" in bot.sent[-1]["html"].lower() or "استضافة سحابية" in bot.sent[-1]["html"]
    finally:
        await container.aclose()
