"""Tunnel screens (Phase 2b): active links, publish a port, remove.

The URL is **ephemeral** by the roadmap's rule: it appears only as a ``LINK``
button on the live screen and is never stored or printed as a plain string. The
screen also explains which gate is off — the workspace lifecycle status
(suspended/locked/deleted/unknown, F-04), the server switch
(``tunnels.enabled``), or one of the two workspace switches (``allow_tunnels``,
``allow_net``) — and offers the fix where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    Tunnel,
    TunnelList,
    WorkspaceId,
    WorkspaceStatus,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.pagination import (
    footer as page_footer,
    pager_rows as _pager_rows,
    window,
)
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen

#: The documented publishable port range (validated locally before the call).
MIN_PORT = 1024
MAX_PORT = 65535


@dataclass(frozen=True)
class DetectedPort:
    """One listening TCP port offered by the publish picker.

    ``attributed`` is **display only**: a socket with no discoverable owner is
    still offered (marked "owner unknown"), never hidden (F-06, the 2c rule).
    """

    port: int
    attributed: bool = True


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


#: Engine lifecycle status → the publish gate sentence (F-04). ``active`` is the
#: only status that may publish; every other status names itself in the reason.
_LIFECYCLE_GATE_KEYS: dict[WorkspaceStatus, str] = {
    WorkspaceStatus.SUSPENDED: "tunnel.gate.suspended",
    WorkspaceStatus.LOCKED: "tunnel.gate.locked",
    WorkspaceStatus.DELETED: "tunnel.gate.deleted",
    WorkspaceStatus.UNKNOWN: "tunnel.gate.unknown",
}


def _lifecycle_gate_key(status: WorkspaceStatus) -> str | None:
    return _LIFECYCLE_GATE_KEYS.get(status)


# -- active links ------------------------------------------------------------
def tunnels_screen(
    tunnels: TunnelList,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    allow_tunnels: bool = True,
    allow_net: bool = True,
    server_enabled: bool = True,
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
    max_tunnels: int | None = None,
    note: str | None = None,
    gate: str | None = None,
    page: int = 0,
) -> Screen:
    """The public-links list, its gate, and (when known) its own ceiling.

    ``max_tunnels`` comes from the engine meta (``tunnels.max_per_workspace``) —
    never a hardcoded constant. When the cap is reached the publish entry is
    muted with a reason so the ceiling is visible *before* it is hit. ``note`` is
    a one-line, already-localized explanation (e.g. a port already has a tunnel,
    F-03). ``status`` is the workspace's lifecycle status: a suspended/locked/
    deleted workspace is a gate too (F-04), so Publish is muted with its reason.
    ``gate`` (already-localized) **overrides** the computed gate sentence —
    the publish refusal uses it to show the **engine's own** reason (F-05) instead
    of the bot's possibly-stale cached limits.
    """
    page_tunnels, total_pages, _has_more = window(list(tunnels.tunnels), page)
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.title", locale))
    at_cap = max_tunnels is not None and tunnels.count >= max_tunnels

    if note:
        doc.blockquote(note)

    if not tunnels.tunnels:
        doc.p(catalog.t("tunnel.empty", locale))
        doc.p(italic(catalog.t("tunnel.empty_hint", locale)))
    else:
        rows = []
        for tunnel in page_tunnels:
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
        doc.p(page_footer(page, total_pages, locale))
        # The URL is ephemeral; say so and never print it as text.
        doc.blockquote(catalog.t("tunnel.ephemeral", locale))
        # F-07: a non-active tunnel's link no longer serves — say so, do not
        # keep a live-looking Open button for it.
        if any(_is_live(t.status) is False for t in page_tunnels):
            doc.blockquote(catalog.t("tunnel.stopped_note", locale))

    # The ceiling is always stated when the engine reports one (R2).
    if max_tunnels is not None:
        doc.p(
            catalog.t(
                "tunnel.usage",
                locale,
                used=_num(tunnels.count, locale),
                max=_num(max_tunnels, locale),
            )
        )
        if at_cap:
            doc.blockquote(
                catalog.t("tunnel.limit_note", locale, max=_num(max_tunnels, locale))
            )

    gate = gate or _gate_note(
        locale,
        allow_tunnels=allow_tunnels,
        allow_net=allow_net,
        server_enabled=server_enabled,
        status=status,
    )
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
            status=status,
            max_tunnels=max_tunnels,
            page=page,
            total_pages=total_pages,
            page_tunnels=page_tunnels,
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
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
    max_tunnels: int | None = None,
    page: int = 0,
    total_pages: int = 1,
    page_tunnels: list[Tunnel] | None = None,
):
    if page_tunnels is None:
        page_tunnels, total_pages, _has_more = window(list(tunnels.tunnels), page)
    rows: list[list] = []
    # A LINK button per URL — the only place the ephemeral URL appears. A tunnel
    # that is not ``active`` no longer serves: its URL is **removed** and replaced
    # by a muted "not serving" button (F-07), so the user is never invited to a
    # dead link. An unknown status is treated as not-live (fail safe).
    for tunnel in page_tunnels:
        if not tunnel.url:
            continue
        if _is_live(tunnel.status) is True:
            rows.append(
                [
                    inline(
                        catalog.t("tunnel.link", locale, port=_num(tunnel.port, locale)),
                        url=tunnel.url,
                        intent=Intent.LINK,
                    )
                ]
            )
        else:
            rows.append(
                [
                    inline(
                        catalog.t(
                            "tunnel.link_stopped", locale, port=_num(tunnel.port, locale)
                        ),
                        intent=Intent.MUTED,
                        disabled=True,
                    )
                ]
            )
    at_cap = max_tunnels is not None and tunnels.count >= max_tunnels
    lifecycle_ok = _lifecycle_gate_key(status) is None
    publishable = server_enabled and allow_tunnels and allow_net and lifecycle_ok
    if at_cap:
        # Disabled-with-reason: the label itself names the ceiling (R2).
        rows.append(
            [
                inline(
                    catalog.t("tunnel.publish_full", locale),
                    _cb("tnl:publish", ws_id),
                    intent=Intent.MUTED,
                    disabled=True,
                )
            ]
        )
    else:
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
    # Remove buttons follow the page, never the whole list.
    for tunnel in page_tunnels:
        rows.append(
            [
                inline(
                    catalog.t("tunnel.remove", locale, port=_num(tunnel.port, locale)),
                    _cb("tnl:remove_confirm", ws_id, tunnel.id),
                    intent=Intent.DANGER,
                )
            ]
        )
    rows += _pager_rows(
        page, total_pages, lambda target: _cb("tnl:list", ws_id, target), locale
    )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


def _gate_note(
    locale: Locale,
    *,
    allow_tunnels: bool,
    allow_net: bool,
    server_enabled: bool,
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
) -> str | None:
    # The lifecycle is the first gate: a paused/locked/gone workspace cannot
    # publish at all, whatever the three tunnel switches say (F-04).
    lifecycle_key = _lifecycle_gate_key(status)
    if lifecycle_key is not None:
        return catalog.t(lifecycle_key, locale)
    if not server_enabled:
        return catalog.t("tunnel.gate.server", locale)
    if not allow_tunnels:
        return catalog.t("tunnel.gate.allow_tunnels", locale)
    if not allow_net:
        return catalog.t("tunnel.gate.allow_net", locale)
    return None


#: Engine tunnel status → a translated label (F-07). Anything unknown falls back
#: to the generic ``tunnel.status.unknown`` — never the raw engine token.
_TUNNEL_STATUS_KEYS: dict[str, str] = {
    "active": "tunnel.status.active",
    "stopped": "tunnel.status.stopped",
}


def _status_label(status: str, locale: Locale) -> str:
    clean = (status or "").strip().lower()
    if not clean:
        return catalog.t("port.no_state", locale)
    return catalog.t(_TUNNEL_STATUS_KEYS.get(clean, "tunnel.status.unknown"), locale)


def _is_live(status: str) -> bool:
    """Whether a tunnel in this status still serves its URL. Unknown ⇒ no."""
    return (status or "").strip().lower() == "active"


# -- publish a port ----------------------------------------------------------
def _as_detected(value: DetectedPort | int) -> DetectedPort:
    if isinstance(value, DetectedPort):
        return value
    return DetectedPort(port=int(value))


def publish_port_screen(
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    detected: list[DetectedPort | int] | None = None,
) -> Screen:
    """Pick a port to publish. **Every** reported listening TCP port is offered
    (F-06); an unattributed port is marked "owner unknown", never hidden. A
    non-listening port goes through the confirm warning (R3)."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(catalog.t("tunnel.publish_body", locale))
    detected = [_as_detected(p) for p in (detected or [])]
    if detected:
        doc.p(catalog.t("tunnel.publish_detected", locale))
        doc.ul(_raw(_detected_label(p, locale)) for p in detected)
    else:
        doc.p(italic(catalog.t("tunnel.publish_none", locale)))
        doc.p(italic(catalog.t("tunnel.publish_none_hint", locale)))
    doc.p(italic(catalog.t("tunnel.port_hint", locale)))

    rows: list[list] = []
    for entry in detected:
        rows.append(
            [
                inline(
                    _detected_button_label(entry, locale),
                    _cb("tnl:port", ws_id, entry.port),
                    intent=Intent.SUCCESS,
                )
            ]
        )
    rows.append([inline(catalog.t("tunnel.publish_custom", locale), _cb("tnl:port_custom", ws_id), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("common.cancel", locale), _cb("tnl:list", ws_id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


def _detected_label(entry: DetectedPort, locale: Locale) -> str:
    if entry.attributed:
        return f"<code>{_num(entry.port, locale)}</code>"
    return catalog.t("tunnel.publish_unattributed_port", locale, port=_num(entry.port, locale))


def _detected_button_label(entry: DetectedPort, locale: Locale) -> str:
    if entry.attributed:
        return f"🌐 {_num(entry.port, locale)}"
    return catalog.t("tunnel.publish_unattributed_port", locale, port=_num(entry.port, locale))


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


def publish_confirm(port: int, ws_id: WorkspaceId, locale: Locale, *, warning: str | None = None) -> Screen:
    """Confirm a typed port before publishing (R5: nothing fires from a prompt).

    ``warning`` is the non-listening sentence, shown when the port is not in the
    workspace's detected set (R3); it never blocks — starting a server later is
    legitimate.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(catalog.t("tunnel.confirm_body", locale, port=_num(port, locale)))
    if warning:
        doc.blockquote(warning)
    doc.p(italic(catalog.t("tunnel.port_hint", locale)))
    markup = inline_markup(
        [
            [inline(catalog.t("common.confirm", locale), _cb("tnl:port", ws_id, port), intent=Intent.SUCCESS)],
            [inline(catalog.t("common.cancel", locale), _cb("tnl:publish", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


def publishing_screen(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.publish_title", locale))
    doc.p(catalog.t("tunnel.publishing", locale))
    # No ``send_new``: the progress indicator edits the message that will carry
    # the result (F-02) instead of leaving an orphaned "Publishing…" behind.
    return Screen(doc=doc)


def publish_refused_screen(
    port: int,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    max_tunnels: int | None = None,
) -> Screen:
    """The screen shown when the engine refuses with ``too_many_tunnels`` (R1).

    It states the limit **with the actual number** and offers the fix: a button
    that opens the list/removal path. It is deliberately **not** the plain list,
    so the refusal is never a silent redraw.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.limit_title", locale))
    if max_tunnels is not None:
        doc.p(catalog.t("tunnel.limit_reached", locale, max=_num(max_tunnels, locale)))
    else:
        doc.p(catalog.t("err.too_many_tunnels", locale))
    doc.p(_raw(f"<code>{_num(port, locale)}</code>"))
    markup = inline_markup(
        [
            # The fix: go to the list and remove one. ``tnl:list`` is the same
            # action the list's own remove buttons live on, so removal is one tap.
            [inline(catalog.t("tunnel.remove_one", locale), _cb("tnl:list", ws_id), intent=Intent.DANGER)],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


def tunnel_timeout_screen(port: int, ws_id: WorkspaceId, locale: Locale) -> Screen:
    """The ``tunnel_url_unavailable`` screen: explain the cause and retry (R4)."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("tunnel.timeout_title", locale))
    doc.p(catalog.t("err.tunnel_url_unavailable", locale))
    doc.p(catalog.t("tunnel.timeout_explain", locale))
    doc.p(_raw(f"<code>{_num(port, locale)}</code>"))
    markup = inline_markup(
        [
            # Retry the same port — never dump the user back to a list.
            [inline(catalog.t("tunnel.retry", locale, port=_num(port, locale)), _cb("tnl:port", ws_id, port), intent=Intent.SUCCESS)],
            [inline(catalog.t("common.back", locale), _cb("tnl:list", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


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
