"""TASK package-offer-fresh — stop offering what is already installed.

The report: a terminal install succeeded and the same command then ran fine, but
the install button was still on the screen. Two causes, both pinned here:

* ``missing_section`` had no idea what was installed — it offered from the
  scrollback's old failure line; and
* it read the **whole** buffer, so a failure that scrolled away kept counting.

The offer is now answered by facts (the workspace's real package list) and by the
**newest** command's block of the displayed tail. The tests below are the ones the
task asks for: the bite (install, re-render, no offer), the distribution-name
normaliser, the tail guard, the per-module skip, the degraded ``None`` path, and
the "each install callback once" keyboard assertion.
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
from cloudbot.domain.python_packages import (
    is_installed,
    normalize_distribution_name,
)
from cloudbot.domain.value_objects import ExecResult, Limits, Package, PackageList, WorkspaceId
from cloudbot.handlers import execution as exec_handlers
from cloudbot.presentation.screens.packages import last_failure, missing_section
from cloudbot.presentation.screens.run import exec_result

TG = 6508129575
BOT_ID = 42
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")

FAIL = "ModuleNotFoundError: No module named 'flask'"


def _offer(stderr: str, *, installed=None, locale=Locale.EN):
    return missing_section(stderr, WS, locale, installed=installed)


# ------------------------------------------------------------------ normalise
@pytest.mark.parametrize(
    "left,right",
    [
        ("Pillow", "pillow"),
        ("pillow", "PILLOW"),
        ("scikit_learn", "scikit-learn"),
        ("scikit.learn", "scikit-learn"),
        ("scikit-learn", "scikit_learn"),
        ("python_dotenv", "python-dotenv"),
    ],
)
def test_distribution_names_compare_the_pep_503_way(left, right):
    assert normalize_distribution_name(left) == normalize_distribution_name(right)


def test_installed_matching_is_a_name_match_not_a_substring():
    # ``flask`` must not be "installed" because ``flask-login`` is.
    installed = {normalize_distribution_name("flask-login")}
    assert not is_installed("flask", installed)
    assert is_installed("flask-login", installed)


def test_an_unknown_list_is_not_the_same_as_an_empty_one():
    assert not is_installed("flask", None)
    assert not is_installed("flask", frozenset())


# ---------------------------------------------------------------- the bite ---
def test_the_bite_installing_then_rerendering_shows_no_offer():
    """His exact report: install the package, run again, the button is still there.

    The same output is rendered twice: once with the package absent (the offer is
    there) and once with it installed (the offer is gone). Against the old code the
    second assertion fails — nothing ever consulted the list.
    """
    output = FAIL + "\n"
    before, before_rows = _offer(output)
    assert before and before_rows, "the failure produced no offer at all (vacuous)"

    installed = frozenset({normalize_distribution_name("flask")})
    after, after_rows = _offer(output, installed=installed)
    assert after == ""
    assert after_rows == []


def test_installed_none_still_offers_so_the_degraded_path_is_not_a_no_op():
    html, rows = _offer(FAIL, installed=None)
    assert html and rows, "installed=None silently disabled the offer"
    assert any("flask" in b.callback_data for r in rows for b in r)


# ----------------------------------------------------------------- tail guard
def test_a_tail_that_mentions_flask_but_ends_in_success_offers_nothing():
    output = (
        "$ python3 app.py\n"
        + FAIL + "\n"
        "$ python3 app.py\n"
        "served on 8080\n"
        "$ "
    )
    html, rows = _offer(output)
    assert html == "" and rows == []


def test_a_failure_that_scrolled_off_the_displayed_tail_is_not_offered():
    """Only the last 12 KiB (the displayed tail) may produce an offer."""
    from cloudbot.presentation.screens.packages import OFFER_TAIL_BYTES

    old_failure = FAIL + "\n"
    filler = ("noise line\n" * 4000)[: OFFER_TAIL_BYTES + 1000]
    output = old_failure + filler + "$ echo done\ndone\n$ "
    assert FAIL not in last_failure(output)
    html, rows = _offer(output)
    assert html == "" and rows == []


def test_an_empty_or_idle_terminal_shows_no_offer():
    for output in ("", "   \n \n", "$ ", "$ ls\nmain.py\n$ "):
        html, rows = _offer(output)
        assert html == "" and rows == [], output


def test_a_current_failure_tail_still_offers():
    html, rows = _offer("$ python3 app.py\n" + FAIL + "\n")
    assert html and rows
    assert any("flask" in b.callback_data for r in rows for b in r)


# ---------------------------------------------------- installed per module ---
def test_one_installed_module_is_skipped_while_a_different_missing_one_stays():
    output = (
        "$ python3 app.py\n"
        "ModuleNotFoundError: No module named 'flask'\n"
        "ModuleNotFoundError: No module named 'PIL'\n"
    )
    installed = frozenset({normalize_distribution_name("pillow")})
    html, rows = _offer(output, installed=installed)
    assert "flask" in html
    assert "pillow" not in html
    callbacks = [b.callback_data for r in rows for b in r]
    assert any("flask" in c for c in callbacks)
    assert not any("pillow" in c for c in callbacks)


def test_the_pip_name_a_callback_carries_is_what_is_checked():
    """The installed check compares pip names, so PIL→pillow is found when the
    store reports any spelling of pillow."""
    output = "$ python3 app.py\nModuleNotFoundError: No module named 'PIL'\n"
    for spelling in ("Pillow", "pillow", "PILLOW"):
        html, rows = _offer(
            output, installed=frozenset({normalize_distribution_name(spelling)})
        )
        assert html == "" and rows == [], spelling


# --------------------------------------------------------------- keyboard ----
def test_each_install_callback_appears_at_most_once_in_the_keyboard():
    output = (
        "$ python3 app.py\n"
        "ModuleNotFoundError: No module named 'flask'\n"
        "ModuleNotFoundError: No module named 'PIL'\n"
        "ModuleNotFoundError: No module named 'PIL'\n"
    )
    screen = exec_result(
        ExecResult(1, "", output, 120, False),
        WS,
        Locale.EN,
        missing_offer=_offer(output),
    )
    callbacks = [
        b.callback_data
        for row in screen.markup.inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("pkg:fix:")
    ]
    assert len(callbacks) == len(set(callbacks)), callbacks


# ------------------------------------------------------- dispatcher, real ---
class _Bot:
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

        return io.BytesIO(b"d")

    async def __call__(self, method):
        return True

    def actions(self, message_id: int) -> list[str]:
        markup = self.messages[message_id].get("markup")
        rows = getattr(markup, "inline_keyboard", None) or []
        return [b.callback_data or "" for row in rows for b in row]


def _callback(uid: int, data: str, *, message_id: int | None = None) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=TG, is_bot=False, first_name="A"),
            chat_instance="c",
            message=Message(
                message_id=message_id if message_id is not None else uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=TG, type="private"),
                from_user=User(id=TG, is_bot=False, first_name="A"),
                text="screen",
            ),
            data=data,
        ),
    )


async def _harness():
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
    assert isinstance(engine, FakeEngine)
    await container.users.get_or_create(TG, default_locale=Locale.EN)
    await container.users.add_workspace(TG, WS)
    # A real install needs egress; the seeded workspace has none by default.
    await engine.patch_workspace(WS, limits=Limits(allow_net=True))
    await container.services.session.enter(TG, WS)
    exec_handlers._ORIGINS.clear()
    exec_handlers._LAST_EXEC_RESULT.clear()
    exec_handlers._LAST_EXEC.clear()
    bot = _Bot()
    dispatcher = build_dispatcher(container, bot)
    return container, engine, bot, dispatcher


async def test_end_to_end_the_installed_package_is_never_offered_again():
    """Drive the real dispatcher: install the offered package, then render the
    terminal again and prove the offer is gone. This is the reported defect."""
    container, engine, bot, dispatcher = await _harness()
    try:
        # Put the failure into the fake PTY's scrollback.
        session = await container.services.sessions.open(TG, WS)
        engine._session_output[session.id] = (
            "$ python3 app.py\n" + FAIL + "\n$ "
        )
        await dispatcher.feed_update(bot, _callback(1, f"term:view:{WS}:{session.id}"))
        first = max(bot.messages)
        actions = bot.actions(first)
        assert any(a.startswith("pkg:fix:") for a in actions), (
            "the failure produced no offer (the test would be vacuous)"
        )
        # The offer's buttons are attached once each, never duplicated.
        assert len(actions) == len(set(actions)), actions
        offer = next(a for a in bot.actions(first) if a.startswith("pkg:fix:"))
        # Install the offered package exactly as a tap does.
        await dispatcher.feed_update(bot, _callback(2, offer, message_id=first))
        installed = {p.name.casefold() for p in (await container.services.packages.list(TG, WS)).packages}
        assert "flask" in installed, "the fake install did not record the package"

        # Re-render the same session with the same scrollback: no offer.
        await dispatcher.feed_update(bot, _callback(3, f"term:view:{WS}:{session.id}"))
        latest = max(bot.messages)
        assert not any(a.startswith("pkg:fix:") for a in bot.actions(latest)), (
            f"the offer survived the install: {bot.actions(latest)}"
        )
    finally:
        await container.aclose()


def test_two_unknown_modules_get_one_type_it_yourself_control():
    """The unknown module has no button that names it; the "type the package
    name" control opens the same prompt for any of them, so it is attached once."""
    output = (
        "$ python3 app.py\n"
        "ModuleNotFoundError: No module named 'weird_a'\n"
        "ModuleNotFoundError: No module named 'weird_b'\n"
    )
    _html, rows = _offer(output)
    callbacks = [b.callback_data for r in rows for b in r]
    assert len(callbacks) == len(set(callbacks)), callbacks
    assert sum(1 for c in callbacks if c.startswith("pkg:install")) == 1


