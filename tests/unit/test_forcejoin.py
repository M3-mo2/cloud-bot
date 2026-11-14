"""TASK forcejoin: the mandatory-subscription gate and its admin management.

The properties that matter, each asserted against real objects (the middleware,
the SQLite store, the domain mapping) rather than a mock of the thing under test:

* a subscribed user passes, each non-subscribed status is refused;
* any "cannot know" case **allows** (the anti-lockout rule) and logs a warning;
* the owner (``CLOUDBOT_ADMIN_IDS``) is exempt;
* adding a chat where the bot is not an admin is refused with the exact reason;
* disabling a chat removes it from the requirement without deleting the row;
* the global switch off ⇒ nobody is gated;
* the gate is a middleware: a representative handler is blocked without touching
  that handler's own code;
* every gate button's callback_data is ≤ 64 bytes, one column, and no digit is
  Arabic-Indic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import structlog
from aiogram.types import CallbackQuery, Chat, Message, User

from cloudbot.adapters.store.sqlite_forcejoin import SqliteForceJoinStore
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.forcejoin import (
    ChatResolutionError,
    ForceJoinChat,
    MembershipResult,
    chat_kind_for_type,
    status_is_subscribed,
)
from cloudbot.middleware.context import UserContext
from cloudbot.middleware.forcejoin import ForceJoinMiddleware
from cloudbot.presentation.i18n.formatters import has_arabic_digits
from cloudbot.presentation.screens.forcejoin import gate_screen
from cloudbot.services.forcejoin import ForceJoinService

USER = 500
ADMIN = 777
BOT = 42
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)

class Directory:
    """A controllable ``TelegramDirectory``: membership per chat, admin per chat."""

    def __init__(self) -> None:
        self.results: dict[int, MembershipResult] = {}
        self.admins: dict[int, bool] = {}
        self.chats: dict[str, dict] = {}
        self.bot_id = BOT

    async def resolve_chat(self, reference: str) -> dict:
        if reference not in self.chats:
            raise ChatResolutionError("not_found", reference)
        return self.chats[reference]

    async def is_admin_in(self, chat_id: int, bot_id: int) -> bool:
        return self.admins.get(chat_id, False)

    async def membership(self, chat_id: int, user_id: int) -> MembershipResult:
        return self.results.get(chat_id, MembershipResult.unknown_for(chat_id, "no_fixture"))

@pytest.fixture
async def store(database):
    return SqliteForceJoinStore(database)

@pytest.fixture
def directory():
    return Directory()

@pytest.fixture
async def gate(store, directory):
    return ForceJoinService(store, directory, bot_id=BOT)

async def _add(store, chat_id: int, *, kind="channel", title="Cloud", username="cloud", enabled=True):
    chat = await store.add(
        chat_id=chat_id,
        username=username,
        title=title,
        kind=kind,
        bot_is_admin=True,
        added_by=ADMIN,
    )
    if not enabled:
        await store.set_enabled(chat_id, False)
    return chat

def _ctx(tg_id: int = USER, *, is_admin: bool = False) -> UserContext:
    return UserContext(
        tg_id=tg_id, locale=Locale.EN, is_admin=is_admin, mode=RunMode.PRODUCTION
    )

def _message(text: str = "hello", uid: int = USER) -> Message:
    return Message(
        message_id=1,
        date=NOW,
        chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="U"),
        text=text,
    )

def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=USER, is_bot=False, first_name="U"),
        chat_instance="c",
        data=data,
    )

# -- 1. the membership mapping ---------------------------------------------
@pytest.mark.parametrize("status", ["member", "administrator", "creator"])
def test_subscribed_statuses_pass(status):
    assert status_is_subscribed(status) is True

@pytest.mark.parametrize("status", ["left", "kicked"])
def test_left_and_kicked_are_refused(status):
    assert status_is_subscribed(status) is False

def test_restricted_is_refused_without_is_member_and_passes_with_it():
    assert status_is_subscribed("restricted", is_member=False) is False
    assert status_is_subscribed("restricted", is_member=True) is True

# -- 2. the gate verdict ----------------------------------------------------
async def test_subscribed_user_passes(store, directory, gate):
    chat = await _add(store, -1001)
    directory.results[-1001] = MembershipResult.allowed(-1001)
    status = await gate.status(USER)
    assert status.must_join is False
    assert status.missing == ()
    assert status.required == (chat,)

@pytest.mark.parametrize("result", [
    MembershipResult.refused(-1001),
    MembershipResult.unknown_for(-1001, "network"),
])
async def test_unknown_allows_instead_of_locking_out(store, directory, gate, result):
    await _add(store, -1001)
    directory.results[-1001] = result
    with structlog.testing.capture_logs() as logs:
        status = await gate.status(USER)
    if result.unknown:
        assert status.must_join is False  # cannot know ⇒ allow
        assert any(entry["event"] == "forcejoin.unverifiable_chat" for entry in logs)
    else:
        assert status.must_join is True

async def test_each_non_subscribed_chat_is_reported_missing(store, directory, gate):
    first = await _add(store, -1001, title="A", username="a")
    second = await _add(store, -1002, title="B", username="b")
    directory.results[-1001] = MembershipResult.refused(-1001)
    directory.results[-1002] = MembershipResult.refused(-1002)
    status = await gate.status(USER)
    assert status.must_join is True
    assert set(status.missing) == {first, second}

async def test_a_refusal_is_never_cached_and_a_positive_is(store):
    calls = {"n": 0}

    class Counting(Directory):
        async def membership(self, chat_id, user_id):
            calls["n"] += 1
            return await super().membership(chat_id, user_id)

    directory = Counting()
    gate = ForceJoinService(store, directory, bot_id=BOT)
    await _add(store, -1001)
    # A refusal is never cached: the user pressed «تحقّقت», so ask again.
    directory.results[-1001] = MembershipResult.refused(-1001)
    await gate.status(USER)
    await gate.status(USER)
    assert calls["n"] == 2
    await gate.status(USER, force=True)
    assert calls["n"] == 3  # «تحقّقت» always asks again

    # A positive answer is reused inside the short TTL (one API call per tap).
    directory.results[-1001] = MembershipResult.allowed(-1001)
    await gate.status(USER, force=True)
    await gate.status(USER)
    await gate.status(USER)
    assert calls["n"] == 4

# -- 3. the switches --------------------------------------------------------
async def test_disabling_a_chat_removes_it_without_deleting_the_row(store, directory, gate):
    await _add(store, -1001)
    await _add(store, -1002)
    directory.results[-1001] = MembershipResult.allowed(-1001)
    directory.results[-1002] = MembershipResult.refused(-1002)
    assert (await gate.status(USER)).must_join is True

    await gate.set_chat_enabled(-1002, False)
    assert (await gate.status(USER)).must_join is False
    rows = await store.chats()
    assert [c.chat_id for c in rows] == [-1001, -1002]  # nothing deleted
    assert (await store.get(-1002)).enabled is False

async def test_global_switch_off_gates_nobody(store, directory, gate):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)
    assert (await gate.status(USER)).must_join is True
    await gate.set_enabled(False)
    status = await gate.status(USER)
    assert status.enabled is False
    assert status.must_join is False

# -- 4. adding a chat -------------------------------------------------------
async def test_adding_a_chat_where_the_bot_is_not_admin_is_refused(store, directory, gate):
    directory.chats["@nope"] = {
        "chat_id": -1005,
        "username": "nope",
        "title": "Nope",
        "type": "channel",
    }
    directory.admins[-1005] = False
    with pytest.raises(ChatResolutionError) as caught:
        await gate.add_chat("@nope", added_by=ADMIN)
    assert caught.value.reason == "not_admin"
    assert await store.chats() == []  # not stored

async def test_adding_a_chat_the_bot_administers_stores_it(store, directory, gate):
    directory.chats["@cloud"] = {
        "chat_id": -1006,
        "username": "cloud",
        "title": "Cloud",
        "type": "supergroup",
    }
    directory.admins[-1006] = True
    chat = await gate.add_chat("@cloud", added_by=ADMIN)
    assert chat.chat_id == -1006
    assert chat.kind == "group"
    assert chat.bot_is_admin is True
    assert chat.added_by == ADMIN
    assert (await gate.status(USER)).required == (chat,)

def test_chat_kind_mapping():
    assert chat_kind_for_type("channel") == "channel"
    assert chat_kind_for_type("supergroup") == "group"
    assert chat_kind_for_type("group") == "group"

# -- 5. the middleware is the seam -----------------------------------------
async def _run(mw, event, ctx, services):
    calls = {"handler": 0}

    async def handler(ev, data):
        calls["handler"] += 1
        return "handled"

    from cloudbot.presentation.rich.sender import RichSender

    class Bot:
        id = BOT

        def __init__(self):
            self.sent, self.edits = [], []

        async def send_rich_message(self, **kw):
            self.sent.append(kw)
            return Message.model_construct(chat={"id": kw["chat_id"]}, message_id=1)

        async def edit_message_text(self, **kw):
            self.edits.append(kw)
            return Message.model_construct(chat={"id": kw["chat_id"]}, message_id=1)

    bot = Bot()
    data = {"ctx": ctx, "services": services, "sender": RichSender(bot)}
    result = await mw(handler, event, data)
    return calls["handler"], result, bot

class _Services:
    def __init__(self, gate: ForceJoinService) -> None:
        self.forcejoin = gate

async def test_a_representative_handler_is_blocked_without_touching_it(store, directory, gate):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)
    mw = ForceJoinMiddleware()
    handler_calls, result, bot = await _run(mw, _message(), _ctx(), _Services(gate))
    assert handler_calls == 0  # the handler's own code never ran
    assert result is None
    assert bot.sent, "the gate screen must be sent"

async def test_the_admin_is_exempt(store, directory, gate):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)
    mw = ForceJoinMiddleware()
    handler_calls, _, _ = await _run(mw, _message(), _ctx(ADMIN, is_admin=True), _Services(gate))
    assert handler_calls == 1

async def test_start_and_the_gate_callback_are_exempt(store, directory, gate):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)
    mw = ForceJoinMiddleware()
    started, _, _ = await _run(mw, _message("/start"), _ctx(), _Services(gate))
    checked, _, _ = await _run(mw, _callback("fj:check"), _ctx(), _Services(gate))
    assert started == 1
    assert checked == 1

async def test_a_gate_bug_allows_the_user_through(store, directory):
    """A broken gate must never brick the product (the anti-lockout rule)."""

    class Broken:
        async def status(self, user_id, *, force=False):
            raise RuntimeError("boom")

    class Services:
        forcejoin = Broken()

    mw = ForceJoinMiddleware()
    handler_calls, _, _ = await _run(mw, _message(), _ctx(), Services())
    assert handler_calls == 1

# -- 6. the screen ----------------------------------------------------------
async def test_gate_screen_has_one_button_per_missing_chat_and_a_check_button(store, directory, gate):
    first = await _add(store, -1001, title="Channel One", username="one")
    second = await _add(store, -1002, title="Group Two", username="", kind="group")
    directory.results[-1001] = MembershipResult.refused(-1001)
    directory.results[-1002] = MembershipResult.refused(-1002)
    status = await gate.status(USER)
    screen = gate_screen(status, Locale.EN)
    built = screen.doc.build()
    blocks = built.blocks
    paragraphs = [b for b in blocks if getattr(b, "type", "") == "paragraph"]
    assert any("must join" in b.text for b in paragraphs)
    button_blocks = [b for b in blocks if getattr(b, "type", "") == "buttons"]
    chats = [b.buttons[0] for b in button_blocks[:-1]]
    assert [b.text for b in chats] == [first.label, second.label]
    # A public username is a real join link; one without is a callback.
    assert chats[0].url == "https://t.me/one"
    assert chats[1].callback_data == "fj:chat:-1002"
    check = button_blocks[-1].buttons[0]
    assert check.text == "✅ I have joined"
    assert check.style == "success"

@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
async def test_gate_buttons_are_small_single_column_and_ascii(store, directory, gate, locale):
    await _add(store, -1001, title="Cloud", username="cloud")
    directory.results[-1001] = MembershipResult.refused(-1001)
    screen = gate_screen(await gate.status(USER), locale)
    built = screen.doc.build()
    for block in built.blocks:
        if getattr(block, "type", "") != "buttons":
            continue
        assert len(block.buttons) == 1  # one column
        for button in block.buttons:
            if button.callback_data:
                assert len(button.callback_data.encode("utf-8")) <= 64
            assert not has_arabic_digits(str(button.text))
    assert not has_arabic_digits(screen.doc.html)


# -- 7. end to end through the real dispatcher ------------------------------
async def test_an_ordinary_command_is_gated_through_the_real_dispatcher(database):
    """The property that matters: a handler written before the gate is blocked.

    ``/help`` knows nothing about force-join; the update-level middleware stops
    it before routing. The admin's identical ``/help`` is let through.
    """
    from datetime import datetime, timezone

    from aiogram.types import Chat as TgChat
    from aiogram.types import Message as TgMessage
    from aiogram.types import Update, User as TgUser

    from cloudbot.bootstrap.app import build_dispatcher
    from cloudbot.bootstrap.container import build
    from cloudbot.core.config import Settings

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

    class FakeBot:
        id = BOT

        def __init__(self):
            self.sent, self.calls = [], []

        async def get_chat_member(self, chat_id, user_id):
            self.calls.append((chat_id, user_id))

            class Member:
                status = "left"

            return Member()

        async def delete_message(self, chat_id, message_id):
            return True

        async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
            self.sent.append(rich_message)
            return TgMessage.model_construct(chat={"id": chat_id}, message_id=1)

        async def edit_message_text(self, **kw):
            self.sent.append(kw["rich_message"])
            return TgMessage.model_construct(chat={"id": 1}, message_id=1)

    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    store = container.forcejoin_store
    await store.add(
        chat_id=-1001, username="cloud", title="Cloud", kind="channel",
        bot_is_admin=True, added_by=ADMIN,
    )

    def update(uid: int, user_id: int, text: str) -> Update:
        return Update(
            update_id=uid,
            message=TgMessage(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=TgChat(id=user_id, type="private"),
                from_user=TgUser(id=user_id, is_bot=False, first_name="U"),
                text=text,
            ),
        )

    # A non-subscribed user's /help is stopped: no menu, the gate screen instead.
    await dispatcher.feed_update(bot, update(1, USER, "/help"))
    assert bot.sent, "the gate must answer"
    blocks = bot.sent[-1].blocks
    assert blocks and blocks[0].type == "paragraph"

    # The admin is exempt: the ordinary help screen renders (html, not a gate).
    await dispatcher.feed_update(bot, update(2, ADMIN, "/help"))
    assert bot.sent[-1].blocks is None and bot.sent[-1].html

    # /start is exempt so the gate can be rendered by the handler itself.
    before = len(bot.sent)
    await dispatcher.feed_update(bot, update(3, USER, "/start"))
    assert len(bot.sent) > before

    await container.aclose()

# -- 8. TASK forcejoin-v2: every decision is recorded -----------------------
async def _event_rows(database):
    conn = await database.connect()
    async with conn.execute(
        "SELECT ts, tg_id, chat_id, outcome, reason, latency_ms, cached, detail "
        "FROM forcejoin_events ORDER BY id"
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]

def _measured_directory(base: "Directory", latencies: dict[int, int] | None = None):
    """A directory whose answers carry the measurement the service records."""
    latency = latencies or {}

    class Measured(Directory):
        async def membership(self, chat_id, user_id):
            result = await super().membership(chat_id, user_id)
            return result.with_measurement(
                latency_ms=latency.get(chat_id, 12), cached=False
            )

    measured = Measured()
    measured.results = base.results
    measured.admins = base.admins
    measured.chats = base.chats
    measured.bot_id = base.bot_id
    return measured

async def test_pass_deny_unknown_each_write_one_row(store, directory):
    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore

    await _add(store, -1001)
    await _add(store, -1002)
    await _add(store, -1003)
    directory.results[-1001] = MembershipResult.allowed(-1001)
    directory.results[-1002] = MembershipResult.refused(-1002)
    directory.results[-1003] = MembershipResult.unknown_for(-1003, "network")
    db = store._db
    events = SqliteForceJoinEventStore(db)
    gate = ForceJoinService(store, directory, bot_id=BOT, events=events)
    await gate.status(USER)

    rows = await _event_rows(db)
    by_chat = {r["chat_id"]: (r["outcome"], r["reason"]) for r in rows}
    assert by_chat[-1001] == ("pass", "subscribed")
    assert by_chat[-1002] == ("deny", "absent")
    assert by_chat[-1003] == ("unknown", "network")

async def test_cached_positive_writes_cached_one_and_zero_latency(store, directory):
    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore

    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.allowed(-1001)
    db = store._db
    gate = ForceJoinService(
        store, _measured_directory(directory), bot_id=BOT,
        events=SqliteForceJoinEventStore(db),
    )
    await gate.status(USER)          # a live call (fresh row)
    await gate.status(USER)          # inside the positive TTL -> cached row

    rows = await _event_rows(db)
    assert [r["cached"] for r in rows] == [0, 1]
    cached_row = rows[-1]
    assert cached_row["outcome"] == "pass"
    assert cached_row["reason"] == "cached_positive"
    assert cached_row["latency_ms"] == 0

async def test_a_refusal_is_never_cached_two_fresh_rows(store, directory):
    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore

    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)
    db = store._db
    gate = ForceJoinService(store, directory, bot_id=BOT, events=SqliteForceJoinEventStore(db))
    await gate.status(USER)
    await gate.status(USER)

    rows = await _event_rows(db)
    assert len(rows) == 2
    assert all(r["outcome"] == "deny" and r["cached"] == 0 for r in rows)

async def test_a_failed_event_write_does_not_raise_out_of_status(store, directory):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.refused(-1001)

    class ExplodingSink:
        async def record(self, event):
            raise RuntimeError("disk full")

    gate = ForceJoinService(store, directory, bot_id=BOT, events=ExplodingSink())
    status = await gate.status(USER)  # must not raise
    assert status.must_join is True

async def test_user_cooldown_returns_previous_answer_and_force_bypasses_it(store, directory):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.unknown_for(-1001, "network")
    clock = {"now": 100.0}
    gate = ForceJoinService(store, directory, bot_id=BOT, clock=lambda: clock["now"])
    await gate.status(USER)                  # one live call (unknown, cooldown stored)
    calls = {"n": 0}

    class Counting(Directory):
        async def membership(self, chat_id, user_id):
            calls["n"] += 1
            return await super().membership(chat_id, user_id)

    counting = Counting()
    counting.results = directory.results
    counting.admins, counting.chats, counting.bot_id = directory.admins, directory.chats, directory.bot_id
    gate._directory = counting

    clock["now"] += 1.0
    cached = await gate.status(USER)           # inside 3 s -> previous answer
    assert calls["n"] == 0
    assert cached.verdicts[0].cached is True

    forced = await gate.status(USER, force=True)  # admin test bypasses it
    assert calls["n"] == 1
    assert forced.verdicts[0].cached is False

async def test_rate_limit_becomes_unknown_allows_and_pauses_the_chat(store):
    """A 429 is not a refusal: the user is admitted, and the chat backs off."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import GetChatMember

    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore
    from cloudbot.adapters.telegram.directory import TelegramDirectory

    class Bot:
        id = BOT
        calls = 0

        async def get_chat_member(self, chat_id, user_id):
            Bot.calls += 1
            raise TelegramRetryAfter(
                method=GetChatMember(chat_id=chat_id, user_id=user_id),
                message="Too Many Requests: retry after 30",
                retry_after=30,
            )

    await _add(store, -1001)
    db = store._db
    clock = {"now": 100.0}
    gate = ForceJoinService(
        store, TelegramDirectory(Bot()), bot_id=BOT,
        events=SqliteForceJoinEventStore(db), clock=lambda: clock["now"],
    )
    status = await gate.status(USER)
    assert status.must_join is False  # admitted
    assert status.verdicts[0].state == "unknown"

    rows = await _event_rows(db)
    assert rows[-1]["outcome"] == "error"
    assert rows[-1]["reason"] == "rate_limited"

    # The per-chat pause means the next check does not call the API again.
    before = Bot.calls
    clock["now"] += 5.0
    again = await gate.status(USER)
    assert again.must_join is False
    assert Bot.calls == before

