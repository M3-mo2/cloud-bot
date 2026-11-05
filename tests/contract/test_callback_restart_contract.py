"""Contract: **every** screen's every button survives a process restart.

THE root-cause test for TASK 23 (F-23.1). The old codec routed every key-bearing
callback through a **process-global, in-memory** token registry (CB-8), so after a
restart a button drawn by the previous process decoded to ``key=()`` and the
handler's ``decode(cb.data).key[0]`` raised ``IndexError`` — for *every* prefix
(``ws:``, ``file:``, ``term:``, ``run:``, …), not just the two that were patched.

This test drives the **real dispatcher** with the registry **reset** — exactly what
a restart does — and again with it **warm**, and asserts that:

* **no button raises** (no exception escapes the dispatcher), and
* **no button renders nothing** (every tap produces a message — a screen, a
  correctable "out of date" screen, an error screen; never silence).

It does not care which prefix or which handler is wrong, which is what makes the
whole class impossible: a new key-bearing callback that dies after a restart fails
here regardless.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceStatus
from cloudbot.presentation.keyboards.callbacks import codec

TG = 777
LOCALES = (Locale.EN, Locale.AR)

#: The fixture workspace ids the catalog renders (its ``fixtures`` seed and
#: ``FakeEngine``'s own seeds). The walk must own every one of them so a scoped
#: handler reaches its service instead of the isolation gate.
FIXTURE_WS = (
    "ws_01M6HJASCWM9Z1PF755GT89NK4",
    "ws_01M6HJASCWM9Z1PF755GT89NK5",
    "ws_01M6HJASCWM9Z1PF755GT89NK6",
)


#: The one bot double, shared with the dev tool and the other contract tests.
from cloudbot.handlers.dev_sandbox import FakeBot  # noqa: E402


def _update(uid: int, user_id: int, data: str) -> Update:
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


def _buttons(screen) -> list:
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [b for row in rows for b in row]


def _screen_builders() -> dict:
    from cloudbot.presentation.catalog import SCREEN_BUILDERS

    return SCREEN_BUILDERS


#: A button with no effect by design: it carries no key parts, so it cannot go
#: stale, and tapping it is *meant* to render nothing (a placeholder where an
#: action is unavailable). The walk's "never silent" rule has this one named
#: exception — everything else must produce a screen.
NO_RENDER_EXPECTED: frozenset[str] = frozenset({"nav:noop"})


async def _container():
    settings = Settings(
        # An admin id so the ``adm:`` panel buttons are routable too; the panel is
        # part of "every screen" and its callbacks must be exercised.
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids=frozenset({TG}),
        # The walk taps hundreds of buttons back-to-back; a realistic throttle
        # would refuse a burst (a correct production behaviour, but it renders
        # nothing by design and is not what this test measures).
        throttle_rate=10**9,
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    await container.users.get_or_create(TG, default_locale=Locale.EN)
    # Own every seeded/fixture workspace so no scoped handler stops at the gate.
    for ws_id in FIXTURE_WS:
        await container.users.add_workspace(TG, WorkspaceId(ws_id))
    # Make the fixture ids resolvable in the fake engine (the blocked ws does not
    # stop the walk, but a lifecycle button should still reach a screen).
    for ws_id, name, status in (
        ("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE),
        ("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED),
        ("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED),
    ):
        container.engine._seed_workspace(ws_id, name, status)
    await container.services.workspaces.create(TG, "mine")
    return container


async def _reset_state(container) -> None:
    """Restore the fake engine + ownership to their seeded state.

    The walk taps *destructive* buttons too (``ws:delete`` really deletes in the
    fake engine); without this, a later screen's lifecycle button would hit a
    missing workspace and raise ``KeyError`` inside the fake — a harness artifact,
    not a product defect.
    """
    for ws_id, name, status in (
        ("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE),
        ("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED),
        ("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED),
    ):
        container.engine._seed_workspace(ws_id, name, status)
    for ws_id in FIXTURE_WS:
        await container.users.add_workspace(TG, WorkspaceId(ws_id))
    await container.users.set_current_workspace(TG, None)


async def _walk(warm: bool) -> tuple[int, list[tuple[str, str]]]:
    """Feed every screen button through the real dispatcher.

    ``warm`` keeps the registry as drawn; ``warm=False`` resets it **after** each
    screen is rendered and **before** its buttons are tapped — the restart.
    Returns (buttons checked, (name, locale) for any that rendered nothing).
    """
    from cloudbot.presentation.catalog import fixtures

    container = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    fixtures_data = fixtures()
    checked = 0
    silent: list[tuple[str, str]] = []
    try:
        uid = 0
        for locale in LOCALES:
            for name, builder in sorted(_screen_builders().items()):
                await _reset_state(container)
                screen = builder(fixtures_data, locale)
                if not warm:
                    # A restart: the token registry that mapped a token back to its
                    # key parts died with the previous process.
                    codec.reset()
                for button in _buttons(screen):
                    data = getattr(button, "callback_data", None)
                    if not data:
                        continue
                    checked += 1
                    uid += 1
                    before = len(bot.sent)
                    await dispatcher.feed_update(bot, _update(uid, TG, data))
                    if data in NO_RENDER_EXPECTED:
                        continue
                    if len(bot.sent) == before:
                        silent.append((name, f"{locale.value}:{data}"))
    finally:
        await container.aclose()
    return checked, silent


async def test_every_screen_button_survives_a_warm_registry():
    """The positive control: with the registry as drawn, no button is silent."""
    checked, silent = await _walk(warm=True)
    assert checked > 0, "the walk checked no buttons — it is vacuous"
    assert not silent, f"{len(silent)} buttons rendered nothing (warm): {silent[:10]}"


async def test_every_screen_button_survives_a_restart():
    """The root-cause test: reset the registry (a restart), tap everything.

    No exception may escape the dispatcher, and no tap may render nothing. A
    key-bearing callback that still depends on the in-memory registry goes red
    here — whatever its prefix.
    """
    checked, silent = await _walk(warm=False)
    assert checked > 0, "the walk checked no buttons — it is vacuous"
    assert not silent, (
        f"after a restart {len(silent)} buttons rendered nothing "
        f"(the registry died with the process): {silent[:10]}"
    )


# -- the three prefixes the task names, pinned directly -----------------------
@pytest.mark.parametrize(
    "data",
    [
        "ws:view:{ws}",           # the terminal view's Back button — the live failure
        "file:ls:{ws}:0",         # a file listing button
        "term:list:{ws}",         # a terminal list button
    ],
    ids=["ws:", "file:", "term:"],
)
async def test_a_pre_restart_inline_callback_still_works(data):
    """A pre-restart ``ws:``, ``file:`` and ``term:`` callback still works after
    ``codec.reset()``.

    Each fits the 64-byte cap, so it is inline and the reset cannot affect it; the
    test proves the whole point of TASK 23 for the three prefixes named in the spec.
    """
    container = await _container()
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = data.format(ws=FIXTURE_WS[0])
        codec.reset()  # the restart
        before = len(bot.sent)
        await dispatcher.feed_update(bot, _update(1, TG, payload))
        assert len(bot.sent) > before, f"{payload!r} rendered nothing after a restart"
    finally:
        await container.aclose()
