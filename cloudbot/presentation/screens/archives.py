"""Archive screens (Phase 2g): upload+extract, in-place extract, export.

The three entry points are **upload and extract** (accept a Telegram document;
the content, not the extension, is the authority), **extract in place** (pick a
file already in the workspace), and **export a folder** (choose a directory, get
a ZIP). Each offers a **dry-run preview** before anything is written.

Export is always ZIP; **RAR is never offered as a create target**. General file
delivery to Telegram documents remains CB-14 (Phase 3); only the exported ZIP and
the uploaded archive are moved here.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import ArchiveResult, WorkspaceId
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

#: Show the per-entry list only while it stays readable.
MAX_ENTRIES_SHOWN = 20


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


# -- menu --------------------------------------------------------------------
def archive_menu(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.title", locale))
    doc.p(catalog.t("archive.intro", locale))
    doc.p(italic(catalog.t("archive.content_authority", locale)))
    doc.p(italic(catalog.t("archive.export_zips_only", locale)))
    markup = inline_markup(
        [
            [inline(catalog.t("archive.upload", locale), _cb("arc:upload", ws_id), intent=Intent.SUCCESS)],
            [
                inline(catalog.t("archive.extract_in_place", locale), _cb("arc:extract_prompt", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("archive.export", locale), _cb("arc:export_prompt", ws_id), intent=Intent.PRIMARY),
            ],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


# -- upload-and-extract prompt ----------------------------------------------
def upload_prompt(ws_id: WorkspaceId, max_mb: int, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.upload_title", locale))
    doc.p(_raw(catalog.t("archive.upload_prompt", locale)))
    doc.p(italic(catalog.t("archive.upload_max", locale, mb=_num(max_mb, locale))))
    doc.p(italic(catalog.t("archive.content_authority", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def upload_too_large(size: str, limit: str, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.upload_title", locale))
    doc.blockquote(catalog.t("archive.upload_too_large", locale, size=size, limit=limit))
    markup = inline_markup(
        [[inline(catalog.t("common.back", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY)]]
    )
    return Screen(doc=doc, markup=markup)


# -- extract-in-place prompt -------------------------------------------------
def extract_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.extract_title", locale))
    doc.p(_raw(catalog.t("archive.extract_prompt", locale)))
    doc.p(italic(catalog.t("archive.extract_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- export prompt -----------------------------------------------------------
def export_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.export_title", locale))
    doc.p(_raw(catalog.t("archive.export_prompt", locale)))
    doc.p(italic(catalog.t("archive.export_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- dry-run preview / result ------------------------------------------------
def dry_run_prompt(
    result: ArchiveResult,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    action: str,
    target: str,
) -> Screen:
    """Preview a dry run and offer to apply it for real."""
    doc = _result_doc(result, ws_id, locale, heading="archive.dry_run_title")
    doc.p(italic(catalog.t("archive.dry_run_nothing_written", locale)))
    confirm = _cb("arc:apply", ws_id, action, target)
    markup = inline_markup(
        [
            [
                inline(catalog.t("archive.apply", locale), confirm, intent=Intent.SUCCESS),
                inline(catalog.t("common.cancel", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def archive_result_screen(
    result: ArchiveResult,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page: int = 0,
) -> Screen:
    page_entries, total_pages, _has_more = window(list(result.entries), page)
    page = max(0, min(page, total_pages - 1))
    doc = _result_doc(
        result,
        ws_id,
        locale,
        heading="archive.result_title",
        page_entries=page_entries,
        page=page,
        total_pages=total_pages,
    )
    rows = [
        [
            inline(catalog.t("archive.title", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY),
            inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
        ]
    ]
    rows += _pager_rows(
        page, total_pages, lambda target: _cb("arc:result", ws_id, target), locale
    )
    return Screen(doc=doc, markup=inline_markup(rows))


def export_done_screen(
    filename: str, size_bytes: int, ws_id: WorkspaceId, locale: Locale
) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("archive.export_done_title", locale))
    doc.p(
        _raw(
            catalog.t(
                "archive.export_done_body",
                locale,
                name=esc(filename),
                size=esc(format_bytes(size_bytes, locale)),
            )
        )
    )
    markup = inline_markup(
        [[inline(catalog.t("archive.title", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY)]]
    )
    return Screen(doc=doc, markup=markup)


def _result_doc(
    result: ArchiveResult,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    heading: str,
    page_entries: list | None = None,
    page: int = 0,
    total_pages: int = 1,
) -> RichDoc:
    """The shared result body.

    ``page_entries`` is supplied by the paginated result screen. The dry-run
    preview passes ``None`` and keeps its old behaviour (every entry while it
    still fits the readable cap, otherwise the omitted-line).
    """
    paginated = page_entries is not None
    doc = RichDoc(locale)
    doc.h(2, catalog.t(heading, locale))
    doc.kv_table(
        [
            (catalog.t("archive.format", locale), _raw(f"<code>{esc(result.format)}</code>")),
            (catalog.t("archive.destination", locale), _raw(f"<code>{esc(result.destination)}</code>")),
            (catalog.t("archive.entries_total", locale), _num(result.entries_total, locale)),
            (catalog.t("archive.entries_extracted", locale), _num(result.entries_extracted, locale)),
            (catalog.t("archive.entries_skipped", locale), _num(result.entries_skipped, locale)),
            (catalog.t("archive.entries_rejected", locale), _num(result.entries_rejected, locale)),
            (catalog.t("archive.bytes_written", locale), esc(format_bytes(result.bytes_written, locale))),
            (catalog.t("archive.dry_run", locale), catalog.t("common.yes" if result.dry_run else "common.no", locale)),
        ]
    )

    # A skipped entry is not a failure; a rejected one is. Refused entries get
    # their own stable sentence, never a raw engine string. On the paginated
    # result screen this list follows the page too, so no bullet list can grow
    # past the window either.
    refused = [
        e for e in (page_entries if paginated else result.entries) if e.rejected
    ]
    if refused:
        doc.p(_raw(f"<b>{catalog.t('archive.rejected_header', locale)}</b>"))
        for entry in refused[:MAX_ENTRIES_SHOWN]:
            reason = _reason_sentence(entry.reason, locale)
            doc.p(_raw(f"• <code>{esc(entry.name)}</code> — {reason}"))

    shown = page_entries if paginated else (
        list(result.entries) if result.entries and len(result.entries) <= MAX_ENTRIES_SHOWN else []
    )
    if shown:
        rows = []
        for entry in shown:
            status = (
                catalog.t("archive.entry.rejected", locale)
                if entry.rejected
                else catalog.t("archive.entry.skipped", locale)
                if entry.skipped
                else catalog.t("archive.entry.extracted", locale)
            )
            rows.append(
                [
                    _raw(f"<code>{esc(entry.path)}</code>"),
                    catalog.t(f"archive.entry_type.{entry.type}", locale)
                    if catalog.has(f"archive.entry_type.{entry.type}", locale)
                    else esc(entry.type),
                    status,
                ]
            )
        doc.table(
            [
                catalog.t("archive.column.path", locale),
                catalog.t("archive.column.type", locale),
                catalog.t("archive.column.status", locale),
            ],
            rows,
        )
        if paginated:
            doc.p(page_footer(page, total_pages, locale))
    else:
        doc.p(italic(catalog.t("archive.entries_omitted", locale, n=_num(len(result.entries), locale))))

    return doc


def _reason_sentence(reason: str, locale: Locale) -> str:
    key = ARCHIVE_REASON_KEYS.get(reason)
    if key is None:
        return catalog.t("archive.refusal.default", locale)
    return catalog.t(key, locale)


#: A stable refusal code decides which sentence a rejected entry gets.
ARCHIVE_REASON_KEYS: dict[str, str] = {
    "archive_entry_exists": "err.archive_entry_exists",
    "archive_too_large": "err.archive_too_large",
    "archive_too_many_entries": "err.archive_too_many_entries",
    "archive_ratio_exceeded": "err.archive_ratio_exceeded",
    "unsupported_archive": "err.unsupported_archive",
    "archive_encrypted": "err.archive_encrypted",
    "archive_multivolume": "err.archive_multivolume",
    "archive_unsafe_entry": "err.archive_unsafe_entry",
    "payload_too_large": "err.payload_too_large",
    "insufficient_storage": "err.insufficient_storage",
    "unprocessable": "err.unprocessable",
}
