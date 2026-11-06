"""File browser, file view, and file action screens."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import FileEntry
from cloudbot.domain.value_objects import FileContent, FilePage, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_bytes, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.pagination import PAGE_SIZE as _PAGE_SIZE
from cloudbot.presentation.pagination import footer as _footer
from cloudbot.presentation.pagination import pager_rows as _pager_rows
from cloudbot.presentation.pagination import total_pages_for as _total_pages_for
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen

PAGE_SIZE = _PAGE_SIZE


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def _icon(entry: FileEntry) -> str:
    if entry.type == "dir":
        return "📁"
    if entry.runnable and "python" in entry.runnable:
        return "🐍"
    if entry.runnable:
        return "⚙️"
    return "📄"


def _type_label(entry: FileEntry, locale: Locale) -> str:
    if entry.type == "dir":
        return catalog.t("file.type.folder", locale)
    if entry.type == "symlink":
        return catalog.t("file.type.symlink", locale)
    if entry.runnable:
        return catalog.t("file.runnable", locale, lang=entry.runnable)
    return catalog.t("file.type.file", locale)


def file_browser(
    page: FilePage,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page_index: int = 0,
) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.title", locale))
    doc.p(_raw(f"<code>{page.path}</code>"))
    doc.div()

    if not page.entries:
        doc.p(catalog.t("file.empty", locale))
    else:
        rows = []
        for entry in page.entries:
            size = format_bytes(entry.size, locale) if entry.size is not None else "—"
            rows.append(
                [
                    _raw(f"{_icon(entry)} <code>{_esc(entry.name)}</code>"),
                    _type_label(entry, locale),
                    size,
                ]
            )
        doc.table(
            [
                catalog.t("file.column.name", locale),
                catalog.t("file.column.type", locale),
                catalog.t("file.column.size", locale),
            ],
            rows,
        )
        total_pages = _total_pages_for(page.total)
        doc.p(
            _footer(
                page_index,
                total_pages,
                locale,
                key="file.footer",
                count=_num(page.total, locale),
            )
        )
    if page.truncated:
        doc.p(catalog.t("file.truncated", locale))

    return Screen(doc=doc, markup=file_browser_markup(page, ws_id, locale, page_index=page_index))


def file_browser_markup(page: FilePage, ws_id: WorkspaceId, locale: Locale, *, page_index: int = 0):
    path = page.path
    rows: list[list] = []

    # Breadcrumbs (current segment disabled).
    crumbs = _breadcrumbs(path)
    crumb_row = [inline(catalog.t("file.home", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY)]
    for i, (label, target) in enumerate(crumbs):
        if i == len(crumbs) - 1:
            crumb_row.append(inline(label, _cb("file:ls", ws_id, target), intent=Intent.PRIMARY, disabled=True))
        else:
            crumb_row.append(inline(label, _cb("file:ls", ws_id, target), intent=Intent.PRIMARY))
    rows.append(crumb_row)

    for entry in page.entries:
        if entry.type == "dir":
            rows.append(
                [inline(f"📁 {entry.name}", _cb("file:cd", ws_id, entry.path), intent=Intent.PRIMARY)]
            )
        else:
            rows.append(
                [inline(f"📄 {entry.name}", _cb("file:view", ws_id, entry.path), intent=Intent.PRIMARY)]
            )

    rows.append([inline(catalog.t("file.upload", locale), _cb("file:upload", ws_id, path), intent=Intent.SUCCESS)])
    rows.append([inline(catalog.t("file.new_folder", locale), _cb("file:mkdir", ws_id, path), intent=Intent.PRIMARY)])
    rows += _pager_rows(
        page_index,
        _total_pages_for(page.total),
        lambda target: _cb("file:ls", ws_id, target),
        locale,
    )
    if path != "/":
        rows.append([inline(catalog.t("file.up", locale), _cb("file:cd", ws_id, _parent(path)), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("file.workspace", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


def file_view(
    content: FileContent,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    name: str | None = None,
) -> Screen:
    display_name = name or content.path.rstrip("/").rsplit("/", 1)[-1]
    doc = RichDoc(locale)
    icon = "🐍" if content.content_type.endswith("python") else ("📦" if content.binary else "📄")
    doc.h(2, f"{icon} {display_name}")

    if content.truncated and not content.binary:
        doc.blockquote(catalog.t("file.view.truncated", locale))

    rows = [
        [_th(catalog.t("file.view.size", locale)), format_bytes(content.total_size or content.size, locale)],
        [_th(catalog.t("file.view.type", locale)), content.content_type or "text/plain"],
    ]
    if content.content_type.endswith("python"):
        rows.append([_th(catalog.t("file.view.runnable", locale)), "python3"])
    doc.table(None, rows)

    if content.binary:
        doc.blockquote(catalog.t("file.view.binary", locale))
    else:
        lang = "python" if "python" in content.content_type else "text"
        doc.pre(content.content, language=lang)
        sha = (content.sha256 or "")[:10]
        if content.start_line and content.end_line and content.total_lines:
            doc.p(
                italic(
                    catalog.t(
                        "file.view.footer",
                        locale,
                        sha=sha,
                        start=_num(content.start_line, locale),
                        end=_num(content.end_line, locale),
                        total=_num(content.total_lines, locale),
                    )
                )
            )
        elif sha:
            doc.p(italic(catalog.t("file.view.footer_nolines", locale, sha=sha)))

    return Screen(doc=doc, markup=file_view_markup(content, ws_id, locale, display_name))


def file_view_markup(content: FileContent, ws_id: WorkspaceId, locale: Locale, name: str):
    rows: list[list] = []
    top: list = []
    if content.content_type.endswith("python") or (content.content_type and not content.binary):
        if content.content_type.endswith("python"):
            top.append(inline(catalog.t("file.view.run", locale), _cb("run:file", ws_id, content.path), intent=Intent.SUCCESS))
    top.append(inline(catalog.t("file.view.rename", locale), _cb("file:rename", ws_id, content.path), intent=Intent.PRIMARY))
    top.append(inline(catalog.t("file.view.delete", locale), _cb("file:delete_confirm", ws_id, content.path), intent=Intent.DANGER))
    rows.append(top)
    if content.truncated and not content.binary:
        rows.append([inline(catalog.t("file.view.show_more", locale), _cb("file:more", ws_id, content.path), intent=Intent.PRIMARY)])
    rows.append(
        [
            inline(catalog.t("file.folder", locale), _cb("file:cd", ws_id, _parent(content.path)), intent=Intent.PRIMARY),
            inline(catalog.t("file.workspace", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
        ]
    )
    return inline_markup(rows)


# -- file action prompts -----------------------------------------------------
def mkdir_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.mkdir.title", locale))
    doc.p(_raw(catalog.t("file.mkdir.prompt", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def rename_prompt(path: str, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.rename.title", locale))
    doc.p(_raw(catalog.t("file.rename.current", locale, path=_esc(path))))
    doc.p(_raw(catalog.t("file.rename.prompt", locale)))
    markup = inline_markup(
        [
            [
                inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY),
                inline(catalog.t("common.same_folder", locale), _cb("file:rename_same", ws_id, path), intent=Intent.PRIMARY),
            ]
        ],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def delete_confirm(path: str, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.delete.title", locale))
    doc.p(_raw(catalog.t("file.delete.body", locale, path=_esc(path))))
    doc.blockquote(catalog.t("file.delete.warning", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("common.delete", locale), _cb("file:delete", ws_id, path), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("file:cd", ws_id, _parent(path)), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def upload_prompt(path: str, max_mb: int, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.upload.title", locale))
    doc.p(_raw(catalog.t("file.upload.prompt", locale, path=_esc(path))))
    doc.p(italic(catalog.t("file.upload.max", locale, mb=_num(max_mb, locale))))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def upload_too_large(path: str, size: str, limit: str, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.upload.title", locale))
    doc.blockquote(catalog.t("file.upload.too_large", locale, size=size, limit=limit))
    markup = inline_markup(
        [[inline(catalog.t("common.back", locale), _cb("file:cd", ws_id, path), intent=Intent.PRIMARY)]]
    )
    return Screen(doc=doc, markup=markup)


def upload_progress(path: str, name: str, done: str, total: str, percent: float, locale: Locale) -> Screen:
    from cloudbot.presentation.rich.components import usage_bar

    doc = RichDoc(locale)
    doc.h(2, catalog.t("file.upload.uploading", locale))
    doc.p(_raw(catalog.t("file.upload.progress", locale, name=_esc(name), done=done, total=total)))
    doc.pre(usage_bar(percent), language="text")
    return Screen(doc=doc)


# -- breadcrumbs / path helpers ---------------------------------------------
def _breadcrumbs(path: str) -> list[tuple[str, int]]:
    parts = [p for p in path.split("/") if p]
    crumbs: list[tuple[str, int]] = []
    acc = ""
    for p in parts:
        acc += "/" + p
        crumbs.append((p, 0))
    return crumbs


def _parent(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
    return parent


import base64 as _base64


def _b64(text: str) -> str:
    return _base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(text: str) -> str:
    padding = "=" * (-len(text) % 4)
    return _base64.urlsafe_b64decode((text + padding).encode("ascii")).decode("utf-8")


def _esc(text: str) -> str:
    from cloudbot.presentation.rich.tags import esc

    return esc(text)


def _th(label: str) -> Html:
    return _raw(f"<th>{label}</th>")