# ------------------------------------------------- the failed install reason --
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_a_failed_install_says_why_with_the_exit_code_and_output_tail(locale):
    """A failed install must not be silence: the exit code and the engine's own
    output tail are on the screen, and the command output the user came from is
    still reachable through the origin back button."""
    from cloudbot.domain.value_objects import InstallResult
    from cloudbot.presentation.screens.packages import install_result_screen
    from cloudbot.services.packages import InstallOutcome

    outcome = InstallOutcome(
        result=InstallResult(
            exit_code=2,
            output="ERROR: Could not find a version that satisfies the requirement flask==99\n",
            stdout="",
            duration_ms=1200,
            timed_out=False,
            truncated=False,
            target="/opt/python/private",
        ),
        allow_net=False,
    )
    screen = install_result_screen(outcome, "flask", WS, locale)
    html = screen.doc.html
    # The exit code and the engine's own reason are both shown.
    assert "2" in html
    assert "No matching" in html or "Could not find a version" in html
    # No install button is re-offered on the result screen.
    callbacks = [
        b.callback_data for row in screen.markup.inline_keyboard for b in row
    ]
    assert not any((c or "").startswith("pkg:fix:") for c in callbacks)
    # And the way back to the screen the user was reading is still present.
    assert "origin:back" in callbacks