async def test_chat_not_found_marks_not_admin_without_disabling(store):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetChatMember

    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore
    from cloudbot.adapters.telegram.directory import TelegramDirectory

    class Bot:
        id = BOT

        async def get_chat_member(self, chat_id, user_id):
            raise TelegramBadRequest(
                method=GetChatMember(chat_id=chat_id, user_id=user_id),
                message="Bad Request: bot is not a member of the channel chat",
            )

    await _add(store, -1001)
    db = store._db
    gate = ForceJoinService(
        store, TelegramDirectory(Bot()), bot_id=BOT,
        events=SqliteForceJoinEventStore(db),
    )
    with structlog.testing.capture_logs() as logs:
        status = await gate.status(USER)
    assert status.must_join is False  # admitted
    row = await store.get(-1001)
    assert row is not None
    assert row.bot_is_admin is False
    assert row.enabled is True  # never disabled silently
    assert any(e["event"] == "forcejoin.chat_needs_attention" for e in logs)

async def test_boot_self_check_writes_is_admin_and_logs_one_line(store, directory):
    await _add(store, -1001)
    await _add(store, -1002)
    directory.admins[-1001] = True
    directory.admins[-1002] = False
    gate = ForceJoinService(store, directory, bot_id=BOT)
    with structlog.testing.capture_logs() as logs:
        ok, total, bad = await gate.self_check()
    assert (ok, total, bad) == (1, 2, [-1002])
    assert (await store.get(-1001)).bot_is_admin is True
    assert (await store.get(-1002)).bot_is_admin is False
    assert any(
        "1/2 chats ok" in str(e.get("summary", "")) and e.get("needs_attention") == [-1002]
        for e in logs
    )

