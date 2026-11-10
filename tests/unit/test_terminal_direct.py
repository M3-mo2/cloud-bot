"""TASK terminal-direct — the terminal button opens a terminal, not an archive.

What he saw: pressing the workspace's **🖥 الطرفيات** landed on a paginated list of
every session ever opened — ten dead rows across three pages, none of which opened
anything. His words: *"what is this even"*; what he asked for: *"a user opens a
terminal immediately, can execute commands in it, with a stop-terminal button and
ctrl+c, and that's it — not to land on a dumb list like this."*

These tests pin the replacement, end to end through the real dispatcher:

* the workspace button **opens or reuses** a session and renders its live view —
  the bite, which fails against the deleted ``my_terminals``;
* a second tap **reuses** the live session (one engine create, not two);
* a session that **ended** is never reused (a tap creates a fresh one);
* the live view carries ``Ctrl-C`` and one stop control, and **no** ``Ctrl-D`` in
  the top row;
* **stop** returns to the workspace home, never to a list route;
* finished handles are **forgotten** after a grace window, and stay viewable
  inside it;
* no module references the deleted archive route or screen (a grep-style guard).

The size-control and retired-resize tests from TASK term26 are kept unchanged: they
were about a different defect and still hold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionId, SessionStatus, WorkspaceId
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import terminal_callbacks as term_cb
from cloudbot.services.sessions import FORGET_GRACE

TG = 777
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]

def _rows(screen) -> list[list]:
    return list(screen.markup.inline_keyboard)

def _actions(screen) -> list[str]:
    return [
        b.callback_data
        for row in _rows(screen)
        for b in row
        if getattr(b, "callback_data", None)
    ]

# -- harness -----------------------------------------------------------------
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append({"html": rich_message.html, "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def __call__(self, method):
        return True

def _callback(uid: int, data: str) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=TG, is_bot=False, first_name="A"),
            chat_instance="c",
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=TG, type="private"),
                from_user=User(id=TG, is_bot=False, first_name="A"),
                text="screen",
            ),
            data=data,
        ),
    )

def _key() -> StorageKey:
    return StorageKey(bot_id=FakeBot.id, chat_id=TG, user_id=TG)

async def _container():
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
    await container.users.get_or_create(TG, default_locale=Locale.EN)
    ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
    return container, ws

async def _ended_session(container, ws, *, ended_ago: timedelta) -> str:
    """Open then close a session, and backdate the bot-side terminal stamp."""
    session = await container.services.sessions.open(TG, ws.id)
    await container.services.sessions.close(TG, ws.id, session.id)
    stamp = datetime.now(timezone.utc) - ended_ago
    await container.sessions.mark_ended(TG, SessionId(session.id), stamp)
    return session.id

# -- 1. the bite: a terminal, not a list -------------------------------------
async def test_the_bite_ended_sessions_do_not_displace_a_live_terminal():
    """Two ended sessions + one live one → a live view, never a list.

    This is the assertion that fails against ``my_terminals``: that screen
    rendered every session (ended first-class) and nothing on it opened a prompt.
    """
    container, ws = await _container()
    await _ended_session(container, ws, ended_ago=timedelta(minutes=5))
    await _ended_session(container, ws, ended_ago=timedelta(minutes=3))
    live = await container.services.sessions.open(TG, ws.id)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, term_cb.open_terminal(ws.id)))
        assert bot.sent, "nothing rendered"
        sent = bot.sent[-1]
        html = sent["html"]
        actions = [
            b.callback_data
            for row in sent["markup"].inline_keyboard
            for b in row
            if b.callback_data
        ]
        # A live terminal view: its status line and its command row.
        assert "Running" in html or "قيد التشغيل" in html, html[:200]
        assert any(a.startswith("term:cmd") for a in actions), "no command row"
        assert any(a.startswith("term:signal") for a in actions), "no Ctrl-C"
        assert any(a.startswith("term:close") for a in actions), "no stop control"
        # Never the archive: no route to it, and no per-session view list.
        assert not any("term:list" in a for a in actions)
        assert not any(a.startswith("term:view") for a in actions)
    finally:
        await container.aclose()

# -- 2. open-or-reuse --------------------------------------------------------
async def test_a_second_tap_reuses_the_live_session():
    """Two taps with a live session produce ONE engine session (assert the count)."""
    container, ws = await _container()
    creates: list[int] = []
    real_open = container.engine.open_session

    async def counting_open(*args, **kwargs):
        creates.append(1)
        return await real_open(*args, **kwargs)

    container.engine.open_session = counting_open  # type: ignore[method-assign]
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, term_cb.open_terminal(ws.id)))
        first_html = bot.sent[-1]["html"]
        await dispatcher.feed_update(bot, _callback(2, term_cb.open_terminal(ws.id)))
        assert len(creates) == 1, f"the second tap opened another session ({len(creates)})"
        # Both renders are the same live session's view.
        assert "Running" in first_html or "قيد التشغيل" in first_html
        assert bot.sent[-1]["html"] == first_html or "Running" in bot.sent[-1]["html"] or "قيد التشغيل" in bot.sent[-1]["html"]
        live = await container.services.sessions.list_for_workspace(TG, ws.id)
        assert len([s for s in live if s.status is SessionStatus.RUNNING]) == 1
    finally:
        container.engine.open_session = real_open  # type: ignore[method-assign]
        await container.aclose()

async def test_an_ended_session_is_not_reused():
    """Only an ended session present → a tap creates a new one."""
    container, ws = await _container()
    ended = await _ended_session(container, ws, ended_ago=timedelta(minutes=5))
    creates: list[int] = []
    real_open = container.engine.open_session

    async def counting_open(*args, **kwargs):
        creates.append(1)
        return await real_open(*args, **kwargs)

    container.engine.open_session = counting_open  # type: ignore[method-assign]
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, term_cb.open_terminal(ws.id)))
        assert len(creates) == 1, "an ended session was reused"
        actions = _actions_from(bot.sent[-1])
        assert any(a.startswith("term:cmd") for a in actions), "ended session's view rendered"
        # The ended handle is still present (inside the grace window), the new one too.
        ids = {s.id for s in await container.services.sessions.list_for_workspace(TG, ws.id)}
        assert ended in ids and len(ids) == 2
    finally:
        container.engine.open_session = real_open  # type: ignore[method-assign]
        await container.aclose()

# -- 3. the live view's exact controls --------------------------------------
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_live_view_carries_ctrl_c_and_a_stop_and_no_ctrl_d(locale):
    session = TerminalSession(id="ses_01M6HJATZTFXTWXWBG8A5KWYS8", workspace_id=WS, status=SessionStatus.RUNNING)
    screen = term_screens.terminal_view(session, WS, locale, output="$ ")
    actions = _actions(screen)
    assert any(a.startswith("term:signal:") for a in actions), "Ctrl-C is missing"
    assert any(a.startswith("term:close") for a in actions), "the stop control is missing"
    assert not any(a.startswith("term:ctrl_d") for a in actions), "Ctrl-D is still in the view"
    # One destructive verb only: no second "close"-shaped action.
    close_like = [a for a in actions if a.startswith("term:close") or a.startswith("term:forget")]
    assert len(close_like) == 1, f"two exit verbs on a live view: {close_like}"
    # The top row is the command row: send + Ctrl-C.
    first_row = [b.callback_data for b in _rows(screen)[0] if b.callback_data]
    assert any(a.startswith("term:cmd") for a in first_row)
    assert any(a.startswith("term:signal") for a in first_row)

# -- 4. stop returns to the workspace home -----------------------------------
async def test_stop_returns_to_the_workspace_home_never_a_list():
    container, ws = await _container()
    session = await container.services.sessions.open(TG, ws.id)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, term_cb.close(ws.id, session.id)))
        html = bot.sent[-1]["html"]
        actions = _actions_from(bot.sent[-1])
        # The workspace home, not a terminal list and not the dead session's view:
        # its tool grid is on screen and the terminal button is one tap away again.
        assert any(a.startswith("file:ls") for a in actions), actions
        assert any(a.startswith("term:open") for a in actions), actions
        assert not any("term:list" in a for a in actions)
        assert "Terminal closed" not in html and "أُغلق" not in html
        # The session really stopped on the engine ...
        assert (await container.engine.get_session(ws.id, session.id)).status.is_terminal
        # ... and its bot-side handle was forgotten (stop is "done with it").
        assert await container.sessions.get(TG, SessionId(session.id)) is None
    finally:
        await container.aclose()

def _actions_from(sent: dict) -> list[str]:
    return [
        b.callback_data
        for row in sent["markup"].inline_keyboard
        for b in row
        if getattr(b, "callback_data", None)
    ]

# -- 4b. the capacity screen names what to close ----------------------------
def test_the_capacity_screen_offers_a_stop_for_each_live_session():
    """When the cap refuses, the message says what to stop and how.

    Only live sessions are listed: a stop control on a dead one would free
    nothing. This is the reachable answer to ``terminal.too_many`` the task
    requires, and it is not the deleted archive.
    """
    live = [
        TerminalSession(id="ses_01M6HJATZTFXTWXWBG8A5KWYS8", workspace_id=WS, status=SessionStatus.RUNNING),
        TerminalSession(id="ses_01M6HJATZTFXTWXWBG8A5KWYS9", workspace_id=WS, status=SessionStatus.RUNNING),
    ]
    screen = term_screens.cap_screen(live, WS, Locale.EN)
    actions = _actions(screen)
    stops = [a for a in actions if a.startswith("term:close")]
    assert len(stops) == 2, f"each live session needs its own stop: {actions}"
    assert any(a == f"ws:view:{WS}" for a in actions)
    assert not any("term:list" in a for a in actions)
    assert "limit" in screen.doc.html.lower()

def test_term_new_opens_a_fresh_session_even_when_one_is_live():
    """The dead view's ``➕ New terminal`` is not the reuse route.

    A finished session can never run again, so the action offered on its view must
    create a session; reusing a live one from a *dead* view would silently show a
    different terminal than the screen the button was on.
    """
    import asyncio

    async def scenario():
        container, ws = await _container()
        live = await container.services.sessions.open(TG, ws.id)
        creates: list[int] = []
        real_open = container.engine.open_session

        async def counting_open(*args, **kwargs):
            creates.append(1)
            return await real_open(*args, **kwargs)

        container.engine.open_session = counting_open  # type: ignore[method-assign]
        bot = FakeBot()
        dispatcher = build_dispatcher(container, bot)
        try:
            await dispatcher.feed_update(bot, _callback(1, term_cb.new_terminal(ws.id)))
            assert len(creates) == 1, "term:new reused a live session"
            ids = {s.id for s in await container.services.sessions.list_for_workspace(TG, ws.id)}
            assert live.id in ids and len(ids) == 2
        finally:
            container.engine.open_session = real_open  # type: ignore[method-assign]
            await container.aclose()

    asyncio.run(scenario())

# -- 5. the archive cannot creep back ---------------------------------------
def test_no_module_registers_or_references_the_terminal_archive():
    """A grep-style guard: the deleted route, screen and destination stay gone.

    The scan is over source, so a route re-registered (or a fixture builder that
    renders the old list) fails here even if no test taps it.
    """
    forbidden = ("term:list", "my_terminals", "nav:terminals")
    offenders = []
    for path in (REPO_ROOT / "cloudbot").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
    assert not offenders, "the terminal archive is back:\n" + "\n".join(offenders)
    # The screen is not merely unregistered: it does not exist.
    assert not hasattr(term_screens, "my_terminals")
    assert not hasattr(term_screens, "my_terminals_markup")

# -- 6. forgetting, with the grace window ------------------------------------
async def test_a_finished_session_older_than_the_grace_window_is_forgotten():
    container, ws = await _container()
    old = await _ended_session(container, ws, ended_ago=FORGET_GRACE + timedelta(hours=1))
    await container.services.sessions.forget_finished(TG, ws.id)
    assert await container.sessions.get(TG, SessionId(old)) is None, "the corpse survived"
    assert await container.services.sessions.list_for_workspace(TG, ws.id) == []
    await container.aclose()

async def test_a_finished_session_inside_the_window_stays_viewable():
    container, ws = await _container()
    recent = await _ended_session(container, ws, ended_ago=FORGET_GRACE - timedelta(minutes=10))
    assert await container.services.sessions.forget_finished(TG, ws.id) == 0
    handle = await container.sessions.get(TG, SessionId(recent))
    assert handle is not None, "a session inside the window was forgotten"
    # And its view still renders readably (the user may still be looking at it).
    view = term_screens.terminal_view(
        await container.services.sessions.get(TG, ws.id, recent), ws.id, Locale.EN
    )
    assert "Terminal" in view.doc.html or "الطرفية" in view.doc.html
    await container.aclose()

# -- 7. no size control anywhere in the terminal flow (kept from term26) -----
SIZE_ACTIONS = ("term:resize", "term:rsz", "term:rsz_custom")

@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_no_terminal_screen_offers_a_size_control(locale):
    fixtures = screen_catalog.fixtures()
    checked = 0
    for name, build in screen_catalog.SCREEN_BUILDERS.items():
        if not name.startswith("terminal"):
            continue
        screen = build(fixtures, locale)
        for action in _actions(screen):
            checked += 1
            assert not action.startswith(SIZE_ACTIONS), f"{name} still offers {action!r}"
    assert checked > 0, "no terminal buttons were checked — the test is vacuous"

@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_the_view_body_carries_no_size_line(locale):
    session = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=WS,
        status=SessionStatus.RUNNING,
        created_at=NOW,
        cols=80,
        rows=24,
    )
    html = term_screens.terminal_view(session, WS, locale, output="$ ").doc.html
    assert "الحجم" not in html
    assert "Size:" not in html
    assert "80×24" not in html  # and the Arabic-Indic form is gone too

# -- 8. a retired resize button is answered, and arms nothing (kept) ----------
@pytest.mark.parametrize(
    "builder",
    [term_cb.resize, term_cb.rsz_custom, lambda w, s: term_cb.rsz(w, s, "120x40")],
    ids=["resize", "rsz_custom", "rsz"],
)
async def test_a_retired_resize_button_renders_the_view_and_arms_no_input_state(builder):
    container, ws = await _container()
    session = await container.services.sessions.open(TG, ws.id)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.fsm.storage.set_state(_key(), "TerminalInput:awaiting_resize")
        await dispatcher.fsm.storage.set_data(_key(), {"ws_id": str(ws.id), "ses_id": session.id})
        before = len(bot.sent)
        await dispatcher.feed_update(bot, _callback(1, builder(ws.id, session.id)))
        assert len(bot.sent) > before
        assert "Terminal" in bot.sent[-1]["html"] or "الطرفية" in bot.sent[-1]["html"]
        assert await dispatcher.fsm.storage.get_state(_key()) is None
    finally:
        await container.aclose()
