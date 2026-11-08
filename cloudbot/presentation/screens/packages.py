"""Package screens (Phase 2a): installed list, install prompt, install result.

The install result is **honest**: a non-zero ``exit_code`` renders as a failure
screen, and when the workspace has no egress the screen says *why* in human words.
The engine's raw pip output never reaches a normal user; it rides the existing
DEBUG+admin collapsed raw-payload block only.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.python_packages import (
    is_installed,
    missing_modules,
    normalize_distribution_name,
    pip_name_for,
)
from cloudbot.domain.value_objects import PackageList, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_count,
    format_duration,
    localize_digits,
)
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
from cloudbot.services.files import MAX_PREVIEW_BYTES, tail_preview
from cloudbot.services.packages import InstallOutcome

#: Keep install output bounded when shown; rich text caps at 32768 chars.
MAX_OUTPUT_CHARS = 8000

#: The offer is built from this many trailing bytes of a command's output (12
#: KiB) — exactly the bound the screens themselves display (``terminal._tail``
#: and ``run.live_logs`` use ``tail_preview``). A failure that scrolled off
#: further up is not the failure the user is looking at, so offering to fix it is
#: noise; the tail is the part still on screen.
OFFER_TAIL_BYTES = MAX_PREVIEW_BYTES

#: The start of a shell prompt line as a real PTY prints it: ``$ cmd``,
#: ``# cmd`` (root), ``>>> expr``, or a ``user@host:~$ cmd`` prefix. Everything
#: after the **last** such line is the newest command's block.
_PROMPT_START = re.compile(
    r"(?m)^(?:[^\s$#\n][^\n$#]{0,79}[$#]|[$#]|>>>)[ \t]"
)


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
    doc.p(_raw(format_count("count.packages", pkg_list.count, locale)))

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
    """Shown while a slow install runs, so the user sees progress, not silence.

    It is **not** ``send_new``: started from a callback it must replace the screen
    the offer was on (the same message), so the offer's buttons do not survive
    under a progress screen. The typed-command path passes ``force_send`` itself.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("package.install_title", locale))
    doc.p(catalog.t("package.installing", locale))
    markup = inline_markup(
        [[inline(catalog.t("package.install", locale), _cb("pkg:install", ws_id), intent=Intent.MUTED, disabled=True)]]
    )
    return Screen(doc=doc, markup=markup)


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


def last_failure(output: str) -> str:
    """The **most recent command's** output, or ``""``.

    A terminal's scrollback is a running log of everything typed; a line that
    failed ten minutes ago is not the failure the user is looking at. Two
    constraints narrow the offer to what is on screen:

    * only the last :data:`OFFER_TAIL_BYTES` (12 KiB) are considered — the same
      bound the screens display (``tail_preview``/``terminal._tail``), so a
      failure that scrolled off further up is not offered;
    * within that tail, only the **newest shell block** counts. A real PTY prints
      a prompt before each command and again when it finishes:

      - text after the last prompt is the command still printing — that block;
      - when the tail ends in a fresh prompt, the finished command is the block
        between the last two prompts (the prompt is the shell saying it is done);
      - a bare prompt with nothing before it is an idle shell: no block, no
        offer.

    *Why the newest block, not the whole tail.* The report was a package installed
    and the same command then run fine, yet the old failure line was still in the
    scrollback and still produced a button. Keying on the newest block means a
    later successful command cannot inherit an earlier failure's offer.
    """
    tail, _ = tail_preview(output or "", OFFER_TAIL_BYTES)
    if not tail.strip():
        return ""
    matches = list(_PROMPT_START.finditer(tail))
    if not matches:
        # No prompt at all (e.g. a run's stderr): the tail is the failure text.
        return tail
    last = matches[-1]
    after = tail[last.end():]
    if after.strip():
        # The command is still printing: its output follows the last prompt.
        return after
    # A fresh prompt closed the newest command: its block is the text between
    # the previous prompt and this one (or everything before it).
    start = matches[-2].end() if len(matches) >= 2 else 0
    return tail[start:last.start()]