async def test_admin_actions_are_recorded_as_config_rows(store, directory):
    from cloudbot.adapters.store.sqlite_forcejoin_events import SqliteForceJoinEventStore

    db = store._db
    gate = ForceJoinService(store, directory, bot_id=BOT, events=SqliteForceJoinEventStore(db))
    directory.chats["@cloud"] = {
        "chat_id": -1006, "username": "cloud", "title": "Cloud", "type": "channel",
    }
    directory.admins[-1006] = True
    await gate.add_chat("@cloud", added_by=ADMIN)
    await gate.set_chat_enabled(-1006, False, actor=ADMIN)
    await gate.remove_chat(-1006, actor=ADMIN)
    await gate.set_enabled(False, actor=ADMIN)

    rows = await _event_rows(db)
    assert [r["outcome"] for r in rows] == ["config"] * 4
    assert [r["reason"] for r in rows] == ["add", "disable", "remove", "gate_off"]

def test_event_detail_is_capped_and_scrubbed():
    from cloudbot.domain.forcejoin import EVENT_DETAIL_MAX, GateEvent, scrub_detail

    assert len(scrub_detail("x" * 500)) == EVENT_DETAIL_MAX
    # A credential-shaped string is never written.
    assert scrub_detail("Authorization: Bearer 123:ABC") == ""
    assert scrub_detail("bot token 123:ABC") == ""
    event = GateEvent(outcome="error", detail="y" * 400)
    assert len(event.normalized().detail) == EVENT_DETAIL_MAX

