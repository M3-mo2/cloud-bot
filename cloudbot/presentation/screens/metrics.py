"""Metrics screen (Phase 2d): one-shot RAM/disk snapshot with honest completeness.

The panel is a **snapshot the user refreshes** (see ``services/metrics.py`` for
why there is no standing SSE connection). ``ram_complete``/``disk_complete`` are
honesty flags: a figure whose flag is false is a lower bound and is rendered as a
provisional value with **no percentage** — never as a confident zero. A workspace
with no live cgroup (``live_cgroups: 0`` and RAM 0) is "nothing running", not an
error, and is shown as an explicit idle state.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceMetrics
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_bytes, localize_digits
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
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def metric_value(
    used: int, limit: int, percent: float, complete: bool, locale: Locale
) -> Html:
    """Render one resource figure, honouring the ``*_complete`` honesty flag.

    When ``complete`` is false the figure is a **lower bound** and the percentage
    is withheld entirely: the value is shown with a provisional marker, because a
    confident "0%" from an incomplete read would be a lie.
    """
    used_text = format_bytes(used, locale)
    if not complete:
        return _raw(
            f"{esc(used_text)} <i>({catalog.t('metrics.incomplete', locale)})</i>"
        )
    limit_text = format_bytes(limit, locale)
    pct = catalog.t("metrics.percent", locale, p=localize_digits(str(round(percent)), locale))
    return _raw(f"{esc(used_text)} / {esc(limit_text)} · {esc(pct)}")


def metrics_screen(
    metrics: WorkspaceMetrics, ws_id: WorkspaceId, locale: Locale, *, page: int = 0
) -> Screen:
    # Four fixed metrics rows fit on one page — but they still go through the one
    # pager so no list screen is the odd one out, and so the row set can grow
    # (a future metric) without ever exceeding a page.
    rows = [
        (catalog.t("metrics.ram", locale), metric_value(
            metrics.ram_used_bytes,
            metrics.ram_limit_bytes,
            metrics.ram_percent,
            metrics.ram_complete,
            locale,
        )),
        (catalog.t("metrics.disk", locale), metric_value(
            metrics.disk_used_bytes,
            metrics.disk_limit_bytes,
            metrics.disk_percent,
            metrics.disk_complete,
            locale,
        )),
        (catalog.t("metrics.live_runs", locale), _num(metrics.live_runs, locale)),
        (catalog.t("metrics.live_cgroups", locale), _num(metrics.live_cgroups, locale)),
    ]
    page_rows, total_pages, _has_more = window(rows, page)
    page = max(0, min(page, total_pages - 1))

    doc = RichDoc(locale)
    doc.h(2, catalog.t("metrics.title", locale))

    doc.kv_table(page_rows)

    if not metrics.ram_complete or not metrics.disk_complete:
        doc.blockquote(catalog.t("metrics.incomplete_note", locale))

    # An idle workspace legitimately reports RAM 0 — that is not a warning.
    if metrics.live_cgroups == 0:
        doc.p(catalog.t("metrics.idle", locale))
        doc.p(italic(catalog.t("metrics.idle_hint", locale)))

    doc.p(
        italic(
            catalog.t(
                "metrics.sampled",
                locale,
                n=_num(metrics.interval_seconds, locale),
            )
        )
    )
    if total_pages >= 1:
        doc.p(page_footer(page, total_pages, locale))

    return Screen(
        doc=doc,
        markup=metrics_markup(ws_id, locale, page=page, total_pages=total_pages),
    )


def metrics_markup(
    ws_id: WorkspaceId, locale: Locale, *, page: int = 0, total_pages: int = 1
):
    rows = [
        [
            inline(catalog.t("metrics.refresh", locale), _cb("met:view", ws_id), intent=Intent.PRIMARY),
            inline(catalog.t("ws.processes", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY),
        ]
    ]
    rows += _pager_rows(
        page, total_pages, lambda target: _cb("met:view", ws_id, target), locale
    )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)
