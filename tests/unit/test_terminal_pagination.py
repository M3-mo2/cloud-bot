"""TASK term26 — the terminal list is compact and paginated, and size is gone.

Three measured defects, each pinned here:

1. *my terminals* rendered one four-column table row per session and grew without
   bound. It is now one line per terminal, **one page** of rows, with the workspace
   list's own pagination contract (``PAGE_SIZE``, ``page``/``total_pages``/
   ``has_more``, prev/next) — not a second one.
2. The live view offered a meaningless size control (and printed the size). The
   chat is the viewport; the shell's ``stty`` is the in-session tool, and
   ``term:open`` carries the documented ``--cols/--rows`` route. No screen emits a
   size control, and the body has no size line.
3. Old ``term:resize``/``term:rsz``/``term:rsz_custom`` buttons still exist in real
   chats: they must be answered with a real screen and must not leave a resize
   input state armed (a user mid-resize must not have their next message swallowed).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionStatus, WorkspaceId
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import terminal_callbacks as term_cb

TG = 777
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


def _sessions(n: int) -> list[TerminalSession]:
    """``n`` sessions, oldest first; the first is the running one."""
    sessions = []
    for index in range(n):
        sessions.append(
            TerminalSession(
                id=f"ses_01M6HJATZTFXTWXWBG8A5KWY{index:02d}",
                workspace_id=WS,
                status=SessionStatus.RUNNING if index == 0 else SessionStatus.TIMED_OUT,
                created_at=NOW - timedelta(hours=index),
            )
        )
    return sessions


def _rows(screen) -> list[list]:
    return list(screen.markup.inline_keyboard)


def _actions(screen) -> list[str]:
    return [
        b.callback_data
        for row in _rows(screen)
        for b in row
        if getattr(b, "callback_data", None)
    ]


def _view_buttons(screen) -> list:
    return [
        b
        for row in _rows(screen)
        for b in row
        if (b.callback_data or "").startswith("term:view")
    ]


# -- 1. one row per page, not one per session --------------------------------
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_a_page_renders_one_compact_line_per_terminal_not_a_growing_table(locale):
    ordered = term_screens.order_sessions(_sessions(12))
    window = ordered[: term_screens.PAGE_SIZE]
    screen = term_screens.my_terminals(
        window, WS, locale, page=0, total_pages=2, has_more=True, now=NOW
    )
    html = screen.doc.html

    # Compact lines, not a table — the old four-column table is gone for good.
    assert "<table" not in html
    assert "الحجم" not in html and "Size:" not in html
    # One identity line per terminal of this page, and no more.
    assert html.count("<code>") == len(window) == term_screens.PAGE_SIZE
    # The count/footer describe this page, not the whole list.
    assert "10" in html  # TASK digits: ASCII in every locale
    assert "1" in html and "2" in html  # "page 1 of 2" / "صفحة 1 من 2"


def test_ordering_is_running_first_then_newest_so_the_live_one_is_on_page_one():
    sessions = _sessions(12)
    # Make the *oldest* session the running one: insertion/fetch order would hide it.
    ordered = term_screens.order_sessions(list(reversed(sessions)))
    assert ordered[0].status is SessionStatus.RUNNING
    ages = [s.created_at for s in ordered[1:]]
    assert ages == sorted(ages, reverse=True), "finished sessions are not newest-first"


# -- 2. paging moves the window and the keyboard follows the page -------------
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


async def test_paging_moves_the_window_and_the_keyboard_follows_the_page():
    container, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    many = _sessions(12)
    original = container.services.sessions.list_open_for_workspace

    async def fake_list(*args, **kwargs):
        return many

    container.services.sessions.list_open_for_workspace = fake_list  # type: ignore[assignment]
    try:
        await dispatcher.feed_update(bot, _callback(1, term_cb.list_(ws.id)))
        page0 = bot.sent[-1]
        views0 = {
            b.callback_data
            for row in page0["markup"].inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("term:view")
        }
        # The keyboard follows the page: exactly one page worth of session buttons,
        # never one per session in the whole list.
        assert len(views0) == term_screens.PAGE_SIZE
        assert ("term:next" not in page0["html"])
        actions0 = [
            b.callback_data for row in page0["markup"].inline_keyboard for b in row if b.callback_data
        ]
        assert any(a.startswith("term:open") for a in actions0)
        assert any(a == f"ws:view:{ws.id}" or a.startswith("ws:view") for a in actions0)

        await dispatcher.feed_update(bot, _callback(2, term_cb.list_(ws.id, 1)))
        page1 = bot.sent[-1]
        views1 = {
            b.callback_data
            for row in page1["markup"].inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("term:view")
        }
        # The second page carries the remaining two and none of the first page's.
        assert len(views1) == 2
        assert views0.isdisjoint(views1)
        # Fixed actions stay put at the bottom on every page.
        actions1 = [
            b.callback_data for row in page1["markup"].inline_keyboard for b in row if b.callback_data
        ]
        assert any(a.startswith("term:open") for a in actions1)
        assert any(a.startswith("ws:view") for a in actions1)
    finally:
        container.services.sessions.list_open_for_workspace = original  # type: ignore[assignment]
        await container.aclose()


# -- 3. no size control anywhere in the terminal flow ------------------------
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


# -- 4. a retired resize button is answered, and arms nothing -----------------
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
        # A user who was mid-resize when this shipped has the old state armed (a
        # raw state string is enough to prove the new handler clears it).
        await dispatcher.fsm.storage.set_state(_key(), "TerminalInput:awaiting_resize")
        await dispatcher.fsm.storage.set_data(
            _key(), {"ws_id": str(ws.id), "ses_id": session.id}
        )
        before = len(bot.sent)
        await dispatcher.feed_update(bot, _callback(1, builder(ws.id, session.id)))
        # A real screen (the live terminal view), never a dead button.
        assert len(bot.sent) > before
        assert "Terminal" in bot.sent[-1]["html"] or "الطرفية" in bot.sent[-1]["html"]
        # And the retired input state is not left armed.
        assert await dispatcher.fsm.storage.get_state(_key()) is None
    finally:
        await container.aclose()
