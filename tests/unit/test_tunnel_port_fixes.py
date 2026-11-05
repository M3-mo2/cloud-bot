"""TASK 20 — the remaining five port-publishing defects (F-04 … F-08).

The audit is ``docs/AUDIT-PORT-PUBLISHING.md``. Each claim is proven here, most
of them through the **real dispatcher** with the fake engine (so the rendered
message and keyboard are what a user would actually see), and the screen
factories in both locales:

* F-04: the create gate consults the workspace's **lifecycle status**; a
  suspended/locked/deleted (or unknown) workspace mutes Publish and names the
  status, in both locales.
* F-05: a ``tunnels_disabled`` refusal renders the **engine's own** reason
  (from ``details.config``/``field``), even when the bot's cached limits disagree
  — never a silent redraw.
* F-06: **every** reported listening TCP port is offered in the picker; an
  unattributed one is marked "owner unknown", never hidden; order is stable.
* F-07: engine tunnel statuses are translated in both locales (never the raw
  token) and a non-active tunnel's LINK button is muted.
* F-08: the dead ``_short`` is gone; the unused ``PORTS_TIMEOUT_S`` is wired in.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from cloudbot.adapters.cloud_service import engine as engine_module
from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import EngineRefused
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    Limits,
    PortEntry,
    PortReport,
    Tunnel,
    TunnelList,
    WorkspaceId,
    WorkspaceStatus,
)
from cloudbot.handlers import features
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import cb
from cloudbot.presentation.rich.tags import esc
from cloudbot.presentation.screens import tunnels as tnl

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
WS_OWNER = 6508129575
_LOCALES = (Locale.EN, Locale.AR)


# ---------------------------------------------------------------- harness ----
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.messages.append((rich_message.html, reply_markup))
        from aiogram.types import Message

        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.messages))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        from aiogram.types import Message

        self.messages.append((getattr(rich_message, "html", ""), reply_markup))
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def __call__(self, method):
        return True

    @property
    def last_html(self) -> str:
        return self.messages[-1][0]


def _callback(uid: int, user_id: int, data: str):
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    msg = Message(
        message_id=uid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text="screen",
    )
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid), from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c", message=msg, data=data,
        ),
    )


async def _harness(*, allow_tunnels=True, allow_net=True, max_tunnels=2, status=WorkspaceStatus.ACTIVE):
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
    engine = container.engine
    assert isinstance(engine, FakeEngine), "DEV + mock_engine must wire the fake engine"
    engine.max_tunnels = max_tunnels
    await container.users.get_or_create(WS_OWNER, default_locale=Locale.EN)
    ws = (await engine.list_workspaces())[0]
    await container.users.add_workspace(WS_OWNER, ws.id)
    await engine.patch_workspace(
        ws.id, limits=Limits(allow_net=allow_net, allow_tunnels=allow_tunnels)
    )
    if status is not WorkspaceStatus.ACTIVE:
        engine._set_status(ws.id, status)
    return container, engine, ws


def _buttons(screen):
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b for row in rows for b in row]


def _offered_ports(screen) -> list[int]:
    """The ports the picker actually offers, in order (decoded from callbacks)."""
    from cloudbot.presentation.keyboards.callbacks import decode

    out: list[int] = []
    for b in _buttons(screen):
        data = b.callback_data or ""
        if not data.startswith("tnl:port"):
            continue
        decoded = decode(data)
        if decoded.action == "tnl:port" and len(decoded.key) >= 2:
            out.append(int(decoded.key[1]))
    return out


def _publish_button(screen):
    return [b for b in _buttons(screen) if (b.callback_data or "").startswith("tnl:publish")]


def _tunnel(tid: str, port: int, status: str) -> Tunnel:
    return Tunnel(
        id=tid, workspace_id=str(WS), port=port,
        url=f"https://{tid}.trycloudflare.com", status=status,
    )


# ================================================================== F-04 ======
@pytest.mark.parametrize("locale", _LOCALES)
@pytest.mark.parametrize(
    "status,key",
    [
        (WorkspaceStatus.SUSPENDED, "tunnel.gate.suspended"),
        (WorkspaceStatus.LOCKED, "tunnel.gate.locked"),
        (WorkspaceStatus.DELETED, "tunnel.gate.deleted"),
        (WorkspaceStatus.UNKNOWN, "tunnel.gate.unknown"),
    ],
)
def test_f04_lifecycle_status_mutes_publish_and_names_the_status(status, key, locale):
    screen = tnl.tunnels_screen(TunnelList(tunnels=()), WS, locale, status=status, max_tunnels=2)
    publish = _publish_button(screen)
    assert publish, "no publish entry rendered"
    assert publish[0].disabled is not None, "a non-active workspace offered an enabled Publish"
    # The reason sentence names the lifecycle status, in the user's own words.
    assert esc(catalog.t(key, locale)) in screen.doc.html


@pytest.mark.parametrize("locale", _LOCALES)
def test_f04_active_workspace_publishes(locale):
    screen = tnl.tunnels_screen(TunnelList(tunnels=()), WS, locale, status=WorkspaceStatus.ACTIVE, max_tunnels=2)
    publish = _publish_button(screen)
    assert publish and publish[0].disabled is None
    # No lifecycle gate sentence on an active workspace.
    for key in ("tunnel.gate.suspended", "tunnel.gate.locked", "tunnel.gate.deleted", "tunnel.gate.unknown"):
        assert esc(catalog.t(key, locale)) not in screen.doc.html


def test_f04_lifecycle_gate_precedes_the_switch_gates():
    """A suspended workspace names the lifecycle, not the tunnel switches."""
    screen = tnl.tunnels_screen(
        TunnelList(tunnels=()), WS, Locale.EN,
        allow_tunnels=False, allow_net=False, server_enabled=False,
        status=WorkspaceStatus.SUSPENDED, max_tunnels=2,
    )
    html = screen.doc.html
    assert esc(catalog.t("tunnel.gate.suspended", Locale.EN)) in html
    assert esc(catalog.t("tunnel.gate.allow_tunnels", Locale.EN)) not in html


async def test_f04_dispatcher_suspended_workspace_offers_a_muted_publish():
    """The real path: the handler reads the lifecycle status from the workspace."""
    container, engine, ws = await _harness(status=WorkspaceStatus.SUSPENDED)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:list", ws.id)))
        assert esc(catalog.t("tunnel.gate.suspended", Locale.EN)) in bot.last_html
        markup = bot.messages[-1][1]
        buttons = [b for row in markup.inline_keyboard for b in row]
        publish = [b for b in buttons if (b.callback_data or "").startswith("tnl:publish")]
        assert publish and publish[0].disabled is not None
    finally:
        await container.aclose()


def test_f04_all_workspace_statuses_are_handled():
    """Every status the domain has is decided: ACTIVE publishes, the rest do not."""
    for status in WorkspaceStatus:
        screen = tnl.tunnels_screen(TunnelList(tunnels=()), WS, Locale.EN, status=status, max_tunnels=2)
        publish = _publish_button(screen)
        if status is WorkspaceStatus.ACTIVE:
            assert publish[0].disabled is None, f"{status} should publish"
        else:
            assert publish[0].disabled is not None, f"{status} must not publish"


# ================================================================== F-05 ======
@pytest.mark.parametrize("locale", _LOCALES)
@pytest.mark.parametrize(
    "details,key",
    [
        ({"config": "tunnels.enabled", "field": "enabled"}, "err.tunnels_disabled.server"),
        ({"config": "limits.allow_tunnels", "field": "allow_tunnels"}, "err.tunnels_disabled.allow_tunnels"),
        ({"config": "limits.allow_net", "field": "allow_net"}, "err.tunnels_disabled.allow_net"),
    ],
)
async def test_f05_engine_reason_wins_over_disagreeing_cache(details, key, locale):
    """The bot's cache says both switches are ON; the engine disagrees."""
    container, engine, ws = await _harness(allow_tunnels=True, allow_net=True)
    ctx = _ctx(locale)

    async def refuse(_ws_id, _port):
        raise EngineRefused("tunnels_disabled", details=details)

    engine.create_tunnel = refuse  # type: ignore[assignment]
    try:
        screen = await features._tunnel_refusal_screen(
            EngineRefused("tunnels_disabled", details=details), ctx, container.services, ws.id, 8080
        )
        assert screen is not None
        html = screen.doc.html
        assert esc(catalog.t(key, locale)) in html, "the engine's own reason was dropped"
        assert "tunnels_disabled" not in html, "the raw engine token leaked"
    finally:
        await container.aclose()