async def test_gate_screen_names_each_chat_and_offers_one_check_button(store, directory):
    await _add(store, -1001, title="Channel One", username="one")
    await _add(store, -1002, title="Group Two", username="", kind="group")
    directory.results[-1001] = MembershipResult.refused(-1001)
    directory.results[-1002] = MembershipResult.refused(-1002)
    status = await gate_status(store, directory)
    screen = gate_screen(status, Locale.EN, cooldown_s=3)
    built = screen.doc.build()
    paragraph_texts = [b.text for b in built.blocks if getattr(b, "type", "") == "paragraph"]
    assert any("Channel One" not in t for t in paragraph_texts)  # titles are on buttons
    button_blocks = [b for b in built.blocks if getattr(b, "type", "") == "buttons"]
    labels = [b.buttons[0].text for b in button_blocks]
    assert labels[0] == "Channel One"
    assert labels[1] == "Group Two"
    assert labels[-1] == "✅ I have joined (3 s)"
    assert button_blocks[-1].buttons[0].disabled is not None

async def test_gate_screen_says_unknown_is_let_through(store, directory):
    await _add(store, -1001)
    directory.results[-1001] = MembershipResult.unknown_for(-1001, "network")
    status = await gate_status(store, directory)
    screen = gate_screen(status, Locale.EN)
    text = screen.doc.build().blocks
    paragraphs = [b.text for b in text if getattr(b, "type", "") == "paragraph"]
    assert any("could not be verified" in t for t in paragraphs)

async def gate_status(store, directory):
    gate = ForceJoinService(store, directory, bot_id=BOT)
    return await gate.status(USER)
