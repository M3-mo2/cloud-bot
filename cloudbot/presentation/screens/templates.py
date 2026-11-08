"""Starter-template screens: the list, the confirmation, and the honest result.

Three screens, one flow (TASK python-templates):

* **A — the list** (``tpl:list``): one block per template, then a button per
  template and a way back to the workspace.
* **B — the confirmation** (``tpl:pick``): names the template and lists **every**
  path it will create. If any target path already exists the screen says which
  one and the create button is disabled — no template ever silently overwrites a
  user's file.
* **C — the result** (``tpl:confirm`` performs the write): created, refused, or
  failed, each stated honestly (partial state is named, never implied).

The list is static data from :mod:`cloudbot.domain.templates`; no network, no
``/v1/meta`` call, no per-user state is involved in rendering it.
"""

from __future__ import annotations

from typing import Sequence

from cloudbot.core.locale import Locale
from cloudbot.domain import templates as catalog_templates
from cloudbot.domain.templates import Template
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_count, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, bold, italic
from cloudbot.presentation.screens.screen import Screen

__all__ = [
    "confirm_screen",
    "done_created",
    "done_failed",
    "done_refused",
    "list_screen",
]


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def _title(template: Template, locale: Locale) -> str:
    return catalog.t(template.title_key, locale)


def _detail(template: Template, locale: Locale) -> str:
    return catalog.t(template.detail_key, locale)


def _notes(template: Template, locale: Locale) -> str:
    return catalog.t(template.notes_key, locale)


def _ws_of(ws_id: WorkspaceId | str) -> str:
    return str(ws_id)


#: One distinguishing leading emoji per template, for its pick button. Presentation
#: owns the visual; the domain catalog stays prose-free. Distinct on the screen so
#: two buttons never share a leading mark (the house emoji rule).
_PICK_EMOJI: dict[str, str] = {
    "hello": "👋",
    "script-with-args": "📝",
    "module": "📚",
    "flask-api": "🌶️",
    "cli-app": "🧰",
    "data-csv": "📈",
    "async-fetch": "🛰️",
    "pytest-suite": "🧪",
}


def _pick_label(template: Template, locale: Locale) -> str:
    emoji = _PICK_EMOJI.get(template.key, "✦")
    return f"{emoji} {_title(template, locale)}"


# -- A. the list --------------------------------------------------------------
def list_screen(ws_id: WorkspaceId | str, locale: Locale) -> Screen:
    """The template catalog: the house header, then one block per template.

    One block per subject (never a table): a bold title and one sentence, then
    the templates' own buttons two to a row, each landing on the confirmation.
    """
    ws = _ws_of(ws_id)
    doc = RichDoc(locale)
    rule = bold(catalog.t("ws.head.rule", locale))
    header = catalog.t("tpl.list.title", locale)
    count = format_count("count.templates", len(catalog_templates.TEMPLATES), locale)

    doc.p(rule)
    doc.p(_raw(f"<b>✦ {header} ⟸ {count}</b>"))
    doc.p(rule)
    doc.p(catalog.t("tpl.list.intro", locale))

    for template in catalog_templates.TEMPLATES:
        doc.p(bold(f"✦ {_title(template, locale)}"))
        doc.p(_detail(template, locale))

    rows: list[list] = []
    pick_row: list = []
    for template in catalog_templates.TEMPLATES:
        pick_row.append(
            inline(
                _pick_label(template, locale),
                _cb("tpl:pick", template.key, ws),
                intent=Intent.PRIMARY,
            )
        )
        if len(pick_row) == 2:
            rows.append(pick_row)
            pick_row = []
    if pick_row:
        rows.append(pick_row)
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


# -- B. the confirmation ------------------------------------------------------
def confirm_screen(
    template: Template,
    ws_id: WorkspaceId | str,
    locale: Locale,
    *,
    conflicts: Sequence[str] = (),
) -> Screen:
    """Name the template, list every path, and create only when nothing collides.

    ``conflicts`` are the target paths that already exist; a non-empty set means
    nothing will be written, and the create button is disabled rather than
    offering an overwrite this task deliberately does not have.
    """
    ws = _ws_of(ws_id)
    paths = catalog_templates.paths(template)
    doc = RichDoc(locale)

    doc.h(2, f"📦 {_title(template, locale)}")
    doc.p(_detail(template, locale))
    doc.p(italic(_notes(template, locale)))

    doc.p(bold(catalog.t("tpl.confirm.paths_header", locale)))
    doc.ul([_raw(f"<code>{_path(p)}</code>") for p in paths])

    if conflicts:
        doc.blockquote(
            _raw(
                catalog.t(
                    "tpl.confirm.conflict_body",
                    locale,
                    paths=", ".join(f"<code>{_path(p)}</code>" for p in conflicts),
                )
            )
        )
        doc.p(catalog.t("tpl.confirm.nothing_written", locale))

    create = inline(
        catalog.t("tpl.confirm.create", locale),
        _cb("tpl:confirm", template.key, ws),
        intent=Intent.SUCCESS,
        disabled=bool(conflicts),
    )
    back = inline(
        catalog.t("common.back", locale),
        _cb("tpl:list", ws),
        intent=Intent.PRIMARY,
    )
    return Screen(doc=doc, markup=inline_markup([[create, back]]))