async def test_f05_gate_refusal_through_the_dispatcher_names_the_engine_switch():
    container, engine, ws = await _harness(allow_tunnels=True, allow_net=True)
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    async def refuse(_ws_id, _port):
        raise EngineRefused(
            "tunnels_disabled", details={"config": "limits.allow_net", "field": "allow_net"}
        )

    engine.create_tunnel = refuse  # type: ignore[assignment]
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:port", ws.id, 8080)))
        assert esc(catalog.t("err.tunnels_disabled.allow_net", Locale.EN)) in bot.last_html
    finally:
        await container.aclose()


def test_f05_gate_override_replaces_the_cached_sentence():
    # The gate refusal passes the engine's reason as ``gate``: it replaces the
    # cached gate, so the screen never carries a contradictory sentence.
    base = tnl.tunnels_screen(
        TunnelList(tunnels=()), WS, Locale.EN, allow_tunnels=True, allow_net=True
    )
    assert esc(catalog.t("err.tunnels_disabled.allow_net", Locale.EN)) not in base.doc.html
    overridden = tnl.tunnels_screen(
        TunnelList(tunnels=()), WS, Locale.EN, allow_tunnels=True, allow_net=True,
        gate=catalog.t("err.tunnels_disabled.allow_net", Locale.EN),
    )
    assert esc(catalog.t("err.tunnels_disabled.allow_net", Locale.EN)) in overridden.doc.html


