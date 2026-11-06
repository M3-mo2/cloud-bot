"""Terminal screens (Phase 1.5): my terminals, the terminal view, ended.

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
    format_duration,
    format_relative,
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
from cloudbot.presentation.pagination import PAGE_SIZE as _PAGE_SIZE
from cloudbot.presentation.pagination import footer as page_footer
from cloudbot.presentation.pagination import pager_rows as _pager_rows
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens import terminal_callbacks as _term
from cloudbot.presentation.screens.screen import Screen

#: The project-wide page size (one pagination contract, not two).
PAGE_SIZE = _PAGE_SIZE
#: Keep the last ~12 KB of PTY output (rich text cap is 32768 chars).
MAX_OUTPUT_BYTES = 12 * 1024


def _raw(text: str) -> Html:
    return Html(text)


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


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


def _created_at(session: TerminalSession) -> float:
    return session.created_at.timestamp() if session.created_at else 0.0


def order_sessions(sessions: list[TerminalSession]) -> list[TerminalSession]:
    """Running first, then newest.

    Opening *my terminals* is about the live session; a user must not have to
    scan past finished ones (or an arbitrary fetch order) to find it. The sort
    happens over the **whole** list before the page window is cut, so paging is
    stable and the live terminal is on page 1.
    """
    return sorted(
        sessions,
        key=lambda session: (session.status is not SessionStatus.RUNNING, -_created_at(session)),
    )


# -- my terminals ------------------------------------------------------------
def my_terminals(
    sessions: list[TerminalSession],
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page: int = 0,
    total_pages: int = 1,
    has_more: bool = False,
    now: datetime | None = None,
) -> Screen:
    """A compact, paginated list — one line per terminal of this page.

    It deliberately has no size column: the chat is the viewport and the size
    is not a user-facing notion (the shell's ``stty`` is the in-session tool).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.my_title", locale))
    if not sessions:
        doc.p(catalog.t("terminal.empty", locale))
        doc.p(italic(catalog.t("terminal.empty_hint", locale)))
    else:
        doc.p(catalog.t("terminal.count", locale, n=_num(len(sessions), locale)))
        for session in sessions:
            emoji = SESSION_STATUS_EMOJI.get(session.status, "—")
            status = f"{emoji} {session_status_label(session.status, locale)}"
            age = format_relative(session.created_at, now, locale)
            doc.p(
                _raw(
                    f"<code>{esc(_short(session.id))}</code> · {esc(status)} · {esc(age)}"
                )
            )
        doc.p(page_footer(page, total_pages, locale))
    return Screen(
        doc=doc,
        markup=my_terminals_markup(
            sessions, ws_id, locale, page=page, total_pages=total_pages
        ),
    )


def my_terminals_markup(
    sessions: list[TerminalSession],
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page: int = 0,
    total_pages: int = 1,
    has_more: bool = False,
):
    rows = []
    # Per-session buttons follow the page, not the whole list: the keyboard can
    # never grow past one page regardless of how many terminals were opened.
    session_buttons = [
        inline(f"🖥 {_short(session.id)}", _term.view(ws_id, session.id), intent=Intent.PRIMARY)
        for session in sessions
    ]
    for index in range(0, len(session_buttons), 2):
        rows.append(session_buttons[index : index + 2])
    rows += _pager_rows(page, total_pages, lambda target: _term.list_(ws_id, target), locale)
    # The fixed actions stay at the bottom on every page.
    rows.append([inline(catalog.t("terminal.open", locale), _term.open_terminal(ws_id), intent=Intent.SUCCESS)])
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
    typing: bool = False,
) -> Screen:
    """The live terminal view.

    ``typing`` is set while the sticky input state is active for this session
    (R1/R3): the screen then says the user is typing straight into the terminal
    and carries the one-tap **stop typing** control, so the state never traps
    anyone.
    """
    doc = RichDoc(locale)
    emoji = SESSION_STATUS_EMOJI.get(session.status, "—")
    status = f"{emoji} {session_status_label(session.status, locale)}"
    doc.h(2, catalog.t("terminal.title", locale))
    doc.p(_raw(f"<b>{catalog.t('terminal.status_line', locale)}</b> {status}"))

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

    if typing and not session.status.is_terminal:
        doc.p(italic(catalog.t("terminal.input_active", locale)))

    return Screen(
        doc=doc,
        markup=terminal_view_markup(session, ws_id, locale, typing=typing),
    )