def missing_section(
    stderr: str,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    installed: Collection[str] | None = None,
) -> tuple[str, list[list]]:
    """The missing-package offer as **(html, keyboard rows)**, or ``("", [])``.

    ``installed`` is the workspace's package list (normalised pip names), fetched
    by the caller before rendering. A module whose pip name is **already
    installed** is skipped completely — no header, no line, no button: the user
    already solved that one, and an offer to install what is on disk is the
    defect this filter exists to prevent. ``installed=None`` means the list could
    not be fetched; the screen then degrades to the old behaviour and offers
    everything (never a silent no-op).

    The offer is answered by the **newest** command's output, not the whole
    scrollback: :func:`last_failure` bounds it to the displayed tail and to the
    block after the last prompt, and returns nothing when the shell is idle at a
    prompt. A successful last command therefore produces no offer at all.

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
    seen: set[str] = set()
    seen_unknown: set[str] = set()
    unknown_button_added = False
    for module in missing_modules(last_failure(stderr)):
        pip = pip_name_for(module)
        if pip is not None:
            key = normalize_distribution_name(pip)
            if key in seen:
                # Two import names for one distributable (e.g. a project whose
                # module and package differ): one offer, not two buttons.
                continue
            if is_installed(pip, installed):
                # Already on disk: no header, no line, no button.
                continue
            seen.add(key)
        else:
            key = module.casefold()
            if key in seen_unknown:
                # The same unknown module twice: one block, not two.
                continue
            seen_unknown.add(key)
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
            # The control opens the same "type a package name" prompt whatever
            # unknown module preceded it, so it is attached **once** even when a
            # tail names two unknown modules: a repeated callback is a duplicate
            # destination the keyboard guard rejects.
            if not unknown_button_added:
                rows.append(
                    [
                        inline(
                            catalog.t("package.fix_unknown_button", locale),
                            _cb("pkg:install", ws_id),
                            intent=Intent.PRIMARY,
                        )
                    ]
                )
                unknown_button_added = True
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
    has_origin: bool = True,
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
        # A failed install must say *why* on the screen, and the engine's own
        # output tail is the reason. It is bounded and escaped; it is pip's
        # message, not JSON or an engine code, and it is the only text that can
        # tell the user what to change (a version, a name, egress).
        reason_tail, _ = tail_preview(result.output or result.stdout or "", MAX_OUTPUT_CHARS)
        if reason_tail.strip():
            doc.p(_raw(f"<b>{catalog.t('package.result_reason', locale)}</b>"))
            doc.pre(reason_tail, language="text")

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
        markup=install_result_markup(
            ws_id,
            locale,
            retry_attempt=retry_attempt,
            has_origin=has_origin,
            names=names if result.ok else None,
        ),
    )


def install_result_markup(
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    retry_attempt: str | None = None,
    has_origin: bool = True,
    names: str | None = None,
):
    """The install result's buttons: **back to where you were**, then retry.

    The origin is recorded by the surface that showed the offer, never guessed
    here. When it is known, the first (primary, success) row returns to it; when
    it is not (a restart, a cold callback) that row is omitted rather than
    pointing somewhere wrong, and ``pkg:list`` is the single fallback exit.

    ``pkg:install`` is deliberately absent: it opens the *type a package name*
    prompt, an input state that would sit on top of this result screen. It is not
    a retry and must never be a row here. ``pkg:list`` is absent too unless it is
    the fallback — when an origin exists, "back" already covers it.
    """
    rows: list[list] = []
    if has_origin:
        label = catalog.t("package.result_back", locale, names=names) if names else catalog.t("package.result_back_plain", locale)
        rows.append(
            [inline(label, _cb("origin:back"), intent=Intent.SUCCESS)]
        )
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
    if not has_origin:
        # No remembered origin: one predictable exit, never a dead end.
        rows.append(
            [inline(catalog.t("package.title", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY)]
        )
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
