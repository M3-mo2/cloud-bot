"""Structural contract: every callback any screen emits must be routable.

Task 07 made the ``adm:`` panel safe by construction: a screen producer and a
handler consumer drifted, and the button raised ``IndexError`` on first use. The
same family produced the Task 22 defects on terminal buttons:

* a terminal list button raised ``IndexError`` when the process restarted between
  drawing it and tapping it (the content-addressed token registry is in-memory);
* ``term:resize`` (the **↔️ Resize** button on the terminal view) had **no handler
  at all** and was silently dead.

Both are producer/consumer mismatches, and both survived every gate because no test
walked the screens' buttons. This test does: it renders **every** screen in
``SCREEN_BUILDERS`` in both locales and asserts, against the **real dispatcher's**
own routing, that each ``callback_data`` it carries is claimed by a handler. A
button no handler claims is a future silent death — and this test makes it loud.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.screens import terminal_callbacks as term_cb
from cloudbot.presentation.screens.terminal_callbacks import TermCallback

LOCALES = (Locale.EN, Locale.AR)


def _settings() -> Settings:
    return Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=777, is_bot=False, first_name="A"),
        chat_instance="c",
        message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=777, type="private"),
            from_user=User(id=777, is_bot=False, first_name="A"),
            text="screen",
        ),
        data=data,
    )


def _callback_handlers(router) -> list:
    """Every callback-query handler in the real router tree, recursively."""
    handlers: list = []
    observer = router.observers.get("callback_query")
    if observer is not None:
        handlers.extend(observer.handlers)
    for sub in getattr(router, "sub_routers", []):
        handlers.extend(_callback_handlers(sub))
    return handlers


async def _is_routable(handlers, data: str) -> bool:
    """True when some handler's own filters accept ``data`` (the real routing)."""
    event = _callback(data)
    for handler in handlers:
        try:
            passed, _ = await handler.check(event)
        except Exception:  # noqa: BLE001 - a filter that raises is a non-match
            continue
        if passed:
            return True
    return False


def _buttons(screen) -> list:
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [b for row in rows for b in row]


def _screen_builders():
    from cloudbot.presentation.catalog import SCREEN_BUILDERS

    return SCREEN_BUILDERS


@pytest.fixture
async def handlers():
    container = build(_settings())
    await container.database.migrate()
    try:
        # ``object()`` is enough: this walk only runs handlers' filters, never the
        # bot. (The dispatcher is never started.)
        dispatcher = build_dispatcher(container, object())
        yield _callback_handlers(dispatcher)
    finally:
        await container.aclose()


async def test_the_contract_walk_finds_handlers_and_buttons(handlers):
    """A walk that finds no handlers or no buttons would pass vacuously."""
    from cloudbot.presentation.catalog import fixtures as catalog_fixtures

    assert handlers, "no callback handlers found — the contract test is vacuous"
    fixtures = catalog_fixtures()
    found = sum(
        len(_buttons(builder(fixtures, Locale.EN)))
        for builder in _screen_builders().values()
    )
    assert found > 0, "no buttons found — the contract test is vacuous"


@pytest.mark.parametrize("locale", LOCALES)
async def test_every_screen_button_is_routable(handlers, locale):
    from cloudbot.presentation.catalog import fixtures as catalog_fixtures

    fixtures = catalog_fixtures()
    checked = 0
    unroutable: list[tuple[str, str]] = []
    for name, build in sorted(_screen_builders().items()):
        CallbackCodec()  # deterministic tokens per render
        screen = build(fixtures, locale)
        for button in _buttons(screen):
            data = getattr(button, "callback_data", None)
            if not data:
                continue
            checked += 1
            if not await _is_routable(handlers, data):
                unroutable.append((name, data))
    assert checked > 0, "no callbacks were checked — the test is vacuous"
    assert not unroutable, (
        "these screens emit a callback no handler can route (a dead button): "
        + repr(unroutable)
    )


@pytest.mark.parametrize("locale", LOCALES)
async def test_every_terminal_button_parses_through_the_term_codec(handlers, locale):
    """A ``term:`` button must be a complete, parseable codec action.

    The codec's parser is total: a producer that omits a key part (or an unknown
    action) is rejected here rather than by an ``IndexError`` at tap time.
    """
    from cloudbot.presentation.catalog import fixtures as catalog_fixtures

    fixtures = catalog_fixtures()
    checked = 0
    for name, build in sorted(_screen_builders().items()):
        CallbackCodec()
        screen = build(fixtures, locale)
        for button in _buttons(screen):
            data = getattr(button, "callback_data", None)
            if not data or not data.startswith("term:"):
                continue
            checked += 1
            parsed = term_cb.parse(data)
            assert isinstance(parsed, TermCallback), (
                f"{name}/{locale.value} produced {data!r}, which the term: codec "
                f"rejects: {parsed}"
            )
    assert checked > 0, "no term: callbacks were checked — the test is vacuous"