# -- C. the result ------------------------------------------------------------
def done_created(
    template: Template, ws_id: WorkspaceId | str, locale: Locale, written: Sequence[str]
) -> Screen:
    ws = _ws_of(ws_id)
    doc = RichDoc(locale)
    doc.h(2, "✅ " + catalog.t("tpl.done.created_title", locale))
    doc.p(bold(_title(template, locale)))
    doc.p(
        _raw(
            catalog.t(
                "tpl.done.created_body",
                locale,
                count=format_count("count.files", len(written), locale),
                root="<code>/</code>",
            )
        )
    )
    doc.ul([_raw(f"<code>{_path(p)}</code>") for p in written])
    doc.p(catalog.t("tpl.done.next", locale))
    rows = [
        [inline(catalog.t("tpl.done.open_files", locale), _cb("file:ls", ws, 0), intent=Intent.SUCCESS)],
        [inline(catalog.t("common.back", locale), _cb("ws:view", ws), intent=Intent.PRIMARY)],
    ]
    return Screen(doc=doc, markup=inline_markup(rows))


def done_refused(
    template: Template,
    ws_id: WorkspaceId | str,
    locale: Locale,
    conflicts: Sequence[str],
) -> Screen:
    ws = _ws_of(ws_id)
    doc = RichDoc(locale)
    doc.h(2, "⛔ " + catalog.t("tpl.done.refused_title", locale))
    doc.p(bold(_title(template, locale)))
    doc.p(
        _raw(
            catalog.t(
                "tpl.done.refused_body",
                locale,
                paths=", ".join(f"<code>{_path(p)}</code>" for p in conflicts),
            )
        )
    )
    doc.blockquote(catalog.t("tpl.confirm.nothing_written", locale))
    rows = [
        [inline(catalog.t("common.back", locale), _cb("tpl:list", ws), intent=Intent.PRIMARY)]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))


def done_failed(
    template: Template,
    ws_id: WorkspaceId | str,
    locale: Locale,
    *,
    failed_path: str,
    error_code: str,
    written: Sequence[str],
    engine_message: str = "",
) -> Screen:
    """The honest stop: which path failed, why, and what landed before it.

    ``engine_message`` is accepted for logging but **never rendered**: AGENTS.md
    rule 6 forbids a raw English engine message reaching a normal user, so the
    reason is the catalog sentence for the engine's *code*. The handler logs the
    raw text where an operator can read it.
    """
    ws = _ws_of(ws_id)
    doc = RichDoc(locale)
    doc.h(2, "⚠️ " + catalog.t("tpl.done.failed_title", locale))
    doc.p(bold(_title(template, locale)))
    doc.p(
        _raw(
            catalog.t(
                "tpl.done.failed_body",
                locale,
                path=f"<code>{_path(failed_path)}</code>",
                reason=_raw(_reason(error_code, locale)),
            )
        )
    )
    if written:
        doc.p(catalog.t("tpl.done.partial_header", locale))
        doc.ul([_raw(f"<code>{_path(p)}</code>") for p in written])
    else:
        doc.blockquote(catalog.t("tpl.confirm.nothing_written", locale))
    rows = [
        [inline(catalog.t("tpl.done.open_files", locale), _cb("file:ls", ws, 0), intent=Intent.PRIMARY)],
        [inline(catalog.t("common.back", locale), _cb("tpl:list", ws), intent=Intent.PRIMARY)],
    ]
    return Screen(doc=doc, markup=inline_markup(rows))


def _reason(error_code: str, locale: Locale) -> str:
    """The localized sentence for the engine's code (never its raw message)."""
    key = f"err.{error_code}"
    if catalog.has(key, locale):
        return catalog.t(key, locale)
    return catalog.t("err.unknown", locale)


def _path(path: str) -> str:
    from cloudbot.presentation.rich.tags import esc

    return esc(path)
