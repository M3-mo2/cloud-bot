"""TASK 24 — the in-bot developer tools (``/dev``).

These tests drive ``/dev`` through the **real dispatcher** (not a mocked handler)
so they exercise the gate, the catalog, the walk and the sandbox exactly as an
admin would. The totals are derived from the catalog, never hardcoded, so they
cannot rot when a screen is added.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.handlers.dev_sandbox import DevSandbox, FakeBot
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.screens.screen import Screen
from cloudbot.presentation.rich.document import RichDoc

TG = 777
LOCALES = (Locale.EN, Locale.AR)


def _settings(**over) -> Settings:
    base = dict(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids=frozenset({TG}),
        dev_tools=True,
        throttle_rate=10**9,
        _env_file=None,
    )
    base.update(over)
    return Settings(**base)


class RecordingBot(FakeBot):
    """A FakeBot whose ``__call__`` answers ``callback.answer()`` (inherited)."""


def _msg(uid: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=uid,
        message=Message(
            message_id=uid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="U"),
            text=text,
        ),
    )


def _cb(uid: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="U"),
            chat_instance="c",
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="U"),
                text="screen",
            ),
            data=data,
        ),
    )


async def _tool(**over):
    settings = _settings(**over)
    container = build(settings)
    await container.database.migrate()
    bot = RecordingBot()
    # The handler routers are process-global; detach them so a second dispatcher
    # in the same test can attach them (production builds the dispatcher once).
    from cloudbot.handlers.dev_sandbox import _detach_routers

    _detach_routers()
    dispatcher = build_dispatcher(container, bot)
    return settings, container, bot, dispatcher


def _last(bot: FakeBot) -> str:
    return bot.sent[-1]["html"]


def _table_value(html: str, label: str) -> str:
    m = re.search(rf"<th>{re.escape(label)}</th><td>([^<]*)</td>", html)
    assert m is not None, f"row {label!r} not found in report"
    return m.group(1)


def _button_count(screen: Screen) -> int:
    return len(screen_catalog.button_infos(screen))


# -- the flow (§5 first bullet) ------------------------------------------------
async def test_dev_opens_sections_lists_screens_and_inspects():
    settings, container, bot, dispatcher = await _tool()
    try:
        # 1. /dev → the sections screen, one row/button per section.
        await dispatcher.feed_update(bot, _msg(1, TG, "/dev"))
        html = _last(bot)
        assert catalog.t("dev.sections.title", Locale.EN) in html
        for section in screen_catalog.sections():
            assert catalog.t(section.title_key, Locale.EN) in html

        # 2. a section lists every one of its screens.
        await dispatcher.feed_update(bot, _cb(2, TG, "dev:section:terminal"))
        html = _last(bot)
        for spec in screen_catalog.screens_in("terminal"):
            assert spec.name in html, f"{spec.name} missing from the section list"

        # 3. one screen, inspected: a verdict for **every** button.
        name = "terminal_view_running"
        await dispatcher.feed_update(bot, _cb(3, TG, f"dev:inspect:{name}:en"))
        html = _last(bot)
        expected = _button_count(screen_catalog.build(name, Locale.EN))
        markup = bot.sent[-1]["markup"]
        taps = [
            b
            for row in markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(f"dev:tap:{name}:")
        ]
        assert len(taps) == expected, (
            f"the inspect screen offers {len(taps)} taps but the screen has {expected} buttons"
        )
        # Every verdict label is present, and the real screen body is rendered.
        assert catalog.t("dev.verdict.handled", Locale.EN) in html
        assert "terminal" in html.lower()
    finally:
        await container.aclose()


async def test_run_all_checks_totals_equal_the_catalog():
    """The report's totals must equal an independent count from the catalog."""
    settings, container, bot, dispatcher = await _tool()
    try:
        section = "terminal"
        specs = screen_catalog.screens_in(section)
        # Independent count: build each screen in both locales and count buttons.
        expected_screens = len(specs) * len(LOCALES)
        expected_buttons = sum(
            _button_count(screen_catalog.build(spec.name, loc))
            for spec in specs
            for loc in LOCALES
        )

        await dispatcher.feed_update(bot, _cb(1, TG, f"dev:run:section:{section}"))
        html = _last(bot)
        assert _table_value(html, catalog.t("dev.col.screens", Locale.EN)) == str(
            expected_screens
        )
        assert _table_value(html, catalog.t("dev.col.buttons", Locale.EN)) == str(
            expected_buttons
        )

        # And the whole catalog: an independent count over every screen × locale.
        cat_screens = len(screen_catalog.SCREEN_BUILDERS) * len(LOCALES)
        cat_buttons = sum(
            _button_count(screen_catalog.build(name, loc))
            for name in screen_catalog.SCREEN_BUILDERS
            for loc in LOCALES
        )
        await dispatcher.feed_update(bot, _cb(2, TG, "dev:run:catalog"))
        html = _last(bot)
        assert _table_value(html, catalog.t("dev.col.screens", Locale.EN)) == str(cat_screens)
        assert _table_value(html, catalog.t("dev.col.buttons", Locale.EN)) == str(cat_buttons)
    finally:
        await container.aclose()


