"""TASK 16/17 — sticky terminal input, driven through the real dispatcher.

The reported bug (verbatim): *«عايز الترمنال اقدر اكتب فيها علطول مش كل مره ادوس
ارسال امر»* — every single command cost a tap on **Send a command** because the
handler cleared the FSM state after the first text message.

These tests drive the real aiogram dispatcher against the fake engine, exactly as
the previous run measured by hand, so the sticky rules are proven end to end:

* one tap, then three consecutive plain messages all reach ``send_line``;
* the state is **not** cleared after a command (the reported bug);
* a known slash-command clears the state and is never sent into the session;
* the one-tap **stop typing** control clears the state;
* a foreign user's message cannot reach the session (``NotOwned`` clears state);
* a text message after the session ended renders the ended screen, clears the
  state and does not raise;
* the view after a command still carries its buttons (the module's own rule);
* the §3 decision — **cut short on idle + supersede on a new command** — pinned.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import cloudbot.handlers.terminal as term_handlers
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.value_objects import SessionStatus
from cloudbot.middleware.context import UserContext
from cloudbot.middleware.terminal_sticky import TerminalStickyMiddleware
from cloudbot.presentation.keyboards.callbacks import cb
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.states.terminal_input import TerminalInput

TG = 777
OTHER = 888
SES = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"


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

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append({"chat_id": chat_id, "message_id": message_id})
        return True

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


def _key(user_id: int) -> StorageKey:
    return StorageKey(bot_id=FakeBot.id, chat_id=user_id, user_id=user_id)


async def _harness(*, shorten_follow: bool = True):
    """A container, a workspace and a running session, plus a spy on send_line."""
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    container = build(settings)
    assert isinstance(container.engine, FakeEngine)
    await container.database.migrate()
    ws = await container.services.workspaces.create(TG, "mine")
    session = await container.services.sessions.open(TG, ws.id)

    sends: list[tuple[int, str, str, str]] = []
    original = container.services.sessions.send_line

    async def spy(tg_id, ws_id, ses_id, command):
        # Record only a *successful* delivery: the ownership gate (NotOwned) and an
        # ended session (EngineRefused) must leave the record empty.
        result = await original(tg_id, ws_id, ses_id, command)
        sends.append((tg_id, str(ws_id), ses_id, command))
        return result

    container.services.sessions.send_line = spy  # type: ignore[assignment]

    if shorten_follow:
        # The real follow is 30 × 1 s. Keep the render path but make the loop
        # cheap so these tests measure behaviour, not wall-clock.
        term_handlers.FOLLOW_MAX_ITERATIONS = 30
        term_handlers.FOLLOW_POLL_INTERVAL = 0.0
    return container, ws, session, sends


@pytest.fixture(autouse=True)
def _short_follow(monkeypatch):
    monkeypatch.setattr(term_handlers, "FOLLOW_POLL_INTERVAL", 0.0)
    yield


# -- the reported bug --------------------------------------------------------
async def test_one_tap_then_three_plain_messages_all_reach_send_line():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        for uid, text in enumerate(["echo one", "echo two", "echo three"], start=2):
            await dispatcher.feed_update(bot, _message(uid, TG, text))
        # All three reached the session, in order, with the right ids.
        assert [s[3] for s in sends] == ["echo one", "echo two", "echo three"]
        assert {(s[0], s[1], s[2]) for s in sends} == {(TG, str(ws.id), session.id)}
    finally:
        await container.aclose()


async def test_state_is_not_cleared_after_a_command():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        await dispatcher.feed_update(bot, _message(2, TG, "echo one"))
        state = await dispatcher.fsm.storage.get_state(_key(TG))
        assert state == TerminalInput.awaiting_command.state, "the sticky state was cleared"
        data = await dispatcher.fsm.storage.get_data(_key(TG))
        assert data["ses_id"] == session.id
        assert str(data["ws_id"]) == str(ws.id)
    finally:
        await container.aclose()


# -- slash-commands always win (R4) -----------------------------------------
@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_known_command_clears_state_and_is_not_sent_into_the_session(command):
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        await dispatcher.feed_update(bot, _message(2, TG, command))
        assert await dispatcher.fsm.storage.get_state(_key(TG)) is None
        # Never as terminal input.
        assert sends == [], "a slash-command was sent into the session"
    finally:
        await container.aclose()


async def test_unknown_command_re_renders_the_terminal_and_keeps_the_state():
    """An unknown command still reaches the terminal handler (never swallowed)."""
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        await dispatcher.feed_update(bot, _message(2, TG, "/frobnicate"))
        assert sends == [], "an unknown command was sent into the session"
        assert await dispatcher.fsm.storage.get_state(_key(TG)) == TerminalInput.awaiting_command.state
    finally:
        await container.aclose()


# -- the one-tap exit (R3) ---------------------------------------------------
async def test_stop_typing_clears_the_state_and_the_next_message_is_not_a_command():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        # The exit control clears the state.
        await dispatcher.feed_update(bot, _callback(2, TG, cb("term:exit", ws.id, session.id)))
        assert await dispatcher.fsm.storage.get_state(_key(TG)) is None
        # A later plain message is no longer a command.
        await dispatcher.feed_update(bot, _message(3, TG, "echo not-sticky"))
        assert sends == [], "a plain message after stop typing reached the session"
    finally:
        await container.aclose()


# -- ownership / isolation (R5/R6) -------------------------------------------
async def test_a_foreign_user_cannot_reach_the_session_and_the_state_is_cleared():
    container, ws, session, sends = await _harness()
    await container.users.get_or_create(OTHER, default_locale=Locale.EN)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Even with a hand-set sticky state pointing at someone else's session,
        # the ownership gate refuses and the state is dropped.
        await dispatcher.fsm.storage.set_state(_key(OTHER), TerminalInput.awaiting_command.state)
        await dispatcher.fsm.storage.set_data(
            _key(OTHER), {"ws_id": str(ws.id), "ses_id": session.id}
        )
        await dispatcher.feed_update(bot, _message(1, OTHER, "rm -rf /"))
        assert sends == [], "a foreign user reached the session"
        assert await dispatcher.fsm.storage.get_state(_key(OTHER)) is None
    finally:
        await container.aclose()


async def test_two_users_states_do_not_collide():
    container, ws, session, sends = await _harness()
    await container.users.get_or_create(OTHER, default_locale=Locale.EN)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        # The other user is never in the sticky state.
        assert await dispatcher.fsm.storage.get_state(_key(OTHER)) is None
        await dispatcher.feed_update(bot, _message(2, OTHER, "whoami"))
        assert sends == [], "another user's plain message was delivered into the session"
        # The owner remains sticky.
        assert await dispatcher.fsm.storage.get_state(_key(TG)) == TerminalInput.awaiting_command.state
    finally:
        await container.aclose()


# -- ended session (R5) ------------------------------------------------------
async def test_text_after_the_session_ended_renders_ended_clears_state_and_does_not_raise():
    container, ws, session, sends = await _harness()
    # End the session on the engine but keep the bot-side handle (the documented
    # close semantics), so a read reports ``session_ended`` rather than NotOwned.
    await container.services.sessions.close(TG, ws.id, session.id)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.fsm.storage.set_state(_key(TG), TerminalInput.awaiting_command.state)
        await dispatcher.fsm.storage.set_data(
            _key(TG), {"ws_id": str(ws.id), "ses_id": session.id}
        )
        # Must not raise.
        await dispatcher.feed_update(bot, _message(1, TG, "echo after end"))
        assert sends == [], "input reached an ended session"
        assert await dispatcher.fsm.storage.get_state(_key(TG)) is None
        assert bot.sent, "nothing rendered"
        assert "Terminal closed" in bot.sent[-1]["html"] or "أُغلق" in bot.sent[-1]["html"]
    finally:
        await container.aclose()


# -- the module's own rule (R2) ----------------------------------------------
async def test_the_terminal_view_after_a_command_still_carries_its_buttons():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        await dispatcher.feed_update(bot, _message(2, TG, "echo one"))
        actions = _actions(bot.sent[-1]["markup"])
        # Every terminal button the view normally carries is still there...
        assert any(a.startswith("term:cmd") for a in actions)
        assert any(a.startswith("term:ctrl_d") for a in actions)
        assert any(a.startswith("term:close") for a in actions)
        # ...plus the one-tap exit.
        assert any(a.startswith("term:exit") for a in actions)
    finally:
        await container.aclose()


# -- §3 decision: cut short on idle, supersede on a new command --------------
async def test_follow_is_cut_short_once_the_session_is_idle():
    """An idle session must not burn the whole 30 × 1 s budget (measured 30 s/command)."""
    reads = 0
    real_read = None

    container, ws, session, sends = await _harness()
    real_read = container.services.sessions.read_output

    async def counting_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        return await real_read(*args, **kwargs)

    container.services.sessions.read_output = counting_read  # type: ignore[assignment]
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("term:cmd", ws.id, session.id)))
        reads = 0
        await dispatcher.feed_update(bot, _message(2, TG, "echo one"))
        # The fake session never changes its output, so the follow is idle after
        # the first quiescent tick and stops well before FOLLOW_MAX_ITERATIONS.
        assert reads <= term_handlers.FOLLOW_IDLE_INTERVALS + 1, (
            f"the idle follow polled the engine {reads} times (a 30-tick stall)"
        )
        assert reads < term_handlers.FOLLOW_MAX_ITERATIONS
    finally:
        await container.aclose()


async def test_a_stale_generation_stops_before_the_next_engine_read():
    """Supersede: a follow that is no longer current returns before its next read.

    If the *other* choice were made (serialise, or no generation at all), a stale
    follower would keep polling and editing; this test would fail.
    """
    key = (TG, "ws_x", SES)
    # A newer command has already claimed generation 5; this follower holds 4.
    term_handlers._follow_generation[key] = 5

    reads = 0

    class FakeSessions:
        async def meta(self):
            return None

        async def get(self, *a, **k):
            from cloudbot.domain.entities import TerminalSession

            return TerminalSession(id=SES, workspace_id="ws_x", status=SessionStatus.RUNNING)

        async def read_output(self, *a, **k):
            nonlocal reads
            reads += 1
            raise AssertionError("a superseded follower read the engine")

    class Bundle:
        sessions = FakeSessions()

    sent = []

    class Sender:
        async def send(self, chat_id, doc, *, markup=None, reply_to=None):
            sent.append(doc)
            return Message.model_construct(chat={"id": chat_id}, message_id=1)

        async def edit(self, *a, **k):
            raise AssertionError("a superseded follower edited a message")

    ctx = UserContext(tg_id=TG, locale=Locale.EN, is_admin=False, mode=RunMode.DEV)
    await term_handlers._stream(
        _message(1, TG, "x"),
        ctx,
        Bundle(),
        Sender(),
        "ws_x",
        SES,
        force_send=True,
        generation=4,
    )
    assert reads == 0, "a superseded follower polled the engine"
    assert sent == [], "a superseded follower rendered"


async def test_a_new_command_supersedes_an_in_flight_follow():
    """The in-flight follow of command A stops when command B claims the session."""
    key = (TG, "ws_x", SES)
    gate = asyncio.Event()
    calls = 0

    class FakeSessions:
        async def meta(self):
            return None

        async def get(self, *a, **k):
            from cloudbot.domain.entities import TerminalSession

            return TerminalSession(id=SES, workspace_id="ws_x", status=SessionStatus.RUNNING)

        async def read_output(self, *a, **k):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Only the first follow (A) blocks, so B can run to completion.
                await gate.wait()
            from cloudbot.services.sessions import SessionOutput

            return SessionOutput(data="x", offset=0, next=1, eof=True, truncated=False)

    class Bundle:
        sessions = FakeSessions()

    class Sender:
        async def send(self, chat_id, doc, *, markup=None, reply_to=None):
            return Message.model_construct(chat={"id": chat_id}, message_id=1)

        async def edit(self, *a, **k):
            return Message.model_construct(chat={"id": 1}, message_id=1)

    ctx = UserContext(tg_id=TG, locale=Locale.EN, is_admin=False, mode=RunMode.DEV)
    svc = Bundle()
    sender = Sender()

    async def follow():
        return await term_handlers._follow(
            _message(1, TG, "a"), ctx, svc, sender, "ws_x", SES, force_send=True
        )

    task_a = asyncio.create_task(follow())
    while calls < 1:  # let A enter its first (blocking) read
        await asyncio.sleep(0)
    # Command B starts a new follow: it claims a new generation.
    task_b = asyncio.create_task(follow())
    while not task_b.done():
        await asyncio.sleep(0)
    gate.set()
    await asyncio.wait_for(task_a, timeout=2)
    # A stopped at its next generation check; it did NOT run its own full budget
    # on top of B's (the two together stayed near one budget, not two).
    assert calls <= term_handlers.FOLLOW_MAX_ITERATIONS + 2, (
        f"both followers polled the engine ({calls} reads) — supersede is not real"
    )
    assert term_handlers._follow_is_current(key, term_handlers._follow_generation[key])


# -- the middleware contract (unit) ------------------------------------------
class _DictState:
    def __init__(self, state=None, data=None):
        self._state = state
        self._data = data or {}

    async def get_state(self):
        return self._state

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self._state = None
        self._data = {}


async def _run_middleware(text: str, state) -> bool:
    called = {"handler": False}

    async def handler(event, data):
        called["handler"] = True

    mw = TerminalStickyMiddleware()
    msg = Message.model_construct(message_id=1, text=text)
    await mw(handler, msg, {"state": state})
    return called["handler"]


@pytest.mark.parametrize("command", ["/start", "/help", "/settings", "/admin", "/notifications"])
async def test_middleware_clears_known_commands_while_sticky(command):
    state = _DictState(state=TerminalInput.awaiting_command.state, data={"ws_id": "ws_x", "ses_id": SES})
    assert await _run_middleware(command, state) is True
    assert state._state is None


async def test_middleware_leaves_unknown_commands_alone():
    state = _DictState(state=TerminalInput.awaiting_command.state, data={"ws_id": "ws_x", "ses_id": SES})
    assert await _run_middleware("/frobnicate", state) is True
    assert state._state == TerminalInput.awaiting_command.state


async def test_middleware_ignores_non_sticky_states_and_plain_text():
    state = _DictState(state=None)
    assert await _run_middleware("/help", state) is True
    assert state._state is None
    state2 = _DictState(state=TerminalInput.awaiting_resize.state)
    assert await _run_middleware("/help", state2) is True
    assert state2._state is None  # a command leaves resize mode too
    state3 = _DictState(state=TerminalInput.awaiting_command.state)
    assert await _run_middleware("ls -la", state3) is True
    assert state3._state == TerminalInput.awaiting_command.state


# -- the view carries the exit and the active line (both locales) ------------
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_typing_view_carries_stop_typing_and_the_active_line(locale):
    from cloudbot.domain.entities import TerminalSession

    session = TerminalSession(id=SES, workspace_id="ws_01M6HJASCWM9Z1PF755GT89NK4", status=SessionStatus.RUNNING)
    screen = term_screens.terminal_view(
        session, "ws_01M6HJASCWM9Z1PF755GT89NK4", locale, typing=True
    )
    actions = _actions(screen.markup)
    assert any(a.startswith("term:exit") for a in actions), "no stop-typing control"
    # The one-tap exit is the danger action, so it reads as the way out.
    exit_button = next(b for row in screen.markup.inline_keyboard for b in row if b.callback_data and b.callback_data.startswith("term:exit"))
    assert exit_button.style == "danger"
    assert exit_button.text in ("⏹ Stop typing", "⏹ إيقاف الكتابة")
    # The body says the session is active in the user's language.
    assert "active" in screen.doc.html or "نشِطة" in screen.doc.html


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_non_typing_view_does_not_carry_stop_typing(locale):
    from cloudbot.domain.entities import TerminalSession

    session = TerminalSession(id=SES, workspace_id="ws_01M6HJASCWM9Z1PF755GT89NK4", status=SessionStatus.RUNNING)
    screen = term_screens.terminal_view(session, "ws_01M6HJASCWM9Z1PF755GT89NK4", locale)
    actions = _actions(screen.markup)
    assert not any(a.startswith("term:exit") for a in actions)