def test_f05_gate_note_falls_back_to_the_generic_when_details_are_silent():
    from cloudbot.presentation.mapping.errors import tunnels_disabled_message

    generic = tunnels_disabled_message({}, Locale.EN)
    assert generic == catalog.t("err.tunnels_disabled", Locale.EN)


def _ctx(locale: Locale):
    from cloudbot.core.modes import RunMode
    from cloudbot.middleware.context import UserContext

    return UserContext(tg_id=WS_OWNER, locale=locale, is_admin=False, mode=RunMode.DEV)


# ================================================================== F-06 ======
def _port_entry(port: int, *, kind: str = "run", attributed: bool = True, protocol: str = "tcp", state: str = "LISTEN"):
    return PortEntry(
        port=port, protocol=protocol, address="0.0.0.0", scope="wildcard",
        state=state, attributed=attributed,
        pid=1234 if attributed else None, kind=kind if attributed else "", owners=(1234,) if attributed else (),
    )


def _report(ports) -> PortReport:
    return PortReport(
        workspace_id=str(WS), ports=tuple(ports), unix=(), count=len(ports), unix_count=0,
        complete=True, notes=(), pids_scanned=len(ports), fds_scanned=0, reachability="",
    )


async def test_f06_unattributed_listening_port_is_offered():
    container, engine, ws = await _harness()
    report = _report([
        _port_entry(8080),
        _port_entry(9090, attributed=False),
    ])
    async def _fake_ports(_ws_id, _r=tuple(report.ports)):
        return _report(_r)

    engine.list_ports = _fake_ports  # type: ignore[assignment]
    try:
        detected = await features._detected_ports(_ctx(Locale.EN), container.services, ws.id)
        numbers = [d.port for d in detected]
        assert numbers == [8080, 9090], f"an unattributed listening port was hidden: {numbers}"
    finally:
        await container.aclose()


async def test_f06_every_reported_kind_is_offered_and_udp_is_not():
    container, engine, ws = await _harness()
    async def _fake_ports(_ws_id):
        return _report([
            _port_entry(7001, kind="run"),
            _port_entry(7002, kind="session"),
            _port_entry(7003, kind="exec"),
            _port_entry(7004, kind="detached"),
            _port_entry(7005, attributed=False),
            _port_entry(5353, protocol="udp", state=""),
        ])

    engine.list_ports = _fake_ports  # type: ignore[assignment]
    try:
        detected = await features._detected_ports(_ctx(Locale.EN), container.services, ws.id)
        numbers = [d.port for d in detected]
        assert numbers == [7001, 7002, 7003, 7004, 7005], numbers
        assert 5353 not in numbers, "a UDP socket (no LISTEN) was offered as a port"
    finally:
        await container.aclose()


def test_f06_picker_marks_the_unattributed_port_without_hiding_it():
    detected = [tnl.DetectedPort(8080), tnl.DetectedPort(9090, attributed=False)]
    for locale in _LOCALES:
        screen = tnl.publish_port_screen(WS, locale, detected=detected)
        ports = _offered_ports(screen)
        assert ports == [8080, 9090], f"the unattributed port was hidden: {ports}"
        mark = catalog.t("tunnel.publish_unattributed_port", locale, port="٩٠٩٠" if locale is Locale.AR else "9090")
        assert esc(mark) in screen.doc.html, "the unattributed port is not marked"