def terminal_view_markup(
    session: TerminalSession, ws_id: WorkspaceId, locale: Locale, *, typing: bool = False
):
    # One exit, not two. The live terminal's exit is **🖥 My terminals** — the
    # list is where a user manages sessions (open another, remove an ended one),
    # and from it the workspace home is the very next tap (its own Back). A second
    # bottom *Back* row duplicated that destination (same reasoning as the
    # workspace home's redundant exit, task 11): it is one tap away, not gone.
    ses_id = session.id
    if session.status.is_terminal:
        # A terminal session never accepts input/signal; offer a new one and a way
        # to drop the bot-side handle from *my terminals* instead.
        rows = [
            [
                inline(catalog.t("terminal.new", locale), _term.open_terminal(ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("terminal.forget", locale), _term.forget(ws_id, ses_id), intent=Intent.PRIMARY),
            ],
            [inline(catalog.t("terminal.my_title", locale), _term.list_(ws_id), intent=Intent.PRIMARY)],
        ]
        return inline_markup(rows)
    if typing:
        # Sticky command mode: the live view keeps every terminal button (R2) and
        # adds the one-tap exit (R3), so the state is always escapable.
        rows = [
            [
                inline(catalog.t("terminal.send_command", locale), _term.cmd(ws_id, ses_id), intent=Intent.PRIMARY),
                inline(catalog.t("terminal.stop_typing", locale), _term.exit_(ws_id, ses_id), intent=Intent.DANGER),
            ],
            [
                inline(catalog.t("terminal.ctrl_c", locale), _term.signal(ws_id, ses_id, "INT"), intent=Intent.PRIMARY),
                inline(catalog.t("terminal.ctrl_d", locale), _term.ctrl_d(ws_id, ses_id), intent=Intent.PRIMARY),
            ],
            [
                inline(catalog.t("terminal.close", locale), _term.close(ws_id, ses_id), intent=Intent.DANGER),
                inline(catalog.t("terminal.my_title", locale), _term.list_(ws_id), intent=Intent.PRIMARY),
            ],
        ]
        return inline_markup(rows)
    rows = [
        [
            inline(catalog.t("terminal.send_command", locale), _term.cmd(ws_id, ses_id), intent=Intent.PRIMARY),
            inline(catalog.t("terminal.ctrl_c", locale), _term.signal(ws_id, ses_id, "INT"), intent=Intent.PRIMARY),
        ],
        [
            inline(catalog.t("terminal.ctrl_d", locale), _term.ctrl_d(ws_id, ses_id), intent=Intent.PRIMARY),
            inline(catalog.t("terminal.close", locale), _term.close(ws_id, ses_id), intent=Intent.DANGER),
        ],
        [inline(catalog.t("terminal.my_title", locale), _term.list_(ws_id), intent=Intent.PRIMARY)],
    ]
    return inline_markup(rows)


# -- command prompt ----------------------------------------------------------
def command_prompt(ws_id: WorkspaceId, ses_id: str, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.command_title", locale))
    doc.p(catalog.t("terminal.command_prompt", locale))
    doc.p(italic(catalog.t("terminal.command_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _term.view(ws_id, ses_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- stale / out-of-date button ----------------------------------------------
def stale_screen(locale: Locale) -> Screen:
    """A ``term:`` callback this process cannot resolve (stale or malformed).

    The full key parts of a terminal callback live in a process-global in-memory
    registry (CB-8); after a restart that registry is empty, so a button drawn by
    the previous process decodes to no key parts. The handler must answer with a
    correctable screen — never an ``IndexError`` and never nothing.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.stale.title", locale))
    doc.p(catalog.t("terminal.stale.body", locale))
    markup = inline_markup(
        [[inline(catalog.t("terminal.stale.reopen", locale), _cb("ws:list"), intent=Intent.PRIMARY)]]
    )
    return Screen(doc=doc, markup=markup)


# -- ended -------------------------------------------------------------------
def ended_screen(session: TerminalSession, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.ended_title", locale))
    doc.p(catalog.t("terminal.ended_body", locale))
    if session.reason:
        doc.p(italic(session_reason_label(session.reason, locale)))
    markup = inline_markup(
        [
            [inline(catalog.t("terminal.new", locale), _term.open_terminal(ws_id), intent=Intent.SUCCESS)],
            [inline(catalog.t("terminal.forget", locale), _term.forget(ws_id, session.id), intent=Intent.PRIMARY)],
            [inline(catalog.t("terminal.my_title", locale), _term.list_(ws_id), intent=Intent.PRIMARY)],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


__all__ = [
    "PAGE_SIZE",
    "command_prompt",
    "ended_screen",
    "my_terminals",
    "my_terminals_markup",
    "order_sessions",
    "stale_screen",
    "terminal_view",
    "terminal_view_markup",
    "timeout_notice",
]
