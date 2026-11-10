"""TASK terminal-always — opening a terminal *is* being in it.

History. TASK 16/17 fixed a reported bug (*«عايز الترمنال اقدر اكتب فيها علطول مش
كل مره ادوس ارسال امر»* — every command cost a tap on **Send a command**) by making
command mode sticky: tap the button once, then plain text keeps reaching the
session. TASK terminal-always removed the cause instead of its symptom: the
owner's objection was that **a terminal should need no "Send a command" button at
all** — *"obviously when I open a terminal I'm going to run commands."* The state
is now armed by ``term:open``/``term:new`` themselves, and the button is deleted.

These tests drive the real aiogram dispatcher against the fake engine, exactly as
a user would, and prove the rules end to end:

* the **bite**: after ``term:open`` a plain message reaches the session with **no**
  intervening ``term:cmd``;
* the state is **not** cleared after a command;
* a known slash-command clears the state and is never sent into the session;
* **the trap, proved closed**: any non-``term:`` callback (``ws:view``) leaves the
  input mode, so the next plain message is normal navigation;
* ``⬅️ Back`` clears the state, renders the workspace home and does **not** kill
  the session;
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
from cloudbot.presentation.screens import terminal_callbacks as term_cb
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
    ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
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


# -- the bite: opening a terminal is being in it ------------------------------
async def test_open_then_plain_text_reaches_the_session_with_no_term_cmd():
    """TASK terminal-always' bite: ``term:open`` alone arms the input.

    Fails against the old design, where plain text reached a session only after a
    second tap on ``term:cmd``: here ``term:open`` is the **only** callback fed,
    then three plain messages, and all three must reach ``send_line``.
    """
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
        # No ``term:cmd`` between the open and the text — that button no longer
        # exists at all (asserted against the rendered keyboard below).
        for uid, text in enumerate(["echo one", "echo two", "echo three"], start=2):
            await dispatcher.feed_update(bot, _message(uid, TG, text))
        assert [s[3] for s in sends] == ["echo one", "echo two", "echo three"]
        assert {(s[0], s[1], s[2]) for s in sends} == {(TG, str(ws.id), session.id)}
        actions = _actions(bot.sent[0]["markup"])
        assert not any(a.startswith("term:cmd") for a in actions), (
            "the open screen still carries a send-command button"
        )
    finally:
        await container.aclose()


async def test_state_is_not_cleared_after_a_command():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
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
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
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
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
        await dispatcher.feed_update(bot, _message(2, TG, "/frobnicate"))
        assert sends == [], "an unknown command was sent into the session"
        assert await dispatcher.fsm.storage.get_state(_key(TG)) == TerminalInput.awaiting_command.state
    finally:
        await container.aclose()


# -- the trap, proved closed --------------------------------------------------
async def test_navigating_away_with_any_callback_leaves_the_input_mode():
    """The load-bearing hole: opening arms the input, so leaving must disarm it.

    ``term:open`` → a workspace-screen callback (``ws:view``) → plain text. The
    text must **not** reach a session: the callback is not a ``term:`` one, so the
    single callback seam clears the state before dispatch and the text takes the
    normal flow. Per-handler clearing is rejected precisely because it would miss a
    screen like this one and the failure would be silent.
    """
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
        assert await dispatcher.fsm.storage.get_state(_key(TG)) == TerminalInput.awaiting_command.state
        await dispatcher.feed_update(bot, _callback(2, TG, cb("ws:view", ws.id)))
        assert await dispatcher.fsm.storage.get_state(_key(TG)) is None, (
            "a non-term callback left the terminal input state armed"
        )
        await dispatcher.feed_update(bot, _message(3, TG, "echo beyond-the-trap"))
        assert sends == [], "a message on another screen was swallowed by the terminal"
    finally:
        await container.aclose()


# -- the non-destructive exit: ⬅️ Back ---------------------------------------
async def test_back_clears_the_state_renders_workspace_home_and_keeps_the_session():
    """``⬅️ Back`` leaves without killing: state cleared, home rendered, alive."""
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
        await dispatcher.feed_update(bot, _callback(2, TG, term_cb.exit_(ws.id, session.id)))
        assert await dispatcher.fsm.storage.get_state(_key(TG)) is None
        # The workspace home: its terminal button is one tap away again.
        actions = _actions(bot.sent[-1]["markup"])
        assert any(a.startswith("term:open") for a in actions), actions
        # Not killed: the same session is still running on the engine.
        assert not (await container.engine.get_session(ws.id, session.id)).status.is_terminal
        # A later plain message is no longer a command.
        await dispatcher.feed_update(bot, _message(3, TG, "echo after-back"))
        assert sends == [], "a plain message after Back reached the session"
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
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
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


# -- the view's own button set -----------------------------------------------
async def test_the_terminal_view_after_a_command_still_carries_its_buttons():
    container, ws, session, sends = await _harness()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
        await dispatcher.feed_update(bot, _message(2, TG, "echo one"))
        actions = _actions(bot.sent[-1]["markup"])
        # Exactly what a terminal needs: Ctrl-C, stop, and the non-destructive Back.
        assert any(a.startswith("term:signal") for a in actions)
        assert any(a.startswith("term:close") for a in actions)
        assert any(a.startswith("term:exit") for a in actions)
        # The question the user's answer already knows is gone.
        assert not any(a.startswith("term:cmd") for a in actions)
        # TASK terminal-direct: Ctrl-D no longer exists on the view at all.
        assert not any(a.startswith("term:ctrl_d") for a in actions)
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
        await dispatcher.feed_update(bot, _callback(1, TG, term_cb.open_terminal(ws.id)))
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

    class Packages:
        async def list(self, *a, **k):
            # The offer's installed check is reached only on a render; a
            # superseded follower must never get this far.
            raise AssertionError("a superseded follower fetched packages")

    class Bundle:
        sessions = FakeSessions()
        packages = Packages()

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

    class Packages:
        async def list(self, *a, **k):
            from cloudbot.domain.value_objects import PackageList

            return PackageList(
                packages=(), count=0, truncated=False, shared_enabled=True,
                shared_available=False, shared_path="", private_path="",
            )

    class Bundle:
        sessions = FakeSessions()
        packages = Packages()

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
    # term26 contract change: the resize input state is retired, so only command
    # mode is sticky now.
    state3 = _DictState(state=TerminalInput.awaiting_command.state)
    assert await _run_middleware("ls -la", state3) is True
    assert state3._state == TerminalInput.awaiting_command.state


# -- the callback seam: leaving by navigation (TASK terminal-always) ----------
def _run_leave_mw(data: str, state) -> bool:
    import asyncio

    from cloudbot.middleware.terminal_sticky import TerminalLeaveMiddleware

    called = {"handler": False}

    async def handler(event, data_):
        called["handler"] = True

    async def run():
        cbq = CallbackQuery.model_construct(data=data, id="1")
        mw = TerminalLeaveMiddleware()
        await mw(handler, cbq, {"state": state})

    asyncio.run(run())
    return called["handler"]


@pytest.mark.parametrize("data", ["ws:view:ws_x", "nav:menu", "pkg:list", "adm:menu"])
def test_leave_middleware_clears_any_non_term_callback(data):
    """Every non-`term:` callback leaves the input mode, before dispatch."""
    state = _DictState(state=TerminalInput.awaiting_command.state, data={"ws_id": "ws_x", "ses_id": SES})
    assert _run_leave_mw(data, state) is True
    assert state._state is None, f"{data!r} did not leave the terminal input state"


@pytest.mark.parametrize("data", ["term:open:ws_x", "term:signal:ws_x:ses:INT", "term:close:ws_x:ses"])
def test_leave_middleware_keeps_term_callbacks(data):
    """A `term:` interaction is still \"in the terminal\": the state is kept."""
    state = _DictState(state=TerminalInput.awaiting_command.state, data={"ws_id": "ws_x", "ses_id": SES})
    assert _run_leave_mw(data, state) is True
    assert state._state == TerminalInput.awaiting_command.state


