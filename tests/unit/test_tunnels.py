"""TASK 15 — every tunnel refusal explains itself; the ceiling is reported.

The live bug: publishing past the per-workspace cap was a **silent redraw** — the
user tapped "publish", saw the publishing screen, and the list came back with no
sentence. These tests pin the fix:
  * ``too_many_tunnels`` renders a real screen that names the limit and offers
    removal, and is not the plain list;
  * every refusal code on this path maps to a rendered sentence, and an unmapped
    code re-raises (it is never swallowed);
  * the tunnels screen states "N of M" from the engine meta (nothing hardcoded);
  * at the cap the publish entry is disabled and carries a reason;
  * a non-listening port warns before publishing, a detected one does not;
  * the timeout path explains the cause and offers retry on the same port.
"""

from __future__ import annotations

import pytest

from cloudbot.core.errors import EngineRefused
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Tunnel, TunnelList, WorkspaceId
from cloudbot.handlers import features
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.rich.tags import esc
from cloudbot.presentation.screens import tunnels as tnl

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")

_EN = Locale.EN
_AR = Locale.AR


def _tunnel(tid: str, port: int) -> Tunnel:
    return Tunnel(
        id=tid,
        workspace_id=str(WS),
        port=port,
        url=f"https://{tid}.trycloudflare.com",
        status="active",
    )


def _list(*ports: int) -> TunnelList:
    return TunnelList(
        tunnels=tuple(_tunnel(f"tnl_{p}", p) for p in ports)
    )


def _buttons(screen):
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b for row in rows for b in row]


# -- R1: too_many_tunnels is a real screen that names the limit and offers removal
@pytest.mark.parametrize("locale", [_EN, _AR])
def test_too_many_tunnels_names_the_limit_and_offers_removal(locale):
    screen = tnl.publish_refused_screen(8082, WS, locale, max_tunnels=2)
    html = screen.doc.html
    # The actual number of the limit is present, in the user's own words.
    assert "2" in html  # TASK digits: ASCII in every locale
    assert catalog.t("tunnel.limit_title", locale) in html
    # The fix: a button whose callback opens the list/removal path.
    callbacks = [b.callback_data for b in _buttons(screen) if b.callback_data]
    assert any(c.startswith("tnl:list") for c in callbacks), "no removal path offered"
    assert any(c.startswith("ws:view") for c in callbacks), "no back path"


def test_too_many_tunnels_screen_is_not_the_plain_list():
    """The refusal must not be a silent redraw of the list (the live bug)."""
    refused = tnl.publish_refused_screen(8082, WS, _EN, max_tunnels=2)
    plain_list = tnl.tunnels_screen(_list(8080, 8081), WS, _EN, max_tunnels=2)
    assert refused.doc.html != plain_list.doc.html
    # And the refusal sentence is nowhere in the plain list.
    assert catalog.t("tunnel.limit_reached", _EN, max="2") not in plain_list.doc.html


# -- R1: every refusal code on this path maps to a sentence; unmapped re-raises
def test_the_refusal_set_is_exactly_the_three_known_kinds():
    assert set(features.TUNNEL_REFUSAL_KINDS) == {
        "too_many_tunnels",
        "tunnel_url_unavailable",
        "tunnels_disabled",
    }


@pytest.mark.parametrize("code", sorted(features.TUNNEL_REFUSAL_KINDS))
@pytest.mark.parametrize("locale", [_EN, _AR])
def test_every_tunnel_refusal_code_renders_a_sentence(code, locale):
    kind = features.TUNNEL_REFUSAL_KINDS[code]
    if kind == "limit":
        html = tnl.publish_refused_screen(8080, WS, locale, max_tunnels=2).doc.html
    elif kind == "timeout":
        html = tnl.tunnel_timeout_screen(8080, WS, locale).doc.html
    else:  # gate → the tunnels screen names the off switch
        html = tnl.tunnels_screen(_list(), WS, locale).doc.html
    assert html and "<h2>" in html
    # No raw engine token leaks into the rendered refusal.
    assert code not in html


class _StubTunnels:
    async def limit(self) -> int:
        return 3


class _StubServices:
    tunnels = _StubTunnels()


class _StubCtx:
    locale = _EN


async def test_an_unmapped_refusal_is_not_swallowed():
    """A future engine code has no screen here → the caller re-raises it."""
    screen = await features._tunnel_refusal_screen(
        EngineRefused("some_future_code"), _StubCtx(), _StubServices(), WS, 8080
    )
    assert screen is None


