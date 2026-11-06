"""Run history screen and the purge-conflict screen.

The default row is what a user *scans*: the run's identity, its state, and when.
The command, duration and exit code are not lost — they are one tap away on the
run's own screen (``run:logs``), which the identity button opens.

Paging goes through the one project pager (``presentation.pagination``), so the
history stops being the one list screen that grew without pages.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import RunPage, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_relative, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import RUN_STATUS_EMOJI, run_status_label
from cloudbot.presentation.pagination import (
    PAGE_SIZE as _PAGE_SIZE,
    footer as page_footer,
    pager_rows as _pager_rows,
    total_pages_for,
)
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen

PAGE_SIZE = _PAGE_SIZE
FILTERS = [
    ("run.filter.all", None),
    ("run.filter.finished", "exited"),
    ("run.filter.failed", "failed"),
    ("run.filter.active", "running"),
]

def _raw(text: str) -> Html:
    return Html(text)

def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)

def run_history(
    page: RunPage,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    active_filter: str | None = None,
    now=None,
    page_index: int = 0,
) -> Screen:
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc)
    total_pages = total_pages_for(page.total)
    page_index = max(0, min(int(page_index), total_pages - 1))
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.history_title", locale))
    doc.p(catalog.t("run.history_count", locale, n=_num(page.total, locale)))
    rows = []
    for run in page.runs:
        emoji = RUN_STATUS_EMOJI.get(run.status, "⏳")
        status = f"{emoji} {run_status_label(run.status, locale)}"
        when = format_relative(run.started_at or run.ended_at, now, locale)
        rows.append([_short(str(run.id)), status, when])
    if rows:
        # Three columns, not the eight-column markup the old double-wrapped
        # headers produced: identity, state, when. Everything else is on the run.
        doc.table(
            [
                catalog.t("run.history_column.run", locale),
                catalog.t("run.column.status", locale),
                catalog.t("run.history_column.when", locale),
            ],
            rows,
        )
        doc.p(page_footer(page_index, total_pages, locale))
    return Screen(
        doc=doc,
        markup=run_history_markup(
            page,
            ws_id,
            locale,
            active_filter=active_filter,
            page_index=page_index,
            total_pages=total_pages,
        ),
    )

def _page_action(ws_id: WorkspaceId, active_filter: str | None, page: int) -> str:
    """The callback for page ``page`` — the active filter is preserved."""
    if active_filter:
        return _cb("run:filter", ws_id, active_filter, page)
    return _cb("run:history", ws_id, page)

def run_history_markup(
    page: RunPage,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    active_filter: str | None,
    page_index: int = 0,
    total_pages: int = 1,
):
    rows = []
    # Every run opens its own screen, which is where command, duration and exit
    # code live. The topic is identity, so the button is the run's short id.
    run_buttons = [
        inline(f"📜 {_short_plain(str(run.id))}", _cb("run:logs", ws_id, run.id), intent=Intent.PRIMARY)
        for run in page.runs
    ]
    for index in range(0, len(run_buttons), 2):
        rows.append(run_buttons[index : index + 2])

    # R1: the active filter points at the screen you are already on, so it is
    # omitted, never a muted placeholder. The remaining filters go two per row.
    filter_buttons = [
        inline(catalog.t(key, locale), _cb("run:filter", ws_id, value or "all"), intent=Intent.PRIMARY)
        for key, value in FILTERS
        if value != active_filter
    ]
    rows += [filter_buttons[i : i + 2] for i in range(0, len(filter_buttons), 2)]

    rows += _pager_rows(
        page_index,
        total_pages,
        lambda target: _page_action(ws_id, active_filter, target),
        locale,
    )
    rows.append(
        [
            inline(catalog.t("run.purge", locale), _cb("run:purge_confirm", ws_id), intent=Intent.DANGER),
            inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
        ]
    )
    return inline_markup(rows)

def purge_conflict(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.purge_title", locale))
    doc.blockquote(catalog.t("run.purge_conflict", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("run.stop_all", locale), _cb("ws:stop", ws_id), intent=Intent.DANGER),
                inline(catalog.t("common.back", locale), _cb("run:history", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)

def _short(run_id: str) -> Html:
    return _raw(f"<code>{_esc(_short_plain(run_id))}</code>")

def _short_plain(run_id: str) -> str:
    if len(run_id) <= 12:
        return run_id
    return f"{run_id[:5]}…{run_id[-2:]}"

def _esc(text: str) -> str:
    from cloudbot.presentation.rich.tags import esc

    return esc(text)
