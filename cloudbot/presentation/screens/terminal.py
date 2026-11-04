"""Terminal screens (Phase 1.5): my terminals, the terminal view, resize, ended.

Screens are pure functions of domain state + locale; no I/O. Every string comes
from a catalog key. The timeout notice is derived from the engine's session
configuration (``/v1/meta``), never a hardcoded number.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import SessionStatus, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_datetime,
    format_duration,
    localize_digits,
)
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import (
    SESSION_STATUS_EMOJI,
    session_reason_label,
    session_status_label,
)
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen

#: The engine's PTY-size bounds (API.md §POST /sessions).
MAX_COLS_ROWS = 1000
#: The documented default PTY size.
DEFAULT_COLS = 80
DEFAULT_ROWS = 24
#: Preset sizes offered on the resize screen (the default is first).
RESIZE_PRESETS: tuple[tuple[int, int], ...] = ((80, 24), (100, 30), (120, 40), (200, 50))
#: Keep the last ~12 KB of PTY output (rich text cap is 32768 chars).
MAX_OUTPUT_BYTES = 12 * 1024


def _raw(text: str) -> Html:
    return Html(text)


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _size(cols: int, rows: int, locale: Locale) -> str:
    return catalog.t("terminal.size", locale, cols=_num(cols, locale), rows=_num(rows, locale))


def timeout_notice(meta: EngineMeta, locale: Locale) -> str:
    """The idle/lifetime notice, from the engine's session config (not hardcoded)."""
    return catalog.t(
        "terminal.timeout_notice",
        locale,
        idle=format_duration(meta.sessions.idle_timeout_sec, locale),
        lifetime=format_duration(meta.sessions.lifetime_sec, locale),
    )


