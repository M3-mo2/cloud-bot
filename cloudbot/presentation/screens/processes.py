"""Process screens (Phase 2e): the inventory, per-process stop, and its outcomes.

``argv`` is truncated **and escaped** (an argv is attacker-controlled text).
``kind`` is shown; a ``detached`` process carries no source tag and is rendered as
a first-class entry with a one-line explanation — it is reported, never hidden.
Stop is addressed by the stable ``proc_`` id, never a raw pid. The ``gone``
outcome is a **success** message of its own, distinct from ``stopped``.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    ProcessEntry,
    ProcessList,
    ProcessStopResult,
    WorkspaceId,
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
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen

#: argv is attacker-controlled; truncate before it reaches the message.
MAX_ARGV_CHARS = 120


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def truncate_argv(argv: tuple[str, ...], locale: Locale, *, limit: int = MAX_ARGV_CHARS) -> str:
    """A safe, escaped, truncated view of a command line."""
    joined = " ".join(argv) if argv else "—"
    if len(joined) > limit:
        return esc(joined[: limit - 1]) + "…"
    return esc(joined)


def _kind_label(kind: str, locale: Locale) -> str:
    key = {
        "run": "process.kind.run",
        "session": "process.kind.session",
        "detached": "process.kind.detached",
    }.get(kind)
    return catalog.t(key, locale) if key else (kind or "")


def _source_label(entry: ProcessEntry, locale: Locale) -> Html:
    if entry.kind == "detached" or not entry.source_id:
        return catalog.t("process.detached_marker", locale)
    return _raw(f"<code>{esc(entry.source_id)}</code>")


def processes_screen(
    process_list: ProcessList, ws_id: WorkspaceId, locale: Locale, *, page: int = 0
) -> Screen:
    page_items, total_pages, _has_more = window(list(process_list.processes), page)
    doc = RichDoc(locale)
    doc.h(2, catalog.t("process.title", locale))
    doc.p(catalog.t("process.count", locale, n=_num(process_list.count, locale)))

    if process_list.empty:
        doc.p(catalog.t("process.empty", locale))
        doc.p(italic(catalog.t("process.empty_hint", locale)))
    else:
        rows = []
        for entry in page_items:
            rows.append(
                [
                    _raw(f"<code>{truncate_argv(entry.argv, locale)}</code>"),
                    _kind_label(entry.kind, locale),
                    _source_label(entry, locale),
                ]
            )
        doc.table(
            [
                catalog.t("process.column.command", locale),
                catalog.t("process.column.kind", locale),
                catalog.t("process.column.source", locale),
            ],
            rows,
        )
        doc.p(italic(catalog.t("process.detached_note", locale)))
        doc.p(page_footer(page, total_pages, locale))

    return Screen(
        doc=doc,
        markup=processes_markup(
            process_list,
            ws_id,
            locale,
            page=page,
            total_pages=total_pages,
            page_items=page_items,
        ),
    )


def processes_markup(
    process_list: ProcessList,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page: int = 0,
    total_pages: int = 1,
    page_items: list[ProcessEntry] | None = None,
):
    if page_items is None:
        page_items, total_pages, _has_more = window(list(process_list.processes), page)
    rows: list[list] = []
    # Stop buttons follow the page, never the whole inventory.
    for entry in page_items:
        rows.append(
            [
                inline(
                    catalog.t("process.stop", locale, cmd=_short(entry)),
                    _cb("proc:stop_confirm", ws_id, entry.id),
                    intent=Intent.DANGER,
                )
            ]
        )
    rows.append(
        [
            inline(catalog.t("process.refresh", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY),
            inline(catalog.t("metrics.title", locale), _cb("met:view", ws_id), intent=Intent.PRIMARY),
        ]
    )
    rows += _pager_rows(
        page, total_pages, lambda target: _cb("proc:list", ws_id, target), locale
    )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


def _short(entry: ProcessEntry) -> str:
    first = entry.argv[0] if entry.argv else entry.id
    name = first.rsplit("/", 1)[-1] or first
    return name[:24]


def stop_confirm(entry: ProcessEntry, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("process.stop_title", locale))
    doc.p(_raw(catalog.t("process.stop_body", locale, cmd=truncate_argv(entry.argv, locale))))
    doc.p(italic(catalog.t("process.stop_hint", locale)))
    markup = inline_markup(
        [
            [
                inline(catalog.t("process.stop_confirm", locale), _cb("proc:stop", ws_id, entry.id), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def stop_result_screen(
    result: ProcessStopResult, ws_id: WorkspaceId, locale: Locale
) -> Screen:
    """The stop outcome, honest: ``stopped`` and ``gone`` read differently."""
    doc = RichDoc(locale)
    if result.gone:
        doc.h(2, catalog.t("process.gone_title", locale))
        doc.p(catalog.t("process.gone_body", locale))
    else:
        doc.h(2, catalog.t("process.stopped_title", locale))
        doc.p(catalog.t("process.stopped_body", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("process.refresh", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)
