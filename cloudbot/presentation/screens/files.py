"""File browser, file view, and file action screens.

The browser is a **tree**, not a table: the open folder is named in the header
and its immediate children are drawn as indented, branch-glyphed lines, each with
its own button **inside the message body** (Bot API 10.3 blocks). The old
three-column table is gone — in a right-to-left message its numbers drifted away
from their headers, which is exactly why the house rule forbids it.

The tree is deliberately **one level deep**: it shows the current directory and
its immediate children. A recursive walk of a workspace can be thousands of
entries and would neither render nor be useful; deeper levels are reached by
tapping the folder (hard rule 6 in TASK file-tree).
"""

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
from cloudbot.presentation.pagination import total_pages_for as _total_pages_for
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic, neutralize_bidi
from cloudbot.presentation.screens.screen import Screen

PAGE_SIZE = _PAGE_SIZE

#: A tree line's own name is elided so a pathological filename cannot push the
#: size off the line. The **callback keeps the full path** — only the visible
#: label is shortened.
MAX_LABEL_CHARS = 24

#: Two buttons per row, as TASK file-tree draws it.
BUTTON_COLUMNS = 2


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


def _ordered(entries: tuple[FileEntry, ...]) -> list[FileEntry]:
    """Folders first, then files, each group sorted by name.

    A user looking for a folder should not have to scan past twenty files; that
    is the whole reason the rule is written down. The engine already sorts this
    way, but the screen owns the guarantee because it owns the layout.
    """
    folders = sorted((e for e in entries if e.type == "dir"), key=lambda e: e.name.casefold())
    files = sorted((e for e in entries if e.type != "dir"), key=lambda e: e.name.casefold())
    return folders + files


def _tree_line(entry: FileEntry, branch: str, locale: Locale) -> str:
    """One tree line: branch glyph, icon, name (folders get ``/``), then size."""
    name = neutralize_bidi(entry.name)
    if entry.type == "dir":
        return f"{branch}{_icon(entry)} {name}/"
    size = format_bytes(entry.size, locale) if entry.size is not None else "—"
    return f"{branch}{_icon(entry)} {name} · {size}"


def _tree_text(entries: list[FileEntry], locale: Locale) -> str:
    lines: list[str] = []
    last = len(entries) - 1
    for index, entry in enumerate(entries):
        branch = "└── " if index == last else "├── "
        lines.append(_tree_line(entry, branch, locale))
    return "\n".join(lines)


def _label(entry: FileEntry) -> str:
    """The button label: the entry's own name, elided when it is very long.

    The label carries the name so a user can tell which row is which — that was
    the complaint about the old keyboard, where four rows of identical buttons
    could not be told apart. Only the label is truncated; the callback is not.
    """
    name = entry.name
    if len(name) > MAX_LABEL_CHARS:
        name = name[: MAX_LABEL_CHARS - 1] + "…"
    if entry.type == "dir":
        name += "/"
    return f"{_icon(entry)} {name}"


def entry_action(entry: FileEntry, ws_id: WorkspaceId) -> str:
    """The callback an entry's own button emits: ``file:cd`` / ``file:view``.

    A folder navigates into itself; a file opens its view. These are exactly the
    actions the old keyboard emitted, so the routing layer is untouched.
    """
    verb = "file:cd" if entry.type == "dir" else "file:view"
    return _cb(verb, ws_id, entry.path)


def _entry_buttons(entries: list[FileEntry], ws_id: WorkspaceId) -> list[list[tuple[str, str, str | None]]]:
    """One button per entry, two to a row, each reaching that entry's action."""
    rows: list[list[tuple[str, str, str | None]]] = []
    row: list[tuple[str, str, str | None]] = []
    for entry in entries:
        row.append((_label(entry), entry_action(entry, ws_id), "primary"))
        if len(row) == BUTTON_COLUMNS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def file_browser(
    page: FilePage,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page_index: int = 0,
) -> Screen:
    """The open folder as a tree, with each entry's button in the body beside it.

    Builds entirely in **blocks**: ``InputRichMessage`` allows either html or
    blocks, never both, and an in-body callback button exists only in the blocks
    form (Telegram refuses ``<tg-button callback_data=...>`` in html). Mixing the
    two would silently drop content, so ``RichDoc.build()`` raises on it.
    """
    doc = RichDoc(locale)
    rule = catalog.t("ws.head.rule", locale)
    title = catalog.t("file.title", locale).replace("📁 ", "")

    doc.paragraph(rule)
    doc.heading(f"✦ {title} ⟸ {neutralize_bidi(page.path)}", size=2)
    doc.paragraph(rule)

    entries = _ordered(page.entries)
    if not entries:
        doc.paragraph(catalog.t("file.empty", locale))
    else:
        # A preformatted block keeps the branch glyphs on their own lines. Plain
        # paragraph text is RichText, not html, so nothing here is escaped markup.
        doc.pre_block(_tree_text(entries, locale))
        for row in _entry_buttons(entries, ws_id):
            doc.buttons(row)

        total_pages = _total_pages_for(page.total)
        nav_row: list[tuple[str, str, str | None]] = []
        if page_index > 0:
            nav_row.append(
                (
                    catalog.t("common.previous", locale),
                    _cb("file:ls", ws_id, page_index - 1),
                    "primary",
                )
            )
        if page_index + 1 < total_pages:
            nav_row.append(
                (
                    catalog.t("common.next", locale),
                    _cb("file:ls", ws_id, page_index + 1),
                    "primary",
                )
            )
        if nav_row:
            doc.buttons(nav_row, align="center")
        doc.footer(
            catalog.t(
                "file.footer",
                locale,
                count=_num(page.total, locale),
                page=_num(page_index + 1, locale),
                total=_num(total_pages, locale),
            )
        )
    if page.truncated:
        doc.paragraph(catalog.t("file.truncated", locale))

    return Screen(doc=doc, markup=file_browser_markup(page, ws_id, locale, page_index=page_index))


def file_browser_markup(page: FilePage, ws_id: WorkspaceId, locale: Locale, *, page_index: int = 0):
    """The keyboard: the actions that are not entries.

    The per-entry navigation moved **into the body** (``file_browser``), so this
    keyboard no longer repeats the listing. What remains is the folder's own
    tools and the way out. The parent-folder button is kept near the bottom so it
    is never the first thing a thumb hits.
    """
    path = page.path
    rows: list[list] = []
    rows.append([inline(catalog.t("file.upload", locale), _cb("file:upload", ws_id, path), intent=Intent.SUCCESS)])
    rows.append([inline(catalog.t("file.new_folder", locale), _cb("file:mkdir", ws_id, path), intent=Intent.PRIMARY)])
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
def _breadcrumbs(path: str) -> list[tuple[str, str]]:
    """Ancestor segments only, each with its real path.

    The current (last) segment is not returned: it is the screen already shown
    (R1 — omitted, not muted). Each ancestor keeps its directory so the crumb
    navigates to *that* listing rather than back to the root.
    """
    parts = [p for p in path.split("/") if p]
    crumbs: list[tuple[str, str]] = []
    acc = ""
    for p in parts[:-1]:
        acc += "/" + p
        crumbs.append((p, acc))
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
