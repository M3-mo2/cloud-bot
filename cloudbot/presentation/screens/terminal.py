"""Terminal screens (Phase 1.5): the live terminal view, ended, stale.

A workspace's **🖥 Terminals** opens a terminal directly (open-or-reuse): there is
no archive screen and no pager here. Screens are pure functions of domain state +
locale; no I/O. Every string comes from a catalog key. The timeout notice is
derived from the engine's session configuration (``/v1/meta``), never hardcoded.
"""

from __future__ import annotations

from collections.abc import Collection

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.value_objects import SessionStatus, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_duration
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
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens import terminal_callbacks as _term
from cloudbot.presentation.screens.screen import Screen

#: Keep the last ~12 KB of PTY output (rich text cap is 32768 chars).
MAX_OUTPUT_BYTES = 12 * 1024


def _raw(text: str) -> Html:
    return Html(text)


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
    installed: Collection[str] | None = None,
) -> Screen:
    """The live terminal view.

    ``typing`` is set while the sticky input state is active for this session
    (R1/R3): the screen then says the user is typing straight into the terminal
    and carries the one-tap **stop typing** control, so the state never traps
    anyone.

    ``installed`` is the workspace's package list (normalised pip names) — the
    offer skips a module that is already on disk instead of re-offering it from
    old scrollback. ``None`` means the list could not be fetched and the screen
    degrades to offering everything.
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

    # A package that is missing is the single most common reason a first command
    # fails in a fresh workspace, and the terminal is where it surfaces. The offer
    # is built from the session's own output — bounded to the block the user is
    # looking at and filtered by what is already installed, so a command that
    # succeeded (or whose package is on disk) grows no button at all.
    from cloudbot.presentation.screens.packages import missing_section

    offer_html, offer_rows = missing_section(
        output or "", ws_id, locale, installed=installed
    )
    if offer_html:
        # ``missing_section`` is the screen that owns this wording and this button;
        # calling it keeps one implementation instead of a terminal-only copy.
        doc.raw(offer_html)

    return Screen(
        doc=doc,
        markup=terminal_view_markup(
            session, ws_id, locale, typing=typing, extra_rows=offer_rows
        ),
    )


def terminal_view_markup(
    session: TerminalSession,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    typing: bool = False,
    extra_rows: list[list] | None = None,
):
    """The live terminal's buttons — exactly what a terminal needs, nothing else.

    A live session carries **one** destructive verb, ``⏹ Stop terminal``: it kills
    the session on the engine and returns to the workspace home. There is no second
    "Close" next to it (two names for one effect is how a hand taps the wrong one),
    and ``Ctrl-D`` is gone from the view: an EOF that ends the shell looks identical
    to the stop button while doing something else, and he did not ask for it.

    ``Ctrl-C`` stays — it interrupts the running process without ending the shell.

    ``extra_rows`` carries the missing-package offer built by
    :func:`cloudbot.presentation.screens.packages.missing_section`; it goes above
    the controls so a user who came here to install something finds it without
    scrolling past navigation, and it is never the only way out.
    """
    ses_id = session.id
    if session.status.is_terminal:
        # A dead session accepts neither input nor a signal: offer a fresh one,
        # the workspace home, and the hygiene action that drops this handle.
        rows = [
            [
                inline(catalog.t("terminal.new", locale), _term.new_terminal(ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("terminal.forget", locale), _term.forget(ws_id, ses_id), intent=Intent.PRIMARY),
            ],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
        if extra_rows:
            rows = [*extra_rows, *rows]
        return inline_markup(rows)

    rows: list[list] = [
        [
            inline(catalog.t("terminal.send_command", locale), _term.cmd(ws_id, ses_id), intent=Intent.PRIMARY),
            inline(catalog.t("terminal.ctrl_c", locale), _term.signal(ws_id, ses_id, "INT"), intent=Intent.PRIMARY),
        ],
    ]
    if typing:
        # Leaving command mode is not destructive; it is a mode exit, so it is
        # primary (and reads with a hand, not a stop square, so it cannot be
        # mistaken for the stop button on the row below).
        rows.append(
            [inline(catalog.t("terminal.stop_typing", locale), _term.exit_(ws_id, ses_id), intent=Intent.PRIMARY)]
        )
    # *Stop* kills the session: destructive, so it gets its own row — never beside
    # a button a hand taps by habit (MOHAMED.md).
    rows.append(
        [inline(catalog.t("terminal.stop", locale), _term.close(ws_id, ses_id), intent=Intent.DANGER)]
    )
    if extra_rows:
        rows = [*extra_rows, *rows]
    return inline_markup(rows)


# -- capacity (too_many_sessions) -------------------------------------------
def cap_screen(
    sessions: list[TerminalSession], ws_id: WorkspaceId, locale: Locale
) -> Screen:
    """The documented cap screen: what is open, and how to free a slot.

    The engine (and the service's pre-check) refuses a new session at
    ``sessions.max_per_workspace``. This screen answers with the **one** thing the
    user can act on: the live sessions, each with the same stop control the live
    view carries, and the workspace home. It never shows the ended archive this
    task deleted — only a session that is running can be stopped to make room.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("terminal.cap.title", locale))
    doc.p(catalog.t("terminal.too_many", locale))
    rows: list[list] = []
    for session in sessions:
        rows.append(
            [
                inline(
                    catalog.t("terminal.stop", locale),
                    _term.close(ws_id, session.id),
                    intent=Intent.DANGER,
                )
            ]
        )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


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
            [inline(catalog.t("terminal.new", locale), _term.new_terminal(ws_id), intent=Intent.SUCCESS)],
            [inline(catalog.t("terminal.forget", locale), _term.forget(ws_id, session.id), intent=Intent.PRIMARY)],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


__all__ = [
    "cap_screen",
    "command_prompt",
    "ended_screen",
    "stale_screen",
    "terminal_view",
    "terminal_view_markup",
    "timeout_notice",
]