def test_leave_middleware_is_a_no_op_outside_the_input_state():
    state = _DictState(state=None)
    assert _run_leave_mw("ws:view:ws_x", state) is True
    assert state._state is None


# -- the view's button set, and one destructive verb (both locales) ---------
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_live_view_carries_ctrl_c_stop_back_and_no_send_command(locale):
    """The exact button set the owner asked for, in both languages.

    Rewrite of the old ``stop typing`` tests: there is no command mode to start or
    stop, so the view carries **Ctrl-C**, **⏹ Stop terminal** (destructive, own
    row) and **⬅️ Back** (non-destructive, primary). No ``term:cmd`` and no
    ``term:exit``-as-"stop typing" copy any more.
    """
    from cloudbot.domain.entities import TerminalSession

    session = TerminalSession(id=SES, workspace_id="ws_01M6HJASCWM9Z1PF755GT89NK4", status=SessionStatus.RUNNING)
    screen = term_screens.terminal_view(session, "ws_01M6HJASCWM9Z1PF755GT89NK4", locale, typing=True)
    actions = [a for a in _actions(screen.markup) if a]
    assert any(a.startswith("term:signal:") for a in actions), "Ctrl-C is missing"
    assert any(a.startswith("term:close") for a in actions), "the stop control is missing"
    assert any(a.startswith("term:exit") for a in actions), "the Back control is missing"
    assert not any(a.startswith("term:cmd") for a in actions), "a send-command button is back"

    rows = screen.markup.inline_keyboard
    exit_button = next(b for row in rows for b in row if b.callback_data and b.callback_data.startswith("term:exit"))
    stop_button = next(b for row in rows for b in row if b.callback_data and b.callback_data.startswith("term:close"))
    # Back leaves without killing: primary, never the danger square.
    assert exit_button.style != "danger"
    # Stop kills: danger, and on its own row so a hand cannot tap it by habit.
    assert stop_button.style == "danger"
    stop_row = next(row for row in rows if any(b is stop_button for b in row))
    assert len(stop_row) == 1, "the destructive verb shares its row"
    # The button rows in order: Ctrl-C, Stop, Back.
    callback_rows = [
        [b.callback_data for b in row if b.callback_data]
        for row in rows
        if any(b.callback_data for b in row)
    ]
    assert callback_rows[0][0].startswith("term:signal")
    assert callback_rows[1][0].startswith("term:close")
    assert callback_rows[2][0].startswith("term:exit")


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_back_and_stop_terminal_do_not_share_a_symbol_or_a_row(locale):
    """Two different verbs must not look alike at a glance.

    ``⬅️ Back`` leaves without killing; ``⏹ Stop terminal`` kills. Reusing the
    danger square for both is how a hand taps the wrong one.
    """
    from cloudbot.domain.entities import TerminalSession

    session = TerminalSession(id=SES, workspace_id="ws_01M6HJASCWM9Z1PF755GT89NK4", status=SessionStatus.RUNNING)
    screen = term_screens.terminal_view(session, "ws_01M6HJASCWM9Z1PF755GT89NK4", locale, typing=True)
    rows = screen.markup.inline_keyboard
    back = next(b for row in rows for b in row if b.callback_data and b.callback_data.startswith("term:exit"))
    stop = next(b for row in rows for b in row if b.callback_data and b.callback_data.startswith("term:close"))
    assert back.text.split()[0] != stop.text.split()[0]
    assert not any(back is b for row in rows for b in row if any(b is stop for b in row))


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_live_view_is_always_an_input_view(locale):
    """There is no typing-less live view any more: the view *is* the input mode.

    ``typing`` no longer changes the button set — it only adds the active line —
    so a live view rendered without it still offers Back (and never Send).
    """
    from cloudbot.domain.entities import TerminalSession

    session = TerminalSession(id=SES, workspace_id="ws_01M6HJASCWM9Z1PF755GT89NK4", status=SessionStatus.RUNNING)
    plain = term_screens.terminal_view(session, "ws_01M6HJASCWM9Z1PF755GT89NK4", locale)
    actions = [a for a in _actions(plain.markup) if a]
    assert any(a.startswith("term:exit") for a in actions)
    assert not any(a.startswith("term:cmd") for a in actions)