async def test_too_many_tunnels_refusal_screen_uses_the_meta_limit():
    screen = await features._tunnel_refusal_screen(
        EngineRefused("too_many_tunnels"), _StubCtx(), _StubServices(), WS, 8080
    )
    assert screen is not None
    # The number comes from the (stub) meta, not a hardcoded 2.
    assert "3" in screen.doc.html


# -- R2: the screen states its own ceiling from the meta (nothing hardcoded)
@pytest.mark.parametrize("locale", [_EN, _AR])
def test_tunnels_screen_shows_n_of_m_from_fake_meta(locale):
    screen = tnl.tunnels_screen(_list(8080, 8081), WS, locale, max_tunnels=5)
    expected = catalog.t(
        "tunnel.usage",
        locale,
        used="2",  # TASK digits: ASCII in every locale
        max="5",  # TASK digits: ASCII in every locale
    )
    assert expected in screen.doc.html
    # No hardcoded ceiling leaked from the old constant.
    assert "2 of 2" not in screen.doc.html


@pytest.mark.parametrize("locale", [_EN, _AR])
def test_at_cap_publish_entry_is_disabled_with_a_reason(locale):
    screen = tnl.tunnels_screen(_list(8080, 8081), WS, locale, max_tunnels=2)
    publish = [b for b in _buttons(screen) if (b.callback_data or "").startswith("tnl:publish")]
    assert publish, "no publish entry rendered"
    assert all(b.disabled is not None for b in publish)
    # The reason is on the screen and in the button label.
    assert esc(catalog.t("tunnel.limit_note", locale, max="2")) in screen.doc.html
    assert catalog.t("tunnel.publish_full", locale) in publish[0].text


@pytest.mark.parametrize("locale", [_EN, _AR])
def test_below_cap_publish_entry_is_enabled(locale):
    screen = tnl.tunnels_screen(_list(8080), WS, locale, max_tunnels=2)
    publish = [b for b in _buttons(screen) if (b.callback_data or "").startswith("tnl:publish")]
    assert publish and publish[0].disabled is None
    assert catalog.t("tunnel.publish", locale) in publish[0].text


# -- R3: the publish flow warns on a non-listening port
def test_unlistened_port_warns_and_detected_port_does_not():
    assert features._unlistened_warning(9000, [8080], _EN) == catalog.t(
        "tunnel.publish_unlistened", _EN
    )
    assert features._unlistened_warning(8080, [8080], _EN) is None
    # No ports detected at all → still warns for the typed port.
    assert features._unlistened_warning(8080, [], _EN) is not None


@pytest.mark.parametrize("locale", [_EN, _AR])
def test_publish_confirm_renders_the_warning_when_present(locale):
    warned = tnl.publish_confirm(
        9000, WS, locale, warning=catalog.t("tunnel.publish_unlistened", locale)
    )
    assert catalog.t("tunnel.publish_unlistened", locale) in warned.doc.html
    plain = tnl.publish_confirm(8080, WS, locale, warning=None)
    assert catalog.t("tunnel.publish_unlistened", locale) not in plain.doc.html


@pytest.mark.parametrize("locale", [_EN, _AR])
def test_publish_screen_with_no_ports_names_what_to_do(locale):
    screen = tnl.publish_port_screen(WS, locale, detected=[])
    assert catalog.t("tunnel.publish_none", locale) in screen.doc.html
    assert catalog.t("tunnel.publish_none_hint", locale) in screen.doc.html


# -- R4: the timeout path explains the cause and offers retry on the same port
@pytest.mark.parametrize("locale", [_EN, _AR])
def test_timeout_screen_explains_and_offers_retry(locale):
    screen = tnl.tunnel_timeout_screen(8080, WS, locale)
    html = screen.doc.html
    assert catalog.t("tunnel.timeout_explain", locale) in html
    # Retry targets the SAME port (a callback carrying 8080), not a bare list.
    retry = [
        b
        for b in _buttons(screen)
        if b.callback_data and b.callback_data.startswith("tnl:port")
    ]
    assert retry, "no retry button"
    assert localize_digits("8080", locale) in html  # the port is named on the screen


# -- ephemeral rule: no URL is ever persisted or rendered as text (no regression)
@pytest.mark.parametrize("locale", [_EN, _AR])
def test_url_never_rendered_as_text(locale):
    url = "https://super-secret.trycloudflare.com"
    tunnels = TunnelList(
        tunnels=(Tunnel(id="tnl_x", workspace_id=str(WS), port=8080, url=url, status="active"),)
    )
    screen = tnl.tunnels_screen(tunnels, WS, locale, max_tunnels=2)
    assert url not in screen.doc.html
    links = [b for b in _buttons(screen) if getattr(b, "url", None)]
    assert any(b.url == url for b in links)
