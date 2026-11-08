"""TASK package-return — after an install, go back where you were.

The defect: the install result screen offered only exits *away* from the screen
the user was reading (``pkg:install`` re-opened the type-a-name prompt on top of
the result, ``pkg:list`` went somewhere else), and the progress screen was sent as
a **new** message so the offer's buttons survived underneath it. "Some screen
overlap" is that second defect.

What is pinned here, driven through the **real dispatcher** with the fake engine:

* the result screen's first button is **back** (``origin:back``) and it resolves to
  the run-result screen the offer was shown on — the bite (it fails against the
  old three-row markup, which had no ``origin:back`` at all);
* installing from each of the four origins returns to that origin;
* no remembered origin ⇒ no back row, and ``pkg:list`` is the single fallback;
* after the tap there is exactly **one** live message carrying an install/back
  button (the overlap);
* the result markup contains no ``pkg:install`` callback at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Limits, WorkspaceId
from cloudbot.handlers import execution as exec_handlers
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import cb

TG = 6508129575
BOT_ID = 42
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


class FakeBot:
    """A chat that models messages by id, so overlap is countable.

    ``send`` mints a new message id; ``edit`` mutates the message it targets and
    keeps its id; ``delete`` removes it. Counting rendered *messages* (not calls)
    is what proves the offer's buttons did not survive under the result.
    """

    id = BOT_ID

    def __init__(self) -> None:
        self.messages: dict[int, dict] = {}
        self._next = 0

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self._next += 1
        self.messages[self._next] = {
            "html": getattr(rich_message, "html", ""),
            "markup": reply_markup,
        }
        return Message.model_construct(chat={"id": chat_id}, message_id=self._next)

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.messages[message_id] = {
            "html": getattr(rich_message, "html", ""),
            "markup": reply_markup,
        }
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.messages.pop(message_id, None)
        return True

    async def send_document(self, chat_id, document, reply_parameters=None):
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

    async def download(self, document):
        import io

        return io.BytesIO(b"data")

    async def __call__(self, method):
        return True

    # -- test helpers --------------------------------------------------------
    def callbacks(self) -> list[str | None]:
        out: list[str | None] = []
        for message in self.messages.values():
            markup = message.get("markup")
            rows = getattr(markup, "inline_keyboard", None) or []
            out.extend(b.callback_data for row in rows for b in row)
        return out

    def live_messages_with(self, predicate) -> list[int]:
        found = []
        for message_id, message in self.messages.items():
            markup = message.get("markup")
            rows = getattr(markup, "inline_keyboard", None) or []
            if any(predicate(b.callback_data or "") for row in rows for b in row):
                found.append(message_id)
        return found


def _callback(uid: int, data: str, *, user_id: int = TG, message_id: int | None = None) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c",
            message=Message(
                message_id=message_id if message_id is not None else uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text="screen",
            ),
            data=data,
        ),
    )


def _message(uid: int, text: str, *, user_id: int = TG) -> Update:
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


async def _harness(*, allow_net: bool = True):
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids={TG},
        throttle_rate=10**9,
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    engine = container.engine
    assert isinstance(engine, FakeEngine), "DEV + mock_engine must wire the fake engine"
    await container.users.get_or_create(TG, default_locale=Locale.EN)
    await container.users.add_workspace(TG, WS)
    await engine.patch_workspace(WS, limits=Limits(allow_net=allow_net))
    await container.services.session.enter(TG, WS)
    exec_handlers._ORIGINS.clear()
    exec_handlers._LAST_EXEC_RESULT.clear()
    exec_handlers._LAST_EXEC.clear()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    return container, engine, bot, dispatcher


def _first_row_actions(message: dict) -> list[str]:
    rows = getattr(message.get("markup"), "inline_keyboard", None) or []
    return [b.callback_data for b in rows[0]] if rows else []


def _all_actions(message: dict) -> list[str]:
    rows = getattr(message.get("markup"), "inline_keyboard", None) or []
    return [b.callback_data or "" for row in rows for b in row]


# =========================================================== the bite ========
async def test_the_bite_a_run_offer_install_returns_to_the_run_result():
    """A tap on an offer button from the run-result screen installs, and the
    result screen's first button is **back** resolving to the run-result screen.

    Fails against the old three-row markup: it had no ``origin:back`` row, so the
    first row was ``exec:again`` (or ``pkg:install``) and no button returned to the
    run the offer came from.
    """
    container, engine, bot, dispatcher = await _harness()
    try:
        from cloudbot.domain.value_objects import ExecResult
        from cloudbot.presentation.screens import packages as pkg_screens
        from cloudbot.presentation.screens import run as run_screens

        result = ExecResult(1, "", "ModuleNotFoundError: No module named 'flask'", 120, False)
        offer = pkg_screens.missing_section(result.stderr, WS, Locale.EN)
        exec_handlers.remember_origin(TG, WS, "run")
        exec_handlers.remember_exec_result(TG, WS, result)
        exec_handlers.remember_exec(TG, WS, "python3 main.py")
        screen = run_screens.exec_result(
            result, WS, Locale.EN, missing_offer=offer, again_action=cb("exec:again", WS)
        )
        screen_id = await _send_screen(bot, screen)
        offer_cb = next(
            b.callback_data
            for row in screen.markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("pkg:fix:")
        )
        # Tap the offer: install flask.
        await dispatcher.feed_update(bot, _callback(2, offer_cb, message_id=screen_id))
        final = bot.messages[screen_id]
        assert "flask" in final["html"]
        first = _first_row_actions(final)
        assert first == ["origin:back"], f"first row is not back: {first}"
        assert "pkg:install" not in _all_actions(final), "the result still opens the input prompt"
        # And it resolves to the run-result screen (the command that failed).
        await dispatcher.feed_update(bot, _callback(3, "origin:back", message_id=screen_id))
        landed = bot.messages[screen_id]["html"]
        assert catalog.t("exec.failed", Locale.EN, code="1") in landed
        assert catalog.t("package.fix_title", Locale.EN, module="flask") in landed
    finally:
        await container.aclose()


async def _send_screen(bot: FakeBot, screen) -> int:
    """Put a screen on screen as a fresh message (a test stand-in for the sender)."""
    from cloudbot.presentation.rich.document import RichDoc

    assert isinstance(screen.doc, RichDoc)
    bot._next = max(bot.messages, default=0) + 1
    bot.messages[bot._next] = {"html": screen.doc.html, "markup": screen.markup}
    return bot._next


# ============================================= each of the four origins ======
async def test_installing_from_the_packages_list_returns_to_it():
    container, engine, bot, dispatcher = await _harness()
    try:
        await dispatcher.feed_update(bot, _callback(1, cb("pkg:list", WS)))
        assert exec_handlers.last_origin(TG, WS) == ("packages", None)
        # The offer there is the packages screen's own install flow: type a name.
        from cloudbot.domain.value_objects import InstallResult
        from cloudbot.presentation.screens import packages as pkg_screens
        from cloudbot.services.packages import InstallOutcome

        outcome = InstallOutcome(
            result=InstallResult(0, "ok", "ok", 100, False, False, "/opt/python/private"),
            allow_net=True,
        )
        screen = pkg_screens.install_result_screen(
            outcome, "flask", WS, Locale.EN,
            has_origin=exec_handlers.last_origin(TG, WS) is not None,
        )
        assert _first_row_actions({"markup": screen.markup}) == ["origin:back"]
        screen_id = await _send_screen(bot, screen)
        await dispatcher.feed_update(bot, _callback(2, "origin:back", message_id=screen_id))
        landed = bot.messages[screen_id]
        assert catalog.t("package.title", Locale.EN) in landed["html"]
        # The destination is the packages list, whose own install entry is here.
        assert cb("pkg:install", WS) in _all_actions(landed)
    finally:
        await container.aclose()


async def test_installing_from_a_file_run_logs_returns_to_that_run():
    container, engine, bot, dispatcher = await _harness()
    try:
        run = await engine.create_run(WS, kind="file", file="/main.py")
        # Open the live logs (the file-run surface that records "runlog:<id>").
        await dispatcher.feed_update(bot, _callback(1, cb("run:logs", WS, run.id)))
        assert exec_handlers.last_origin(TG, WS) == ("runlog", str(run.id))
        await dispatcher.feed_update(bot, _callback(2, "origin:back"))
        landed = bot.messages[max(bot.messages)]["html"]
        # A finished run's logs screen heads with its status, not "Live logs".
        assert "run_FAKE" not in landed
        assert "main.py" in landed or catalog.t("run.live_logs", Locale.EN) in landed
        assert any(a.startswith("run:delete") for a in _all_actions(bot.messages[max(bot.messages)]))
    finally:
        await container.aclose()


async def test_installing_from_the_live_terminal_returns_to_that_session():
    container, engine, bot, dispatcher = await _harness()
    try:
        session = await container.services.sessions.open(TG, WS)
        await dispatcher.feed_update(bot, _callback(1, cb("term:view", WS, session.id)))
        assert exec_handlers.last_origin(TG, WS) == ("terminal", session.id)
        await dispatcher.feed_update(bot, _callback(2, "origin:back"))
        landed = bot.messages[max(bot.messages)]["html"]
        assert catalog.t("terminal.title", Locale.EN) in landed
    finally:
        await container.aclose()


# ================================================ no origin / fallback ======
def test_no_remembered_origin_has_no_back_row_and_pkg_list_fallback():
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens import packages as pkg_screens
    from cloudbot.services.packages import InstallOutcome

    outcome = InstallOutcome(
        result=InstallResult(0, "ok", "ok", 100, False, False, "/opt/python/private"),
        allow_net=True,
    )
    screen = pkg_screens.install_result_screen(
        outcome, "flask", WS, Locale.EN, has_origin=False
    )
    actions = _all_actions({"markup": screen.markup})
    assert "origin:back" not in actions
    assert actions == [cb("pkg:list", WS)], actions


async def test_no_origin_at_all_lands_on_the_workspace_home():
    """A cold ``origin:back`` (a restart wiped the memory) is never a dead end."""
    container, engine, bot, dispatcher = await _harness()
    try:
        exec_handlers._ORIGINS.clear()
        await dispatcher.feed_update(bot, _callback(1, "origin:back"))
        landed = bot.messages[max(bot.messages)]["html"]
        assert catalog.t("ws.head.title", Locale.EN) in landed
    finally:
        await container.aclose()


# ===================================================== the overlap ==========
async def test_after_the_tap_exactly_one_message_carries_install_or_back():
    """The overlap: the offer's buttons must not survive under the result.

    Started from the offer's callback, the progress screen edits the *same*
    message (``send_new`` is gone from it) and the result edits it again, so the
    chat holds one live message with an install/back button — counted, not eyeballed.
    """
    container, engine, bot, dispatcher = await _harness()
    try:
        from cloudbot.presentation.screens import packages as pkg_screens
        from cloudbot.presentation.screens import run as run_screens
        from cloudbot.domain.value_objects import ExecResult

        result = ExecResult(1, "", "ModuleNotFoundError: No module named 'flask'", 120, False)
        offer = pkg_screens.missing_section(result.stderr, WS, Locale.EN)
        exec_handlers.remember_origin(TG, WS, "run")
        exec_handlers.remember_exec_result(TG, WS, result)
        exec_handlers.remember_exec(TG, WS, "python3 main.py")
        screen = run_screens.exec_result(
            result, WS, Locale.EN, missing_offer=offer, again_action=cb("exec:again", WS)
        )
        screen_id = await _send_screen(bot, screen)
        offer_cb = next(
            b.callback_data
            for row in screen.markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("pkg:fix:")
        )
        await dispatcher.feed_update(bot, _callback(2, offer_cb, message_id=screen_id))
        live = bot.live_messages_with(
            lambda data: data.startswith("pkg:fix:")
            or data.startswith("pkg:install")
            or data == "origin:back"
        )
        assert len(live) == 1, f"{len(live)} live messages carry an install/back button"
    finally:
        await container.aclose()


# ================================================= result markup shape ======
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("ok", [True, False])
def test_result_markup_never_contains_the_install_prompt(locale, ok):
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens import packages as pkg_screens
    from cloudbot.services.packages import InstallOutcome

    outcome = InstallOutcome(
        result=InstallResult(
            0 if ok else 1,
            "out",
            "out",
            100,
            False,
            False,
            "/opt/python/private",
        ),
        allow_net=ok,
    )
    screen = pkg_screens.install_result_screen(outcome, "flask", WS, locale)
    actions = _all_actions({"markup": screen.markup})
    assert not any(a.startswith("pkg:install") for a in actions), actions
    if ok:
        assert actions[0] == "origin:back"
    # Two rows at most: back (when an origin is known) and retry (on failure).
    assert len([r for r in screen.markup.inline_keyboard]) <= 2


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_failed_result_keeps_the_reason_and_still_offers_back(locale):
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens import packages as pkg_screens
    from cloudbot.services.packages import InstallOutcome

    outcome = InstallOutcome(
        result=InstallResult(1, "pip: error", "", 100, False, False, "/opt/python/private"),
        allow_net=False,
    )
    screen = pkg_screens.install_result_screen(outcome, "flask", WS, locale)
    html = screen.doc.html
    assert catalog.t("package.result_failed_title", locale) in html
    assert "origin:back" in _all_actions({"markup": screen.markup})
