"""Package screens (Phase 2a): installed list, install prompt, install result.

The install result is **honest**: a non-zero ``exit_code`` renders as a failure
screen, and when the workspace has no egress the screen says *why* in human words.
The engine's raw pip output never reaches a normal user; it rides the existing
DEBUG+admin collapsed raw-payload block only.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.python_packages import missing_modules, pip_name_for
from cloudbot.domain.value_objects import PackageList, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_duration, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.pagination import (
    footer as page_footer,
    pager_rows as _pager_rows,
    window,
)
from cloudbot.presentation.rich.components import RawPayloadDetails
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen
from cloudbot.services.packages import InstallOutcome

#: Keep install output bounded when shown; rich text caps at 32768 chars.
MAX_OUTPUT_CHARS = 8000


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


# -- installed list ----------------------------------------------------------
def packages_screen(
    pkg_list: PackageList, ws_id: WorkspaceId, locale: Locale, *, page: int = 0
) -> Screen:
    page_items, total_pages, _has_more = window(list(pkg_list.packages), page)
    page = max(0, min(page, total_pages - 1))
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.title", locale))
    doc.p(catalog.t("package.count", locale, n=_num(pkg_list.count, locale)))

    if not pkg_list.packages:
        doc.p(catalog.t("package.empty", locale))
        doc.p(italic(catalog.t("package.empty_hint", locale)))
    else:
        rows = []
        for package in page_items:
            rows.append(
                [
                    _raw(f"🐍 <code>{esc(package.name)}</code>"),
                    _raw(f"<code>{esc(package.version)}</code>") if package.version else "—",
                ]
            )
        doc.table(
            [
                catalog.t("package.column.name", locale),
                catalog.t("package.column.version", locale),
            ],
            rows,
        )
        doc.p(page_footer(page, total_pages, locale))
    if pkg_list.truncated:
        doc.p(catalog.t("package.truncated", locale))

    doc.div()
    doc.p(_raw(f"<b>{catalog.t('package.shared_header', locale)}</b>"))
    if pkg_list.shared_available:
        doc.p(catalog.t("package.shared_available", locale))
    elif pkg_list.shared_enabled:
        doc.p(catalog.t("package.shared_unavailable", locale))
    else:
        doc.p(catalog.t("package.shared_disabled", locale))
    if pkg_list.private_path:
        doc.p(
            _raw(
                f"<b>{catalog.t('package.private_path_label', locale)}</b> "
                f"<code>{esc(pkg_list.private_path)}</code>"
            )
        )

    return Screen(
        doc=doc,
        markup=packages_markup(
            pkg_list, ws_id, locale, page=page, total_pages=total_pages
        ),
    )


def packages_markup(
    pkg_list: PackageList,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    page: int = 0,
    total_pages: int = 1,
):
    rows: list[list] = [
        [inline(catalog.t("package.install", locale), _cb("pkg:install", ws_id), intent=Intent.SUCCESS)]
    ]
    if pkg_list.packages:
        rows.append(
            [
                inline(catalog.t("package.remove_one", locale), _cb("pkg:rm_prompt", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("package.remove_all", locale), _cb("pkg:rm_all_confirm", ws_id), intent=Intent.DANGER),
            ]
        )
    if len(pkg_list.packages) > total_pages * 1:
        pass
    if len(pkg_list.packages) > total_pages * 1:
        pass
    rows += _pager_rows(
        page, total_pages, lambda target: _cb("pkg:list", ws_id, target), locale
    )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


# -- install prompt / working -------------------------------------------------
def install_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.install_title", locale))
    doc.p(_raw(catalog.t("package.install_prompt", locale)))
    doc.p(italic(catalog.t("package.install_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def installing_screen(ws_id: WorkspaceId, locale: Locale) -> Screen:
    """Shown while a slow install runs, so the user sees progress, not silence."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.install_title", locale))
    doc.p(catalog.t("package.installing", locale))
    markup = inline_markup(
        [[inline(catalog.t("package.install", locale), _cb("pkg:install", ws_id), intent=Intent.MUTED, disabled=True)]]
    )
    return Screen(doc=doc, markup=markup, send_new=True)


# -- missing-module install offer -------------------------------------------
#: The callback prefix a one-tap install carries. The pip name is user input on
#: the way back in (a callback can be forged), so the handler re-validates it.
FIX_PREFIX = "pkg:fix:"


def fix_callback(pip_name: str) -> str:
    """The callback that installs exactly ``pip_name``.

    The shape is ``pkg:fix:<pip-name>``: the name, not a workspace, because the
    tap installs into the workspace the user is inside. The handler re-validates
    the name (a callback is user input) before it reaches ``pip``.
    """
    return f"{FIX_PREFIX}{pip_name}"


