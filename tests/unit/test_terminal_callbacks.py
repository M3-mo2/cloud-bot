"""D2 — the ``term:`` callback codec and the stale-button fix.

Live defect (the log): the bot restarted at 14:38:03 and at 14:44:47 a user tapped
a terminal button that had been drawn by the **previous** process. Terminal
callbacks carry a workspace id and a session id, which because of Telegram's
64-byte cap are encoded through the generic content-addressed codec (CB-8): the
token is a hash of the key parts, resolved from a **process-global, in-memory**
registry. After the restart that registry was empty, so ``decode`` returned
``key=()`` and ``handlers/terminal.py:95`` (``on_list``) did
``_wid(decode(cb.data).key[0])`` → ``IndexError: tuple index out of range`` —
nothing rendered.

The ``term:`` codec (``presentation/screens/terminal_callbacks.py``) is now the
only place ``term:`` data is built and parsed, and its parser is **total**: a
stale/lost token or a malformed callback returns ``InvalidTermCallback`` instead of
raising, and every handler answers with the stale screen. The every-screen contract
test (``tests/contract/test_screen_callback_contract.py``) additionally proves no
screen emits a callback no handler can route — which also caught the dead
``term:resize`` button.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.presentation.keyboards.callbacks import CallbackCodec, codec
from cloudbot.presentation.screens import terminal as term_screens
from cloudbot.presentation.screens import terminal_callbacks as term_cb
from cloudbot.presentation.screens.terminal_callbacks import (
    InvalidTermCallback,
    TermCallback,
)

TG = 777
WS = "ws_01M6HJASCWM9Z1PF755GT89NK4"
SES = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"

#: Every builder, with representative arguments. The codec's parser must accept
#: each one and return the action + key parts the handler will read.
BUILDERS = [
    ("list", lambda: term_cb.list_(WS), ("list", (WS,))),
    ("open", lambda: term_cb.open_terminal(WS), ("open", (WS,))),
    ("view", lambda: term_cb.view(WS, SES), ("view", (WS, SES))),
    ("refresh", lambda: term_cb.refresh(WS, SES), ("refresh", (WS, SES))),
    ("cmd", lambda: term_cb.cmd(WS, SES), ("cmd", (WS, SES))),
    ("exit", lambda: term_cb.exit_(WS, SES), ("exit", (WS, SES))),
    ("signal", lambda: term_cb.signal(WS, SES, "INT"), ("signal", (WS, SES, "INT"))),
    ("ctrl_d", lambda: term_cb.ctrl_d(WS, SES), ("ctrl_d", (WS, SES))),
    ("resize", lambda: term_cb.resize(WS, SES), ("resize", (WS, SES))),
    ("rsz", lambda: term_cb.rsz(WS, SES, "120x40"), ("rsz", (WS, SES, "120x40"))),
    ("rsz_custom", lambda: term_cb.rsz_custom(WS, SES), ("rsz_custom", (WS, SES))),
    ("close", lambda: term_cb.close(WS, SES), ("close", (WS, SES))),
    ("forget", lambda: term_cb.forget(WS, SES), ("forget", (WS, SES))),
]


@pytest.mark.parametrize("name,builder,expected", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_builder_round_trips(name, builder, expected):
    data = builder()
    assert data.startswith("term:")
    assert len(data.encode("utf-8")) <= 64, data
    parsed = term_cb.parse(data)
    assert isinstance(parsed, TermCallback), parsed
    assert parsed.valid is True
    assert (parsed.action, parsed.args) == expected


def test_the_parsed_keys_the_handlers_read_are_present():
    """The exact fields the handlers index — never a lost tuple."""
    parsed = term_cb.parse(term_cb.view(WS, SES))
    assert parsed.ws_id == WS
    assert parsed.ses_id == SES
    assert parsed.args[0] == WS and parsed.args[1] == SES
    sig = term_cb.parse(term_cb.signal(WS, SES, "INT"))
    assert sig.args[2] == "INT"


# -- the total, defensive parser ---------------------------------------------
def test_a_lost_token_is_stale_not_an_index_error(the_token_is_lost):
    """The exact live defect: a button drawn before a restart decodes to no key.

    ``decode`` (the global codec) returns ``key=()``; the parser must reject it with
    a reason, and the handler must never reach ``key[0]``.
    """
    data = the_token_is_lost  # e.g. "term:list:<token no longer registered>"
    assert data.startswith("term:list:")
    parsed = term_cb.parse(data)
    assert isinstance(parsed, InvalidTermCallback)
    assert parsed.valid is False
    assert parsed.reason == "stale"
    assert parsed.action == "list"


@pytest.fixture
def the_token_is_lost():
    """Produce a real ``term:list`` callback, then simulate a restart (empty registry)."""
    fresh = CallbackCodec()
    data = fresh.encode("term:list", WS)
    # A new process: the registry that mapped the token back to its key parts is gone.
    codec.reset()
    try:
        assert codec.decode(data).key == (), "the restart simulation did not clear the registry"
        yield data
    finally:
        codec.reset()


MALFORMED = [
    "",
    "term",
    "term:",
    "term:list",  # no token at all
    "term:nope:" + "x" * 6,  # unknown action
    "nav:menu",  # not a term callback
    "adm:menu",
]


@pytest.mark.parametrize("data", MALFORMED, ids=[repr(d) for d in MALFORMED])
def test_malformed_callbacks_return_invalid_not_raise(data):
    parsed = term_cb.parse(data)
    assert isinstance(parsed, InvalidTermCallback), parsed
    assert parsed.valid is False


def test_parse_never_raises_for_arbitrary_text():
    for text in ("", ":", "term:::::", "🖥", "term:list:" + "x" * 400):
        parsed = term_cb.parse(text)
        assert parsed.valid is True or isinstance(parsed, InvalidTermCallback)


# -- through the real dispatcher ---------------------------------------------
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append({"html": rich_message.html, "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def __call__(self, method):
        return True


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


def _key(bot, user_id):
    return StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)


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
    ws = await container.services.workspaces.create(TG, "mine")
    return container, ws


async def test_the_live_indexerror_callback_now_renders_a_screen():
    """The exact live path: a stale ``term:list`` button must render the stale
    screen instead of raising ``IndexError`` (and must not send nothing)."""
    container, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # ``workspaces.create`` already registered ownership for TG.
        # Build the callback while the registry is warm, then lose it (a restart).
        data = codec.encode("term:list", ws.id)
        codec.reset()
        before = len(bot.sent)
        await dispatcher.feed_update(bot, _callback(1, TG, data))
        # Not silent, and no crash: the stale screen came back.
        assert len(bot.sent) == before + 1, "the stale button rendered nothing"
        html = bot.sent[-1]["html"]
        assert "out of date" in html or "قديم" in html, html[:200]
    finally:
        await container.aclose()


async def test_a_valid_terminal_list_callback_renders_the_list():
    container, ws = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        data = term_cb.list_(ws.id)
        await dispatcher.feed_update(bot, _callback(1, TG, data))
        assert bot.sent, "nothing rendered"
        html = bot.sent[-1]["html"]
        assert "My terminals" in html or "طرفياتي" in html, html[:200]
    finally:
        await container.aclose()


async def test_every_terminal_button_in_the_terminals_screen_renders_a_screen():
    """Walk every ``term:`` button the my-terminals screen emits; each is routable
    and produces a screen (never a silent no-op)."""
    container, ws = await _container()
    session = await container.services.sessions.open(TG, ws.id)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        screen = term_screens.my_terminals(
            [session], ws.id, Locale.EN
        )
        actions = {
            b.callback_data
            for row in screen.markup.inline_keyboard
            for b in row
            if b.callback_data and b.callback_data.startswith("term:")
        }
        assert actions, "the terminals screen emitted no term: buttons"
        for uid, data in enumerate(sorted(actions), start=1):
            before = len(bot.sent)
            await dispatcher.feed_update(bot, _callback(uid, TG, data))
            assert len(bot.sent) > before, f"{data!r} rendered nothing"
    finally:
        await container.aclose()
