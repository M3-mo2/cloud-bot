"""TASK 19 — the three high-severity port-publishing defects (F-01, F-02, F-03).

Driven through the **real dispatcher** with the fake engine, so each claim is
proven end to end, not at the screen-factory level:

* F-01: a re-raised ``DomainError`` renders exactly one screen. ``NotOwned`` and a
  genuinely missing workspace id render the **byte-identical** "not found" board
  (the isolation rule: a foreign id must look absent, not refused); an unmapped
  code still renders the generic board, never silence.
* F-02: a callback (and a message-originated) publish leaves **no orphaned**
  "Publishing…" message — the progress indication becomes the result message.
* F-03: a double-tap of the same port (repeat tap *and* in-flight create) produces
  one engine create; the guard clears on failure and never leaks across users or
  workspaces.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import EngineRefused
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.value_objects import Limits, WorkspaceId
from cloudbot.handlers import features
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import cb
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.rich.tags import esc
from cloudbot.services.events import RequestEvents
from cloudbot.services.tunnels import TunnelAlreadyPublished
from cloudbot.states.feature_input import FeatureInput

WS_OWNER = 6508129575
OTHER = 555
OTHER2 = 556
BOT_ID = 42


class FakeBot:
    """A chat that models messages by id, so orphans are visible.

    ``send`` mints a new message id; ``edit`` mutates the message it targets;
    ``delete`` removes it. Counting rendered *messages* (not calls) is what proves
    there is no orphan.
    """

    id = BOT_ID

    def __init__(self) -> None:
        self.messages: dict[int, str] = {}
        self.deleted: list[int] = []
        self._next = 0

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self._next += 1
        self.messages[self._next] = rich_message.html
        return Message.model_construct(chat={"id": chat_id}, message_id=self._next)

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.messages[message_id] = getattr(rich_message, "html", "")
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)
        self.messages.pop(message_id, None)
        return True

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


def _key(user_id: int) -> StorageKey:
    return StorageKey(bot_id=BOT_ID, chat_id=user_id, user_id=user_id)


async def _harness(*, allow_tunnels: bool = True, allow_net: bool = True, max_tunnels: int = 2):
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        # The owner under test also holds the admin bit, exactly as in production:
        # ``Settings`` defaults ``admin_ids`` to empty, so without this the
        # service-layer plan gate refuses every workspace-scoped call and each
        # test below fails on "Plan expired" instead of reaching the guard it is
        # about. The gate is not the subject here.
        admin_ids={WS_OWNER},
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


# ========================================================= F-01: the guard ====
async def test_f01_domain_error_on_a_callback_renders_exactly_one_screen():
    container, engine, ws = await _harness()
    await container.users.get_or_create(OTHER, default_locale=Locale.EN)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # A foreign tap reaches ``OwnershipService.assert_owned`` → NotOwned, which
        # the guard must turn into one rendered screen (before: silence).
        await dispatcher.feed_update(bot, _callback(1, OTHER, cb("tnl:list", ws.id)))
        assert len(bot.messages) == 1, f"expected one screen, got {len(bot.messages)}"
        assert catalog.t("err.not_found.title", Locale.EN) in bot.messages[1]
    finally:
        await container.aclose()


async def test_f01_domain_error_on_a_message_renders_exactly_one_screen():
    container, engine, ws = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Typing an out-of-range port raises InvalidInput inside the handler; the
        # guard must render one screen (before: the user was stranded).
        await dispatcher.fsm.storage.set_state(_key(WS_OWNER), FeatureInput.awaiting_port.state)
        await dispatcher.fsm.storage.set_data(_key(WS_OWNER), {"ws_id": str(ws.id)})
        await dispatcher.feed_update(bot, _message(1, WS_OWNER, "80"))
        assert len(bot.messages) == 1, f"expected one screen, got {len(bot.messages)}"
        html = bot.messages[1]
        assert catalog.t("err.domain.invalid_input.port_out_of_range", Locale.EN) in html
    finally:
        await container.aclose()


async def test_f01_not_owned_and_missing_render_the_identical_screen():
    """The isolation assertion: a foreign id must be indistinguishable from absent."""
    container, engine, ws = await _harness()
    await container.users.get_or_create(OTHER, default_locale=Locale.EN)
    bogus = WorkspaceId("ws_00000000000000000000000000")
    # The owner owns the bogus id (so the ownership gate passes) but the engine
    # does not have it → ``EngineRefused('not_found')`` from the engine.
    await container.users.add_workspace(WS_OWNER, bogus)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, OTHER, cb("tnl:list", ws.id)))
        foreign = bot.messages[1]
        await dispatcher.feed_update(bot, _callback(2, WS_OWNER, cb("tnl:list", bogus)))
        missing = bot.messages[2]
        # Byte-identical: dropping the real error text for a foreign id is the rule.
        assert foreign == missing, (
            "NotOwned and a missing id rendered different screens — the existence "
            "of the resource leaks"
        )
        assert catalog.t("err.not_found.title", Locale.EN) in foreign
    finally:
        await container.aclose()


async def test_f01_unmapped_error_renders_the_generic_board_not_silence():
    container, engine, ws = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    async def _future_code(_ws_id, _port):
        raise EngineRefused("some_future_code")

    engine.create_tunnel = _future_code  # type: ignore[assignment]
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        assert bot.messages, "an unmapped refusal rendered nothing"
        html = bot.messages[1]
        assert catalog.t("err.unknown.title", Locale.EN) in html
        # No raw engine token leaks into the rendered board.
        assert "some_future_code" not in html
    finally:
        await container.aclose()


async def test_f01_happy_path_renders_one_screen_no_double_render():
    container, engine, ws = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:list", ws.id)))
        assert len(bot.messages) == 1
        assert catalog.t("tunnel.title", Locale.EN) in bot.messages[1]
    finally:
        await container.aclose()


# ======================================================== F-02: no orphan =====
def _no_publishing_orphan(bot: FakeBot) -> None:
    assert len(bot.messages) == 1, f"expected exactly one message, got {sorted(bot.messages)}"
    for html in bot.messages.values():
        assert catalog.t("tunnel.publishing", Locale.EN) not in html, "orphaned 'Publishing…'"


async def test_f02_callback_publish_success_leaves_no_orphan():
    container, engine, ws = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        _no_publishing_orphan(bot)
        assert catalog.t("tunnel.title", Locale.EN) in bot.messages[1]
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 1
    finally:
        await container.aclose()


async def test_f02_callback_publish_refusal_leaves_no_orphan():
    container, engine, ws = await _harness(max_tunnels=1)
    await engine.create_tunnel(ws.id, 8081)  # cap reached on a different port
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        _no_publishing_orphan(bot)
        assert catalog.t("tunnel.limit_title", Locale.EN) in bot.messages[1]
    finally:
        await container.aclose()


async def test_f02_callback_publish_timeout_leaves_no_orphan():
    container, engine, ws = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    async def _timeout(_ws_id, _port):
        raise EngineRefused("tunnel_url_unavailable")

    engine.create_tunnel = _timeout  # type: ignore[assignment]
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        _no_publishing_orphan(bot)
        assert esc(catalog.t("tunnel.timeout_title", Locale.EN)) in bot.messages[1]
    finally:
        await container.aclose()


async def test_f02_message_originated_publish_leaves_no_orphan():
    container, engine, ws = await _harness()
    bot = FakeBot()
    ctx = UserContext(tg_id=WS_OWNER, locale=Locale.EN, is_admin=False, mode=RunMode.DEV)
    message = _message(1, WS_OWNER, "x").message
    assert message is not None
    try:
        await features._publish(
            message, ctx, container.services, RichSender(bot), ws.id, 8080, RequestEvents()
        )
        _no_publishing_orphan(bot)
        assert catalog.t("tunnel.title", Locale.EN) in next(iter(bot.messages.values()))
    finally:
        await container.aclose()


# ========================================================= F-03: double tap ===
async def test_f03_in_flight_double_publish_creates_one_tunnel():
    container, engine, ws = await _harness()
    svc = container.services.tunnels
    created = 0
    started = asyncio.Event()
    release = asyncio.Event()
    original = engine.create_tunnel

    async def slow(ws_id, port):
        nonlocal created
        created += 1
        started.set()
        await release.wait()
        return await original(ws_id, port)

    engine.create_tunnel = slow  # type: ignore[assignment]
    try:
        first = asyncio.create_task(svc.publish(WS_OWNER, ws.id, 8080))
        await started.wait()
        second = asyncio.create_task(svc.publish(WS_OWNER, ws.id, 8080))
        await asyncio.sleep(0.01)  # let the second reach the guard
        release.set()
        await first
        with pytest.raises(TunnelAlreadyPublished):
            await second
        assert created == 1, f"two taps created {created} tunnels"
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 1
    finally:
        await container.aclose()


async def test_f03_sequential_second_publish_does_not_create_a_second():
    container, engine, ws = await _harness()
    svc = container.services.tunnels
    try:
        await svc.publish(WS_OWNER, ws.id, 8080)
        with pytest.raises(TunnelAlreadyPublished):
            await svc.publish(WS_OWNER, ws.id, 8080)
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 1
    finally:
        await container.aclose()


async def test_f03_failed_create_clears_the_guard_so_a_retry_works():
    container, engine, ws = await _harness()
    svc = container.services.tunnels
    original = engine.create_tunnel

    async def boom(_ws_id, _port):
        raise EngineRefused("internal")

    engine.create_tunnel = boom  # type: ignore[assignment]
    try:
        with pytest.raises(EngineRefused):
            await svc.publish(WS_OWNER, ws.id, 8080)
        # The guard did not stick: a retry with a working engine succeeds.
        engine.create_tunnel = original  # type: ignore[assignment]
        tunnel = await svc.publish(WS_OWNER, ws.id, 8080)
        assert tunnel.port == 8080
    finally:
        await container.aclose()


async def test_f03_two_different_ports_create_two_tunnels():
    container, engine, ws = await _harness()
    svc = container.services.tunnels
    try:
        await svc.publish(WS_OWNER, ws.id, 8080)
        await svc.publish(WS_OWNER, ws.id, 8081)
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 2
    finally:
        await container.aclose()


async def test_f03_two_users_do_not_interfere():
    container, engine, ws = await _harness()
    svc = container.services.tunnels
    await container.users.get_or_create(OTHER2, default_locale=Locale.EN)
    ws2 = engine.add_workspace("other")
    await container.users.add_workspace(OTHER2, ws2.id)
    # This test checks that one user's publish does not disturb the other's, so
    # both need a live plan: the gate is enforced per (user, workspace) and would
    # otherwise refuse the second publish for a reason this test is not about.
    await container.subscriptions.grant(
        OTHER2, str(ws2.id), "month", 30, OTHER2, "test fixture"
    )
    await engine.patch_workspace(
        ws2.id, limits=Limits(allow_net=True, allow_tunnels=True)
    )
    try:
        await asyncio.gather(
            svc.publish(WS_OWNER, ws.id, 8080),
            svc.publish(OTHER2, ws2.id, 8080),
        )
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 1
        assert len((await engine.list_tunnels(ws2.id)).tunnels) == 1
    finally:
        await container.aclose()


async def test_f03_dispatcher_double_tap_creates_one_tunnel():
    """The real dispatcher path: two ``tnl:port`` callbacks for the same port."""
    container, engine, ws = await _harness()
    created = 0
    original = engine.create_tunnel

    async def counting(ws_id, port):
        nonlocal created
        created += 1
        return await original(ws_id, port)

    engine.create_tunnel = counting  # type: ignore[assignment]
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        await dispatcher.feed_update(bot, _callback(2, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        assert created == 1, f"the double tap created {created} tunnels"
        assert len((await engine.list_tunnels(ws.id)).tunnels) == 1
    finally:
        await container.aclose()