def _tail(text: str, *, max_bytes: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[-max_bytes:].decode("utf-8", errors="ignore"), True


def _short(ses_id: str) -> str:
    if len(ses_id) <= 14:
        return ses_id
    return ses_id[:12] + "…"


# -- my terminals ------------------------------------------------------------
def my_terminals(
    sessions: list[TerminalSession],
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    now: datetime | None = None,
) -> Screen:
    if now is None:
        now = datetime.now(timezone.utc)
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.my_title", locale))
    if not sessions:
        doc.p(catalog.t("terminal.empty", locale))
        doc.p(italic(catalog.t("terminal.empty_hint", locale)))
    else:
        doc.p(catalog.t("terminal.count", locale, n=_num(len(sessions), locale)))
        rows = []
        for session in sessions:
            emoji = SESSION_STATUS_EMOJI.get(session.status, "—")
            status = f"{emoji} {session_status_label(session.status, locale)}"
            rows.append(
                [
                    _raw(f"<code>{esc(_short(session.id))}</code>"),
                    status,
                    _size(session.cols, session.rows, locale),
                    format_datetime(session.created_at, locale),
                ]
            )
        doc.table(
            [
                catalog.t("terminal.column.session", locale),
                catalog.t("terminal.column.status", locale),
                catalog.t("terminal.column.size", locale),
                catalog.t("terminal.column.created", locale),
            ],
            rows,
        )
    return Screen(doc=doc, markup=my_terminals_markup(sessions, ws_id, locale))


def my_terminals_markup(sessions: list[TerminalSession], ws_id: WorkspaceId, locale: Locale):
    rows = []
    for session in sessions:
        label = f"🖥 {_short(session.id)}"
        rows.append([inline(label, _cb("term:view", ws_id, session.id), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("terminal.open", locale), _cb("term:open", ws_id), intent=Intent.SUCCESS)])
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


# -- terminal view -----------------------------------------------------------
def terminal_view(
    session: TerminalSession,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    output: str = "",
    truncated: bool = False,
    meta: EngineMeta | None = None,
) -> Screen:
    doc = RichDoc(locale)
    emoji = SESSION_STATUS_EMOJI.get(session.status, "—")
    status = f"{emoji} {session_status_label(session.status, locale)}"
    doc.h(2, catalog.t("terminal.title", locale))
    doc.p(_raw(f"<b>{catalog.t('terminal.status_line', locale)}</b> {status}"))
    doc.p(
        _raw(
            f"<b>{catalog.t('terminal.size_label', locale)}</b> {_size(session.cols, session.rows, locale)}"
        )
    )

    shown, trimmed = _tail(output or "")
    if shown:
        doc.pre(shown, language="text")
    elif session.status is SessionStatus.RUNNING:
        doc.p(italic(catalog.t("terminal.waiting", locale)))

    if truncated:
        doc.blockquote(catalog.t("terminal.truncated", locale))

    if session.status is SessionStatus.FAILED:
        # Never the engine diagnostic; a plain human line only.
        doc.p(italic(catalog.t("session.failed_note", locale)))
    if session.status.is_terminal and session.reason:
        doc.p(italic(session_reason_label(session.reason, locale)))

    if session.status is SessionStatus.RUNNING and meta is not None:
        doc.p(italic(timeout_notice(meta, locale)))

    return Screen(
        doc=doc,
        markup=terminal_view_markup(session, ws_id, locale),
    )


def terminal_view_markup(session: TerminalSession, ws_id: WorkspaceId, locale: Locale):
    ses_id = session.id
    rows = [
        [
            inline(catalog.t("terminal.send_command", locale), _cb("term:cmd", ws_id, ses_id), intent=Intent.PRIMARY),
            inline(catalog.t("terminal.ctrl_c", locale), _cb("term:signal", ws_id, ses_id, "INT"), intent=Intent.PRIMARY),
        ],
        [
            inline(catalog.t("terminal.ctrl_d", locale), _cb("term:ctrl_d", ws_id, ses_id), intent=Intent.PRIMARY),
            inline(catalog.t("terminal.resize", locale), _cb("term:resize", ws_id, ses_id), intent=Intent.PRIMARY),
        ],
        [
            inline(catalog.t("terminal.close", locale), _cb("term:close", ws_id, ses_id), intent=Intent.DANGER),
            inline(catalog.t("terminal.my_title", locale), _cb("term:list", ws_id), intent=Intent.PRIMARY),
        ],
        [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
    ]
    return inline_markup(rows)


# -- command prompt ----------------------------------------------------------
def command_prompt(ws_id: WorkspaceId, ses_id: str, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.command_title", locale))
    doc.p(catalog.t("terminal.command_prompt", locale))
    doc.p(italic(catalog.t("terminal.command_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("term:view", ws_id, ses_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- resize ------------------------------------------------------------------
def resize_screen(session: TerminalSession, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.resize_title", locale))
    doc.p(_raw(catalog.t("terminal.resize_body", locale)))
    doc.p(_raw(f"<b>{catalog.t('terminal.size_label', locale)}</b> {_size(session.cols, session.rows, locale)}"))
    return Screen(doc=doc, markup=resize_markup(session, ws_id, locale))


def resize_markup(session: TerminalSession, ws_id: WorkspaceId, locale: Locale):
    rows = []
    for cols, rowsn in RESIZE_PRESETS:
        label = catalog.t("terminal.size", locale, cols=_num(cols, locale), rows=_num(rowsn, locale))
        current = session.cols == cols and session.rows == rowsn
        rows.append(
            [
                inline(
                    label,
                    _cb("term:rsz", ws_id, session.id, f"{cols}x{rowsn}"),
                    intent=Intent.PRIMARY if not current else Intent.MUTED,
                    disabled=current,
                )
            ]
        )
    rows.append([inline(catalog.t("terminal.resize_custom", locale), _cb("term:rsz_custom", ws_id, session.id), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("common.back", locale), _cb("term:view", ws_id, session.id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


def resize_custom_prompt(ws_id: WorkspaceId, ses_id: str, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.resize_title", locale))
    doc.p(_raw(catalog.t("terminal.resize_custom_prompt", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("term:resize", ws_id, ses_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- ended -------------------------------------------------------------------
def ended_screen(session: TerminalSession, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.ended_title", locale))
    doc.p(catalog.t("terminal.ended_body", locale))
    if session.reason:
        doc.p(italic(session_reason_label(session.reason, locale)))
    markup = inline_markup(
        [
            [inline(catalog.t("terminal.new", locale), _cb("term:open", ws_id), intent=Intent.SUCCESS)],
            [inline(catalog.t("terminal.my_title", locale), _cb("term:list", ws_id), intent=Intent.PRIMARY)],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


__all__ = [
    "DEFAULT_COLS",
    "DEFAULT_ROWS",
    "MAX_COLS_ROWS",
    "RESIZE_PRESETS",
    "command_prompt",
    "ended_screen",
    "my_terminals",
    "my_terminals_markup",
    "resize_custom_prompt",
    "resize_markup",
    "resize_screen",
    "terminal_view",
    "terminal_view_markup",
    "timeout_notice",
]
