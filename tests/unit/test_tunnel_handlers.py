"""TASK 15 — the publish path end-to-end against the fake engine.

The live bug was a **silent redraw**: publishing a third tunnel past the cap sent
the user back to the list with no sentence. These tests drive the real handler
dispatcher (not just the screen factories) so the refusal is proven to render,
and an unmapped refusal is proven to fall through to the error screen rather than
being swallowed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import EngineRefused
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Limits
from cloudbot.presentation.keyboards.callbacks import cb

WS_OWNER = 6508129575


class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(rich_message.html)
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

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
            id=str(uid), from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c", message=msg, data=data,
        ),
    )


async def _container_with_workspace(*, allow_tunnels=True, allow_net=True, max_tunnels=2):
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    engine = container.engine
    assert isinstance(engine, FakeEngine), "DEV + mock_engine must wire the fake engine"
    engine.max_tunnels = max_tunnels
    await container.users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await container.users.add_workspace(WS_OWNER, ws.id)
    await engine.patch_workspace(
        ws.id, limits=Limits(allow_net=allow_net, allow_tunnels=allow_tunnels)
    )
    return container, engine, ws


async def test_publish_at_cap_renders_the_limit_screen_not_a_silent_redraw():
    container, engine, ws = await _container_with_workspace(max_tunnels=2)
    await engine.create_tunnel(ws.id, 8080)
    await engine.create_tunnel(ws.id, 8081)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Opening the list states the ceiling "2 of 2" and mutes publish.
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:list", ws.id)))
        assert "2 of 2" in bot.sent[-1]

        # Publishing a third is refused with a real sentence naming the limit.
        await dispatcher.feed_update(bot, _callback(2, WS_OWNER, cb("tnl:port", ws.id, 8082)))
        html = bot.sent[-1]
        assert "Link limit reached" in html
        assert "8082" in html
        # It is NOT the plain list (that was the live bug).
        assert "Public links" not in html
    finally:
        await container.aclose()


async def test_publish_disabled_at_cap_does_not_call_the_engine():
    """The muted entry carries a reason; a tap is a no-op, not an engine call."""
    container, engine, ws = await _container_with_workspace(max_tunnels=2)
    await engine.create_tunnel(ws.id, 8080)
    await engine.create_tunnel(ws.id, 8081)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:list", ws.id)))
        assert "2 of 2" in bot.sent[-1]
    finally:
        await container.aclose()


async def test_unmapped_refusal_is_re_raised_not_swallowed(monkeypatch):
    """R1: an engine code with no sentence must not be swallowed.

    ``_publish`` re-raises it, so it reaches the error guard (the single place a
    DomainError becomes a screen). We spy on the guard to prove the refusal left
    the tunnel path instead of silently redrawing the list.
    """
    from cloudbot.middleware import error_guard

    seen: list[str] = []
    original = error_guard.ErrorGuardMiddleware._render

    async def spy(self, event, data, exc):
        seen.append(getattr(exc, "engine_code", type(exc).__name__))
        return await original(self, event, data, exc)

    monkeypatch.setattr(error_guard.ErrorGuardMiddleware, "_render", spy)

    container, engine, ws = await _container_with_workspace()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    async def _future_code(_ws_id, _port):
        raise EngineRefused("some_future_code")

    engine.create_tunnel = _future_code  # type: ignore[assignment]
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        assert "some_future_code" in seen, "the unmapped refusal was swallowed"
        # Not a silent redraw of the list.
        assert all("Public links" not in html for html in bot.sent)
    finally:
        await container.aclose()
