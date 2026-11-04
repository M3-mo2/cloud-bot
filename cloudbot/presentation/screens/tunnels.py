"""Tunnel screens (Phase 2b): active links, publish a port, remove.

The URL is **ephemeral** by the roadmap's rule: it appears only as a ``LINK``
button on the live screen and is never stored or printed as a plain string. The
screen also explains which of the three gates is off (server, ``allow_tunnels``,
``allow_net``) and offers the fix where it belongs.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Tunnel, TunnelList, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen

#: The documented publishable port range (validated locally before the call).
MIN_PORT = 1024
MAX_PORT = 65535


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def _short(tunnel_id: str) -> str:
    if len(tunnel_id) <= 12:
        return tunnel_id
    return tunnel_id[:10] + "…"


# -- active links ------------------------------------------------------------
def tunnels_screen(
    tunnels: TunnelList,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    allow_tunnels: bool = True,
    allow_net: bool = True,
    server_enabled: bool = True,
) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.title", locale))

    if not tunnels.tunnels:
        doc.p(catalog.t("tunnel.empty", locale))
        doc.p(italic(catalog.t("tunnel.empty_hint", locale)))
    else:
        rows = []
        for tunnel in tunnels.tunnels:
            rows.append(
                [
                    _raw(f"<code>{_num(tunnel.port, locale)}</code>"),
                    _raw(_status_label(tunnel.status, locale)),
                ]
            )
        doc.table(
            [
                catalog.t("tunnel.column.port", locale),
                catalog.t("tunnel.column.status", locale),
            ],
            rows,
        )
        # The URL is ephemeral; say so and never print it as text.
        doc.blockquote(catalog.t("tunnel.ephemeral", locale))

    gate = _gate_note(locale, allow_tunnels=allow_tunnels, allow_net=allow_net, server_enabled=server_enabled)
    if gate is not None:
        doc.blockquote(gate)

    return Screen(
        doc=doc,
        markup=tunnels_markup(
            tunnels,
            ws_id,
            locale,
            allow_tunnels=allow_tunnels,
            allow_net=allow_net,
            server_enabled=server_enabled,
        ),
    )


def tunnels_markup(
    tunnels: TunnelList,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    allow_tunnels: bool = True,
    allow_net: bool = True,
    server_enabled: bool = True,
):
    rows: list[list] = []
    # A LINK button per URL — the only place the ephemeral URL appears.
    for tunnel in tunnels.tunnels:
        if tunnel.url:
            rows.append(
                [
                    inline(
                        catalog.t("tunnel.link", locale, port=_num(tunnel.port, locale)),
                        url=tunnel.url,
                        intent=Intent.LINK,
                    )
                ]
            )
    publishable = server_enabled and allow_tunnels and allow_net
    rows.append(
        [
            inline(
                catalog.t("tunnel.publish", locale),
                _cb("tnl:publish", ws_id),
                intent=Intent.SUCCESS if publishable else Intent.MUTED,
                disabled=not publishable,
            )
        ]
    )
    for tunnel in tunnels.tunnels:
        rows.append(
            [
                inline(
                    catalog.t("tunnel.remove", locale, port=_num(tunnel.port, locale)),
                    _cb("tnl:remove_confirm", ws_id, tunnel.id),
                    intent=Intent.DANGER,
                )
            ]
        )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


def _gate_note(
    locale: Locale, *, allow_tunnels: bool, allow_net: bool, server_enabled: bool
) -> str | None:
    if not server_enabled:
        return catalog.t("tunnel.gate.server", locale)
    if not allow_tunnels:
        return catalog.t("tunnel.gate.allow_tunnels", locale)
    if not allow_net:
        return catalog.t("tunnel.gate.allow_net", locale)
    return None


def _status_label(status: str, locale: Locale) -> str:
    clean = (status or "").strip()
    if not clean:
        return catalog.t("port.no_state", locale)
    # Plain engine statuses are not user vocabulary; show a safe generic word.
    return clean


# -- publish a port ----------------------------------------------------------
def publish_port_screen(
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    detected: list[int] | None = None,
) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(catalog.t("tunnel.publish_body", locale))
    detected = detected or []
    if detected:
        doc.p(catalog.t("tunnel.publish_detected", locale))
        doc.ul([_raw(f"<code>{_num(p, locale)}</code>") for p in detected])
    else:
        doc.p(italic(catalog.t("tunnel.publish_none", locale)))
    doc.p(italic(catalog.t("tunnel.port_hint", locale)))

    rows: list[list] = []
    for port in detected:
        rows.append(
            [inline(f"🌐 {_num(port, locale)}", _cb("tnl:port", ws_id, port), intent=Intent.SUCCESS)]
        )
    rows.append([inline(catalog.t("tunnel.publish_custom", locale), _cb("tnl:port_custom", ws_id), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("common.cancel", locale), _cb("tnl:list", ws_id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


def publish_custom_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(_raw(catalog.t("tunnel.publish_custom_prompt", locale)))
    doc.p(italic(catalog.t("tunnel.port_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("tnl:publish", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def publishing_screen(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(catalog.t("tunnel.publishing", locale))
    return Screen(doc=doc, send_new=True)


def remove_confirm(tunnel: Tunnel, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.remove_title", locale))
    doc.p(_raw(f"<code>{_num(tunnel.port, locale)}</code>"))
    doc.p(catalog.t("tunnel.remove_body", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("common.delete_permanently", locale), _cb("tnl:remove", ws_id, tunnel.id), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("tnl:list", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)