def missing_section(
    stderr: str, ws_id: WorkspaceId, locale: Locale
) -> tuple[str, list[list]]:
    """The missing-package offer as **(html, keyboard rows)**, or ``("", [])``.

    One block per missing module (max five, from :func:`missing_modules`):

    * a **known** module gets a header, a line naming its pip package, and an
      install button that says «ثبّت <pip>». When the pip name differs from the
      module the traceback named, an italic line names the module — the user must
      see *why* the button says ``pillow`` when his error said ``PIL``;
    * an **unknown** one gets **no install button**: one line saying the package
      is not in the known list and its pip name may differ, plus the existing
      "type the package name" prompt. We never invent a name and never silently
      try the module name.

    Nothing is installed here; a row exists only for a module the traceback named,
    and a tap on its button is what installs it.
    """
    doc = RichDoc(locale)
    rows: list[list] = []
    for module in missing_modules(stderr):
        pip = pip_name_for(module)
        doc.h(2, _raw(f"<b>{esc(catalog.t('package.fix_title', locale, module=module))}</b>"))
        if pip is not None:
            doc.p(
                _raw(
                    f"<code>{esc(pip)}</code> "
                    f"{esc(catalog.t('package.fix_missing_tail', locale))}"
                )
            )
            if pip.casefold() != module.casefold():
                # Why the button says a different name than the traceback did.
                doc.p(italic(catalog.t("package.fix_from", locale, module=module)))
            rows.append(
                [
                    inline(
                        catalog.t("package.fix_install", locale, pip=pip),
                        fix_callback(pip),
                        intent=Intent.SUCCESS,
                    )
                ]
            )
        else:
            doc.p(
                _raw(
                    f"<code>{esc(module)}</code> "
                    f"{esc(catalog.t('package.fix_unknown_tail', locale))}"
                )
            )
            rows.append(
                [
                    inline(
                        catalog.t("package.fix_unknown_button", locale),
                        _cb("pkg:install", ws_id),
                        intent=Intent.PRIMARY,
                    )
                ]
            )
    return doc.html, rows

# -- install result ----------------------------------------------------------
def install_result_screen(
    outcome: InstallOutcome,
    names: str,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    mode: RunMode = RunMode.PRODUCTION,
    is_admin: bool = False,
    retry_attempt: str | None = None,
) -> Screen:
    result = outcome.result
    doc = RichDoc(locale)

    if result.ok:
        doc.h(2, catalog.t("package.result_ok_title", locale))
        doc.p(_raw(catalog.t("package.result_ok", locale, names=esc(names))))
    else:
        doc.h(2, catalog.t("package.result_failed_title", locale))
        doc.p(_raw(catalog.t("package.result_failed", locale, code=_num(result.exit_code, locale))))
        if not outcome.allow_net:
            # The honest cause, in human words — never pip's raw text alone.
            doc.p(_raw(f"<b>{catalog.t('package.result_no_net_title', locale)}</b>"))
            doc.p(_raw(catalog.t("package.result_no_net", locale)))
        if result.timed_out:
            doc.p(catalog.t("package.result_timed_out", locale))

    doc.p(italic(catalog.t("package.result_duration", locale, duration=format_duration(result.duration_ms / 1000.0, locale))))
    if result.target:
        doc.p(_raw(f"<b>{catalog.t('package.result_target', locale)}</b> <code>{esc(result.target)}</code>"))
    if result.truncated:
        doc.p(catalog.t("package.result_truncated", locale))

    # The raw engine install output is engine text: DEBUG+admin only, collapsed.
    payload = result.output or result.stdout
    if payload and mode is RunMode.DEBUG and is_admin:
        RawPayloadDetails(payload[:MAX_OUTPUT_CHARS], mode=mode, is_admin=is_admin).render(doc)

    return Screen(
        doc=doc,
        markup=install_result_markup(ws_id, locale, retry_attempt=retry_attempt),
    )

def install_result_markup(
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    retry_attempt: str | None = None,
):
    rows: list[list] = []
    if retry_attempt:
        # The exact command that failed, on one tap. Offered, never auto-run.
        rows.append(
            [
                inline(
                    catalog.t("exec.again_same", locale),
                    _cb("exec:again", ws_id),
                    intent=Intent.SUCCESS,
                )
            ]
        )
    rows.append(
        [
            inline(catalog.t("package.result_retry", locale), _cb("pkg:install", ws_id), intent=Intent.PRIMARY),
            inline(catalog.t("package.title", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY),
        ]
    )
    rows.append([inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return inline_markup(rows)


# -- remove ------------------------------------------------------------------
def remove_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.remove_title", locale))
    doc.p(_raw(catalog.t("package.remove_prompt", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def remove_all_confirm(ws_name: str, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.remove_all_title", locale))
    doc.p(_raw(catalog.t("package.remove_all_body", locale, name=esc(ws_name))))
    doc.blockquote(catalog.t("package.remove_all_warning", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("common.delete_permanently", locale), _cb("pkg:rm_all", ws_id), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)