# -- a deliberately dead button (§5 second bullet) -----------------------------
async def test_a_dead_button_is_reported_no_handler_and_does_not_crash():
    settings, container, bot, dispatcher = await _tool()
    probe = "dev_probe_dead"

    def _builder(_f, loc):
        doc = RichDoc(loc)
        doc.p("probe")
        return Screen(doc=doc, markup=inline_markup([[inline("Dead", "zzz:dead")]]))

    screen_catalog.register(probe, "terminal", _builder)
    try:
        await dispatcher.feed_update(bot, _cb(1, TG, f"dev:inspect:{probe}:en"))
        html = _last(bot)
        assert catalog.t("dev.verdict.no_handler", Locale.EN) in html
        # The tool did not crash: it rendered the inspect screen with the table.
        assert catalog.t("dev.button.table_title", Locale.EN) in html

        # And a run-all-checks over the section reports it in the findings.
        await dispatcher.feed_update(bot, _cb(2, TG, "dev:run:section:terminal"))
        html = _last(bot)
        assert probe in html
        assert catalog.t("dev.verdict.no_handler", Locale.EN) in html
    finally:
        screen_catalog.unregister(probe)
        await container.aclose()


# -- the restart case (§5 third bullet) ----------------------------------------
async def test_a_token_callback_is_reported_stale_after_a_registry_reset():
    from cloudbot.presentation.keyboards.callbacks import codec

    settings, container, bot, dispatcher = await _tool()
    probe = "dev_probe_stale"
    # A callback so long it falls back to the in-memory token registry.
    token_data = codec.encode("probe:deep", "x" * 120)
    assert "~" in token_data, "the probe callback was not tokenised"

    def _builder(_f, loc):
        doc = RichDoc(loc)
        doc.p("probe")
        return Screen(doc=doc, markup=inline_markup([[inline("Deep", token_data)]]))

    screen_catalog.register(probe, "terminal", _builder)
    try:
        # The restart: the token registry that resolved the token dies.
        codec.reset()
        await dispatcher.feed_update(bot, _cb(1, TG, f"dev:inspect:{probe}:en"))
        html = _last(bot)
        assert catalog.t("dev.verdict.stale", Locale.EN) in html
        assert catalog.t("dev.verdict.handled", Locale.EN) not in html
    finally:
        screen_catalog.unregister(probe)
        await container.aclose()


# -- no engine, no Telegram (§5 fourth bullet) ---------------------------------
async def test_a_full_walk_and_tap_reach_no_http_and_no_real_bot(monkeypatch):
    """The sandbox is isolated: a FakeEngine and a FakeBot, always.

    The engine's HTTP client and aiogram's real send methods are patched to fail,
    so any leak fails the test. ``DevSandbox.start`` must work even when the
    settings point at a real engine URL and ``mock_engine`` is off.
    """
    import cloudbot.adapters.cloud_service.engine as engine_mod
    import aiogram

    def _boom(*_a, **_k):
        raise AssertionError("the developer tools reached the HTTP engine")

    monkeypatch.setattr(engine_mod.EngineAdapter, "create", classmethod(_boom))
    monkeypatch.setattr(aiogram.Bot, "send_rich_message", _boom)
    monkeypatch.setattr(aiogram.Bot, "edit_message_text", _boom)

    # Settings that *would* build a real EngineAdapter — the sandbox must ignore it.
    settings = _settings(mock_engine=False, engine_base_url="http://127.0.0.1:9")
    sandbox = await DevSandbox.start(settings, TG, Locale.EN)
    try:
        from cloudbot.adapters.cloud_service.fake import FakeEngine

        assert isinstance(sandbox.container.engine, FakeEngine)
        assert isinstance(sandbox.bot, FakeBot)

        async def claims(data: str) -> bool:
            return await sandbox.claims(data)

        report = await screen_catalog.walk_catalog(LOCALES, claims=claims)
        assert report.buttons > 0

        # A tap renders into the sandbox's own fake bot, calling nothing real.
        rendered, html, error = await sandbox.tap(
            screen_catalog.button_infos(
                screen_catalog.build("ws_detail", Locale.EN)
            )[0].data
        )
        assert error is None
        assert rendered is True and html
        assert sandbox.bot.sent, "the tap did not render into the sandbox bot"
    finally:
        await sandbox.aclose()


