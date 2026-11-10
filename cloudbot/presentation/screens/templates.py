"""Starter-template screens: the list, the confirmation, and the honest result.

Three screens, one flow (TASK python-templates, TASK template-folders, TASK
template-cards):

* **A — the list** (``tpl:list``): one **card per template**, grouped, each with
  its icon + bold title, a one-line ``tpl.<key>.short`` description and its own
  in-body «use» button. TASK template-cards overturned the old assumption that
  the description belonged only to the confirmation: a picker that hides what a
  template is makes the user open twelve confirmations to choose. The list now
  builds in **blocks** because its buttons sit beside each card, not in the
  keyboard below; the keyboard keeps only «back».
* **B — the confirmation** (``tpl:pick``): names the template, explains it, and
  lists every path it will create — each under the template's **own folder**
  (``hello/hello.py``), which is what the user sees on disk. If the folder
  already exists the screen says so and the create button is disabled — no
  template ever silently overwrites a user's work, and two different templates
  can still live side by side.
* **C — the result** (``tpl:confirm`` performs the write): created, refused, or
  failed, each stated honestly (partial state is named, never implied).

The list is static data from :mod:`cloudbot.domain.templates`; no network, no
``/v1/meta`` call, no per-user state is involved in rendering it.
"""

from __future__ import annotations

from typing import Sequence

from aiogram.types import RichTextBold

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


def _icon(template: Template) -> str:
    """The template's own leading mark: an emoji carried by the domain catalog.

    TASK template-catalog-v2 moved the icon onto :class:`Template` itself: it is
    the same mark in both locales, so it is data, not a translated string, and a
    new template cannot forget it.
    """
    return template.icon


def _detail(template: Template, locale: Locale) -> str:
    return catalog.t(template.detail_key, locale)


def _short(template: Template, locale: Locale) -> str:
    """The one-line description the list card prints (``tpl.<key>.short``).

    TASK template-cards: this is a *separate* key from ``detail`` on purpose. The
    detail runs 40-70 characters and embeds paths; the card needs one short line
    that fits beside the title, and it must name a required library because that
    is the fact that changes a choice.
    """
    return catalog.t(template.short_key, locale)


def _notes(template: Template, locale: Locale) -> str:
    return catalog.t(template.notes_key, locale)


def _ws_of(ws_id: WorkspaceId | str) -> str:
    return str(ws_id)


# -- A. the list --------------------------------------------------------------
def list_screen(ws_id: WorkspaceId | str, locale: Locale) -> Screen:
    """The template catalog: one **card per template**, grouped, in the body.

    TASK template-cards: the old list was twelve bare ``icon + title`` lines and
    twelve near-identical keyboard buttons, so a user had to open a confirmation
    (twelve times, if wrong) to learn what a template was. Each card now carries
    the one-line ``short`` description and its own in-body button; the picker can
    be judged in place. Cards are grouped by ``Template.group`` (domain data, not
    a dict in this screen), because twelve unbroken blocks do not read either.

    The whole message builds in **blocks**: an in-body callback button is only
    legal there (see ``document.py``), and ``doc.buttons`` enforces the 64-byte
    callback cap. The keyboard below keeps only «back».
    """
    ws = _ws_of(ws_id)
    doc = RichDoc(locale)
    rule = catalog.t("ws.head.rule", locale)
    header = catalog.t("tpl.list.title", locale)
    count = format_count("count.templates", len(catalog_templates.TEMPLATES), locale)
    pick = catalog.t("tpl.list.pick", locale)

    doc.paragraph(rule)
    doc.heading(f"✦ {header} ⟸ {count}", size=2)
    doc.paragraph(rule)
    # The one fact that matters: each template owns its folder, so nothing a user
    # already wrote is ever overwritten. Kept from the old intro, as one bold line
    # (the sentence *is* the fact, so splitting a class of it would only weaken it).
    doc.paragraph_rich([RichTextBold(text=catalog.t("tpl.list.intro", locale))])

    for group in catalog_templates.GROUPS:
        members = [t for t in catalog_templates.TEMPLATES if t.group == group]
        if not members:
            continue
        doc.heading(catalog.t(f"tpl.group.{group}", locale), size=3)
        for template in members:
            doc.paragraph_rich(
                [
                    RichTextBold(text=f"{_icon(template)} {_title(template, locale)}"),
                    "\n",
                    _short(template, locale),
                ]
            )
            doc.buttons(
                [(pick, _cb("tpl:pick", template.key, ws), "primary")],
                align="left",
            )

    back = inline(catalog.t("common.back", locale), _cb("ws:view", ws), intent=Intent.PRIMARY)
    return Screen(doc=doc, markup=inline_markup([[back]]))


# -- B. the confirmation ------------------------------------------------------
def confirm_screen(
    template: Template,
    ws_id: WorkspaceId | str,
    locale: Locale,
    *,
    conflicts: Sequence[str] = (),
) -> Screen:
    """Name the template, list every path, and create only when nothing collides.

    ``conflicts`` are the target folders that already exist; a non-empty set
    means nothing will be written, and the create button is disabled rather than
    offering an overwrite this task deliberately does not have.
    """
    ws = _ws_of(ws_id)
    paths = catalog_templates.relative_paths(template)
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
                root=f"<code>{_path(catalog_templates.template_root(template.key))}</code>",
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
    """Escape a path; template paths are shown as the user sees them on disk.

    The domain composes workspace-absolute paths (``/hello/hello.py``) for the
    engine; the screen shows the folder-relative form (``hello/hello.py``).
    """
    from cloudbot.presentation.rich.tags import esc

    return esc(path.lstrip("/"))