# -------------------------------------------------------- MOHAMED.md UI rules --
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("typing", [False, True])
def test_the_destructive_close_button_sits_alone_on_its_row(locale, typing):
    """A destructive action gets its own row, never beside the button a hand taps
    by habit (MOHAMED.md). Close used to share a row with Ctrl-D / the list."""
    from cloudbot.domain.entities import TerminalSession
    from cloudbot.presentation.screens import terminal as term_screens

    session = TerminalSession(
        id="ses_01M6HJATZTFXTWXWBG8A5KWYS8",
        workspace_id=WS,
        status=__import__(
            "cloudbot.domain.value_objects", fromlist=["SessionStatus"]
        ).SessionStatus.RUNNING,
    )
    screen = term_screens.terminal_view(session, WS, locale, typing=typing)
    close_row = [
        row
        for row in screen.markup.inline_keyboard
        if any((b.callback_data or "").startswith("term:close") for b in row)
    ]
    assert len(close_row) == 1, "Close is missing"
    assert len(close_row[0]) == 1, "Close shares a row with another button"


def test_a_successful_run_result_never_offers_a_package():
    """exit 0 => no offer, even if a caller hands one in: success is the strongest
    "nothing is missing" signal, and a stale line must not resurrect a button."""
    result = ExecResult(0, "all good\n", "", 120, False)
    # Deliberately pass an offer built from a real missing traceback.
    screen = exec_result(
        result,
        WS,
        Locale.EN,
        missing_offer=_offer("$ python3 app.py\n" + FAIL),
    )
    callbacks = [
        b.callback_data for row in screen.markup.inline_keyboard for b in row
    ]
    assert not any((c or "").startswith("pkg:fix:") for c in callbacks)
    assert "Missing library" not in screen.doc.html


def test_a_successful_file_run_logs_never_offer_a_package():
    """A file run that exited 0 shows no offer, even with stale stderr text."""
    from cloudbot.domain.entities import Run, RunStatus
    from cloudbot.domain.value_objects import RunId
    from cloudbot.presentation.screens.run import live_logs

    run = Run(
        id=RunId("run_01TEST000000000000000000"),
        workspace_id=WS,
        kind="file",
        argv=["app.py"],
        cwd="/app",
        status=RunStatus.EXITED,
        exit_code=0,
        interpreter="python3",
        timeout_sec=60,
        started_at=None,
        ended_at=None,
        stderr_bytes=0,
    )
    screen = live_logs(run, Locale.EN, stdout="served\n", stderr=FAIL)
    assert "Missing library" not in screen.doc.html
    callbacks = [
        b.callback_data for row in screen.markup.inline_keyboard for b in row
    ]
    assert not any((c or "").startswith("pkg:fix:") for c in callbacks)