# -- architecture: the tool is behind the admin gate (§5 fifth bullet) ---------
async def test_dev_router_is_only_attached_when_the_switch_is_on():
    from cloudbot.handlers import dev

    _settings_off, c_off, _b_off, d_off = await _tool(dev_tools=False)
    _settings_on, c_on, _b_on, d_on = await _tool(dev_tools=True)
    try:
        def _has_dev(dispatcher) -> bool:
            return any(r.name == "dev" for r in _all_routers(dispatcher))

        assert not _has_dev(d_off), "the dev router exists with the switch off"
        assert _has_dev(d_on), "the dev router is missing with the switch on"
    finally:
        await c_off.aclose()
        await c_on.aclose()


def _all_routers(router):
    yield router
    for sub in getattr(router, "sub_routers", []):
        yield from _all_routers(sub)


async def test_non_admin_is_swallowed_by_the_gate_even_with_the_switch_on():
    settings = _settings(admin_ids=frozenset({TG}))
    container = build(settings)
    await container.database.migrate()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _msg(1, 999, "/dev"))
        assert bot.sent == [], "a non-admin reached the developer tools"
        await dispatcher.feed_update(bot, _cb(2, 999, "dev:sections"))
        assert bot.sent == [], "a non-admin's dev: callback was handled"
    finally:
        await container.aclose()


def test_the_gate_recognises_the_dev_surface():
    from cloudbot.middleware.admin_gate import (
        DEV_COMMANDS,
        admin_command,
        _wants_admin,
        _wants_dev,
    )

    def _cbq(data: str) -> CallbackQuery:
        return CallbackQuery(
            id="1",
            from_user=User(id=1, is_bot=False, first_name="U"),
            chat_instance="c",
            data=data,
        )

    assert "/dev" in DEV_COMMANDS
    assert admin_command("/dev") == "/dev"
    assert admin_command("/dev@the_bot") == "/dev"
    # A dev callback is claimed by the gate (so the switch check can refuse it).
    assert _wants_admin(_cbq("dev:sections"))
    assert _wants_dev(_cbq("dev:sections"))
    assert not _wants_admin(_cbq("ws:list"))


def test_the_product_does_not_import_tests():
    """The catalog is product code; it must not depend on the test suite."""
    import ast
    import pathlib

    root = pathlib.Path(screen_catalog.__file__).resolve().parents[2] / "cloudbot"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "tests" or a.name.startswith("tests.") for a in node.names):
                    offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "tests" or node.module.startswith("tests."):
                    offenders.append(str(path))
    assert not offenders, f"the product imports the test suite: {offenders}"


# -- the catalog walk itself (§5: totals from the catalog, not hardcoded) -------
async def test_the_catalog_walk_counts_every_screen_and_flags_a_dead_button():
    """The walk (shared with the tool) totals the catalog independently.

    The positive control proves the walk is not vacuous; the injected dead button
    proves it detects a real failure and reports it without crashing.
    """
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.markup import inline_markup
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.screens.screen import Screen

    probe = "dev_probe_report_dead"

    def _builder(_f, loc):
        doc = RichDoc(loc)
        doc.p("probe")
        return Screen(doc=doc, markup=inline_markup([[inline("Dead", "aaa:bbb")]]))

    sandbox = await DevSandbox.start(_settings(), TG, Locale.EN)
    try:
        async def claims(data: str) -> bool:
            return await sandbox.claims(data)

        clean = await screen_catalog.walk_catalog(LOCALES, claims=claims)
        assert clean.buttons > 0, "the walk is vacuous"
        assert clean.totals["no_handler"] == 0, f"the shipped catalog has dead buttons: {clean.failures}"

        screen_catalog.register(probe, "terminal", _builder)
        try:
            dirty = await screen_catalog.walk_catalog(LOCALES, claims=claims)
        finally:
            screen_catalog.unregister(probe)
        assert dirty.buttons == clean.buttons + len(LOCALES)
        assert dirty.totals["no_handler"] == len(LOCALES)
        assert all(f[1] == "no_handler" for f in dirty.failures)
        assert probe in {f[0] for f in dirty.failures}
    finally:
        await sandbox.aclose()