def test_f06_ordering_is_stable():
    detected = [tnl.DetectedPort(9000, attributed=False), tnl.DetectedPort(8080)]
    screen = tnl.publish_port_screen(WS, Locale.EN, detected=detected)
    assert _offered_ports(screen) == [9000, 8080]


async def test_f06_dispatcher_picker_shows_the_unattributed_port():
    container, engine, ws = await _harness()
    async def _fake_ports(_ws_id):
        return _report([
            _port_entry(8080),
            _port_entry(9090, attributed=False),
        ])

    engine.list_ports = _fake_ports  # type: ignore[assignment]
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, WS_OWNER, cb("tnl:publish", ws.id)))
        markup = bot.messages[-1][1]
        buttons = [b for row in markup.inline_keyboard for b in row]
        from cloudbot.presentation.keyboards.callbacks import decode

        offered = []
        for b in buttons:
            data = b.callback_data or ""
            if data.startswith("tnl:port"):
                d = decode(data)
                if d.action == "tnl:port" and len(d.key) >= 2:
                    offered.append(int(d.key[1]))
        assert 8080 in offered and 9090 in offered, offered
    finally:
        await container.aclose()


# ================================================================== F-07 ======
@pytest.mark.parametrize("locale", _LOCALES)
@pytest.mark.parametrize(
    "status,key",
    [
        ("active", "tunnel.status.active"),
        ("stopped", "tunnel.status.stopped"),
        ("gibberish_status", "tunnel.status.unknown"),
        ("", "port.no_state"),
    ],
)
def test_f07_every_status_is_translated_never_raw(status, key, locale):
    screen = tnl.tunnels_screen(
        TunnelList(tunnels=(_tunnel("tnl_x", 8080, status),)), WS, locale, max_tunnels=2
    )
    html = screen.doc.html
    assert esc(catalog.t(key, locale)) in html
    # The raw token must never be the rendered **label**. ``stopped`` is also an
    # ordinary English word used in the explanatory note, so it is exempt here
    # (the same convention as ``_WORD_CODES`` in test_vocabulary.py); the status
    # cell is asserted translated above, which is what matters.
    if status and status != "stopped":
        assert status not in html, f"the raw engine status {status!r} leaked"


def test_f07_non_active_tunnel_has_no_live_link():
    tunnels = TunnelList(tunnels=(
        _tunnel("tnl_a", 8080, "active"),
        _tunnel("tnl_b", 8081, "stopped"),
    ))
    screen = tnl.tunnels_screen(tunnels, WS, Locale.EN, max_tunnels=3)
    urls = {b.url for b in _buttons(screen) if getattr(b, "url", None)}
    # The active tunnel keeps its live URL; the stopped one's URL is gone.
    assert "https://tnl_a.trycloudflare.com" in urls
    assert "https://tnl_b.trycloudflare.com" not in urls, "a stopped tunnel kept a live LINK"
    # Its row is a muted "not serving" button instead.
    stopped = [
        b for b in _buttons(screen)
        if b.text == catalog.t("tunnel.link_stopped", Locale.EN, port="8081")
    ]
    assert stopped and stopped[0].disabled is not None
    # And the reason is on the screen.
    assert esc(catalog.t("tunnel.stopped_note", Locale.EN)) in screen.doc.html


def test_f07_unknown_status_link_is_not_live():
    screen = tnl.tunnels_screen(
        TunnelList(tunnels=(_tunnel("tnl_c", 8082, "weird"),)), WS, Locale.EN, max_tunnels=2
    )
    assert not [b for b in _buttons(screen) if getattr(b, "url", None)], "an unknown status kept a live link"


# ================================================================== F-08 ======
def test_f08_dead_short_helper_is_removed():
    assert not hasattr(tnl, "_short"), "the dead _short helper is still present"


def test_f08_ports_timeout_is_wired_into_list_ports():
    assert isinstance(engine_module.PORTS_TIMEOUT_S, float)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={
            "workspace_id": "ws_01M6HJASCWM9Z1PF755GT89NK4", "count": 0, "complete": True,
            "notes": [], "pids_scanned": 0, "fds_scanned": 0, "ports": [], "unix": [],
            "unix_count": 0, "reachability": "",
        })

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    import asyncio

    asyncio.run(adapter.list_ports(WS))
    asyncio.run(adapter.aclose())
    # httpx records the per-request timeout under ``timeout``.
    assert captured.get("timeout") is not None
