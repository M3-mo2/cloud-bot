"""Port screens (Phase 2c): listening ports, unix sockets, reachability.

The read is live on every call (nothing is cached). The engine's ``reachability``
sentence is surfaced **verbatim as a blockquote** (the roadmap's explicit rule);
``complete:false`` says the list is a partial view; a UDP entry's empty ``state``
is never rendered as if it were ``LISTEN``; an unattributed socket is reported and
never hidden or guessed; and the ``unix`` section is shown separately because a
unix socket is not a port.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import PortEntry, PortReport, UnixSocket, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def _scope_label(scope: str, locale: Locale) -> str:
    key = {
        "loopback": "port.scope.loopback",
        "wildcard": "port.scope.wildcard",
        "specific": "port.scope.specific",
    }.get(scope)
    if key is None:
        return scope or "—"
    return catalog.t(key, locale)


def _kind_label(kind: str, locale: Locale) -> str:
    key = {
        "run": "port.kind.run",
        "session": "port.kind.session",
        "exec": "port.kind.exec",
        "detached": "port.kind.detached",
    }.get(kind)
    return catalog.t(key, locale) if key else (kind or "")


def _owner_label(entry: PortEntry, locale: Locale) -> str:
    if not entry.attributed or entry.pid is None:
        # Never hide and never guess: say the owner is unknown.
        return catalog.t("port.unattributed", locale)
    kind = _kind_label(entry.kind, locale)
    pid = catalog.t("port.owner_pid", locale, pid=_num(entry.pid, locale))
    return f"{kind} · {pid}" if kind else pid


def ports_screen(report: PortReport, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("port.title", locale))
    doc.p(catalog.t("port.count", locale, n=_num(report.count, locale)))

    if not report.ports:
        doc.p(catalog.t("port.empty", locale))
        doc.p(italic(catalog.t("port.empty_hint", locale)))
    else:
        rows = []
        for entry in report.ports:
            state = entry.state if entry.state else catalog.t("port.no_state", locale)
            rows.append(
                [
                    _raw(f"<code>{_num(entry.port, locale)}</code>"),
                    _raw(f"<code>{esc(entry.protocol)}</code>"),
                    _scope_label(entry.scope, locale),
                    _owner_label(entry, locale),
                    state,
                ]
            )
        doc.table(
            [
                catalog.t("port.column.port", locale),
                catalog.t("port.column.protocol", locale),
                catalog.t("port.column.scope", locale),
                catalog.t("port.column.owner", locale),
                catalog.t("port.column.state", locale),
            ],
            rows,
        )

    # A partial view must say so.
    if not report.complete:
        doc.blockquote(catalog.t("port.partial", locale))
        if report.notes:
            doc.p(italic(catalog.t("port.partial_notes", locale, notes=", ".join(esc(n) for n in report.notes))))

    # The reachability sentence, verbatim, as a blockquote.
    if report.reachability:
        doc.blockquote(report.reachability)

    doc.p(
        italic(
            catalog.t(
                "port.scanned",
                locale,
                pids=_num(report.pids_scanned, locale),
                fds=_num(report.fds_scanned, locale),
            )
        )
    )

    # Unix sockets are a separate section — not ports.
    if report.unix:
        doc.div()
        doc.p(_raw(f"<b>{catalog.t('port.unix_title', locale)}</b>"))
        rows = []
        for sock in report.unix:
            rows.append(
                [
                    _raw(f"<code>{esc(sock.path)}</code>"),
                    _raw(f"<code>{esc(sock.type)}</code>") if sock.type else "—",
                    _unix_owner(sock, locale),
                    sock.state or catalog.t("port.no_state", locale),
                ]
            )
        doc.table(
            [
                catalog.t("port.unix_column.path", locale),
                catalog.t("port.unix_column.type", locale),
                catalog.t("port.column.owner", locale),
                catalog.t("port.unix_column.state", locale),
            ],
            rows,
        )

    return Screen(doc=doc, markup=ports_markup(ws_id, locale))


def _unix_owner(sock: UnixSocket, locale: Locale) -> str:
    if not sock.attributed or sock.pid is None:
        return catalog.t("port.unattributed", locale)
    kind = _kind_label(sock.kind, locale)
    pid = catalog.t("port.owner_pid", locale, pid=_num(sock.pid, locale))
    return f"{kind} · {pid}" if kind else pid


def ports_markup(ws_id: WorkspaceId, locale: Locale):
    return inline_markup(
        [
            [
                inline(catalog.t("port.refresh", locale), _cb("port:list", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("port.publish", locale), _cb("tnl:publish", ws_id), intent=Intent.SUCCESS),
            ],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
