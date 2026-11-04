"""Run history screen and the purge-conflict screen."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import RunPage, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_relative, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import RUN_STATUS_EMOJI, run_status_heading, run_status_label
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen

PAGE_SIZE = 10
FILTERS = [
    ("run.filter.all", None),
    ("run.filter.finished", "exited"),
    ("run.filter.failed", "failed"),
    ("run.filter.active", "running"),
]


def _raw(text: str) -> Html:
    return Html(text)


def _th(label: str) -> Html:
    return _raw(f"<th>{label}</th>")


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
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.history_title", locale))
    doc.p(catalog.t("run.history_count", locale, n=_num(page.total, locale)))
    rows = []
    for run in page.runs:
        emoji = RUN_STATUS_EMOJI.get(run.status, "⏳")
        status = f"{emoji} {run_status_label(run.status, locale)}"
        cmd = " ".join(run.argv)[:40]
        when = format_relative(run.started_at or run.ended_at, now, locale)
        rows.append([_short(str(run.id)), status, _raw(f"<code>{_esc(cmd)}</code>"), when])
    if rows:
        doc.table(
            [
                _th(catalog.t("run.history_column.run", locale)),
                _th(catalog.t("run.column.status", locale)),
                _th(catalog.t("run.column.command", locale)),
                _th("When" if locale is Locale.EN else "الوقت"),
            ],
            rows,
        )
    return Screen(
        doc=doc,
        markup=run_history_markup(page, ws_id, locale, active_filter=active_filter, page_index=page_index),
    )


def run_history_markup(
    page: RunPage, ws_id: WorkspaceId, locale: Locale, *, active_filter: str | None, page_index: int = 0
):
    rows = []
    filter_row = []
    for key, value in FILTERS:
        label = catalog.t(key, locale)
        if value == active_filter:
            filter_row.append(
                inline(label, _cb("run:history", ws_id), intent=Intent.PRIMARY, disabled=True)
            )
        else:
            filter_row.append(inline(label, _cb("run:filter", ws_id, value or "all"), intent=Intent.PRIMARY))
    rows.append(filter_row[:2])
    rows.append(filter_row[2:])
    if page.total > (page_index + 1) * PAGE_SIZE:
        rows.append([inline(catalog.t("common.load_more", locale), _cb("run:history", ws_id), intent=Intent.PRIMARY)])
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
    if len(run_id) <= 12:
        return _raw(f"<code>{_esc(run_id)}</code>")
    return _raw(f"<code>{_esc(run_id[:5])}…{_esc(run_id[-2:])}</code>")


def _esc(text: str) -> str:
    from cloudbot.presentation.rich.tags import esc

    return esc(text)
