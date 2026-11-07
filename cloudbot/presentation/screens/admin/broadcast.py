"""Admin section: «📢 الإذاعة» — a full broadcast console.

The primary path is **copy-message**: the admin composes in his own Telegram and
forwards the message; every recipient receives Telegram's own copy of it, so his
formatting cannot drift. The screens here never re-render his content — the
preview screen carries the *same* ``(chat_id, message_id)`` the sender copies.

The panel screens are pure functions of ``BroadcastView`` and small explicit
arguments. Nothing here does I/O.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.broadcast import (
    AudienceKey,
    BroadcastResult,
    BroadcastRun,
    BroadcastSource,
    BroadcastView,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import blockquote, code, fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

_RULE_KEY = "admin.bc.rule"

def _num(n: int, locale) -> str:
    return localize_digits(str(n), locale)

def _rule(locale) -> str:
    return catalog.t(_RULE_KEY, locale)

def _audience_label(key: AudienceKey, locale) -> str:
    return catalog.t(f"admin.bc.audience.{key}", locale)

def _kind_label(kind: str, locale) -> str:
    return catalog.t(f"admin.bc.received_{kind}", locale)

def _header(doc: RichDoc, locale, fact: str) -> None:
    from cloudbot.presentation.screens.admin._helpers import subject

    doc.p(_rule(locale))
    doc.h(2, f"✦ {subject(locale, 'admin.bc.title')} ⟸ {fact}")
    doc.p(_rule(locale))

def _back_row(locale):
    return [inline(catalog.t("admin.back", locale), admin_cb.section("broadcast"), intent=Intent.PRIMARY)]

# -- 1. the console (compose + history) --------------------------------------
def broadcast_screen(view: AdminView) -> Screen:
    """The section landing screen: compose, the open run, the history shortcut."""
    section = view.broadcast
    locale = view.locale
    doc = RichDoc(locale)
    _header(
        doc,
        locale,
        catalog.t("admin.bc.history_count", locale, n=_num(len(section.history) if section else 0, locale)),
    )
    doc.p(catalog.t("admin.bc.intro", locale))

    if section is None or (not section.history and section.open_run is None):
        doc.p(catalog.t("admin.bc.none", locale))
    elif section.open_run is not None and section.open_run.in_flight:
        doc.blockquote(
            fragment(
                catalog.t(
                    "admin.bc.open_run",
                    locale,
                    sent=_num(section.open_run.sent, locale),
                    total=_num(section.open_run.total, locale),
                )
            )
        )

    kb: list[list] = []
    kb.append(
        [
            inline(
                catalog.t("admin.bc.compose", locale),
                admin_cb.broadcast_compose(),
                intent=Intent.SUCCESS,
            )
        ]
    )
    if section is not None and section.open_run is not None:
        kb.append(
            [
                inline(
                    catalog.t("admin.bc.open_result", locale),
                    admin_cb.broadcast_result(section.open_run.id),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    kb.append(
        [
            inline(
                catalog.t("admin.bc.result_history", locale),
                admin_cb.broadcast_history(),
                intent=Intent.PRIMARY,
            )
        ]
    )
    doc.p(
        fragment(
            italic(catalog.t("admin.bc.never_auto", locale))
        )
    )
    kb.append(_back_row(locale))
    return Screen(doc=doc, markup=inline_markup(kb))

# -- 2. compose prompt -------------------------------------------------------
def compose_prompt(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.compose_waiting", locale))
    doc.p(catalog.t("admin.bc.compose_hint", locale))
    doc.blockquote(fragment(catalog.t("admin.bc.compose_waiting", locale)))
    kb = [
        [
            inline(
                catalog.t("admin.bc.cancel", locale),
                admin_cb.broadcast_cancel(),
                intent=Intent.PRIMARY,
            )
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(kb))

def compose_refused(view: AdminView, *, reason: str) -> Screen:
    """Refuse an album / unsupported message with the exact reason, then re-ask."""
    locale = view.locale
    screen = compose_prompt(view)
    key = f"admin.bc.refused_{reason}"
    if not catalog.has(key, locale):
        key = "admin.bc.refused_unsupported"
    screen.doc.prepend_raw(str(blockquote(fragment(catalog.t(key, locale)))))
    return screen

# -- 3. audience picker ------------------------------------------------------
def audience_screen(
    view: AdminView,
    section: BroadcastView,
    *,
    source: BroadcastSource,
) -> Screen:
    """Every preset with its **live** count; one tap freezes it into a preview."""
    locale = view.locale
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.audience_options", locale, n=_num(len(section.options), locale)))
    doc.p(catalog.t("admin.bc.received", locale, kind=_kind_label(source.kind, locale)))
    doc.p(catalog.t("admin.bc.audience_hint", locale))
    doc.kv_table(
        [
            (
                catalog.t("admin.bc.audience_title", locale),
                catalog.t(
                    "admin.bc.audience_count",
                    locale,
                    label=_audience_label(option.key, locale),
                    n=_num(option.count, locale),
                ),
            )
            for option in section.options
        ]
    )
    kb: list[list] = []
    for option in section.options:
        kb.append(
            [
                inline(
                    catalog.t(
                        "admin.bc.audience_count",
                        locale,
                        label=_audience_label(option.key, locale),
                        n=_num(option.count, locale),
                    ),
                    admin_cb.broadcast_audience(option.key),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    kb.append(
        [
            inline(
                catalog.t("admin.bc.cancel", locale),
                admin_cb.broadcast_cancel(),
                intent=Intent.PRIMARY,
            )
        ]
    )
    return Screen(doc=doc, markup=inline_markup(kb))

# -- 4. preview --------------------------------------------------------------
def preview_screen(
    view: AdminView,
    *,
    source: BroadcastSource,
    family: AudienceKey,
    count: int,
    ceiling: int,
    over_ceiling: bool,
) -> Screen:
    """The exact copy target plus the recipient count and the two-step confirm.

    The screen does **not** re-render the admin's message: the ``source`` row is
    what the sender passes to ``copy_message``, and the panel states that plainly.
    """
    locale = view.locale
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.preview_recipients", locale, n=_num(count, locale)))
    doc.h(3, catalog.t("admin.bc.preview_title", locale))
    doc.p(catalog.t("admin.bc.preview_of", locale))
    doc.blockquote(
        fragment(
            [
                catalog.t("admin.bc.preview_recipients", locale, n=_num(count, locale)),
                " · ",
                code(f"{source.chat_id}:{source.message_id}"),
            ]
        )
    )
    if over_ceiling:
        doc.blockquote(
            fragment(
                catalog.t(
                    "admin.bc.over_ceiling",
                    locale,
                    count=_num(count, locale),
                    ceiling=_num(ceiling, locale),
                )
            )
        )

    kb: list[list] = []
    if over_ceiling:
        # Only the explicit bigger-number consent goes out here.
        kb.append(
            [
                inline(
                    catalog.t("admin.bc.preview_more", locale),
                    admin_cb.broadcast_confirm(family, over_ceiling=True),
                    intent=Intent.DANGER,
                )
            ]
        )
    else:
        kb.append(
            [
                inline(
                    catalog.t("admin.bc.preview_send", locale),
                    admin_cb.broadcast_confirm(family, over_ceiling=False),
                    intent=Intent.SUCCESS,
                )
            ]
        )
    kb.append(
        [
            inline(
                catalog.t("admin.bc.preview_back", locale),
                admin_cb.section("broadcast"),
                intent=Intent.PRIMARY,
            )
        ]
    )
    return Screen(doc=doc, markup=inline_markup(kb))

# -- 5. sending progress -----------------------------------------------------
def progress_screen(view: AdminView, run: BroadcastRun) -> Screen:
    """The in-place progress: sent / failed / remaining, updated on the same message."""
    locale = view.locale
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.progress_total", locale, n=_num(run.total, locale)))
    doc.h(3, catalog.t("admin.bc.sending", locale))
    doc.blockquote(
        fragment(
            catalog.t(
                "admin.bc.progress",
                locale,
                sent=_num(run.sent, locale),
                failed=_num(run.failed, locale),
                remaining=_num(run.remaining, locale),
                total=_num(run.total, locale),
            )
        )
    )
    doc.p(catalog.t("admin.bc.sending_in_place", locale))
    kb = [
        [
            inline(
                catalog.t("admin.bc.open_result", locale),
                admin_cb.broadcast_result(run.id),
                intent=Intent.PRIMARY,
            )
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(kb))

# -- 6. result ---------------------------------------------------------------
def result_screen(view: AdminView, result: BroadcastResult) -> Screen:
    locale = view.locale
    run = result.run
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.progress_total", locale, n=_num(run.total, locale)))
    if run.status == "stopped":
        doc.h(3, catalog.t("admin.bc.result_stopped_title", locale))
    elif run.status == "interrupted":
        doc.h(3, catalog.t("admin.bc.result_interrupted_title", locale))
    else:
        doc.h(3, catalog.t("admin.bc.result_title", locale))

    doc.kv_table(
        [
            (catalog.t("admin.bc.result_total", locale), _num(run.total, locale)),
            (catalog.t("admin.bc.result_delivered", locale), _num(run.sent, locale)),
            (catalog.t("admin.bc.result_failed", locale), _num(run.failed, locale)),
            (catalog.t("admin.bc.result_remaining", locale), _num(run.remaining, locale)),
        ]
    )
    if run.status == "interrupted":
        doc.blockquote(fragment(catalog.t("admin.bc.interrupted", locale)))
    if run.stop_reason == "consecutive_failures":
        total_failed = (
            result.group_count("blocked")
            + result.group_count("deactivated")
            + result.group_count("other")
        )
        doc.blockquote(
            fragment(
                catalog.t(
                    "admin.bc.stop_consecutive",
                    locale,
                    n=_num(total_failed or run.failed, locale),
                )
            )
        )
    if result.groups:
        doc.kv_table(
            [
                (catalog.t("admin.bc.result_blocked", locale), _num(result.group_count("blocked"), locale)),
                (catalog.t("admin.bc.result_deactivated", locale), _num(result.group_count("deactivated"), locale)),
                (catalog.t("admin.bc.result_other", locale), _num(result.group_count("other"), locale)),
            ]
        )
    elif run.failed == 0:
        doc.p(catalog.t("admin.bc.result_none", locale))

    kb: list[list] = []
    if run.failed > 0 and not run.in_flight:
        kb.append(
            [
                inline(
                    catalog.t("admin.bc.result_retry", locale),
                    admin_cb.broadcast_retry(run.id),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    kb.append(
        [
            inline(
                catalog.t("admin.bc.result_history", locale),
                admin_cb.broadcast_history(),
                intent=Intent.PRIMARY,
            )
        ]
    )
    kb.append(_back_row(locale))
    return Screen(doc=doc, markup=inline_markup(kb))

# -- 7. history --------------------------------------------------------------
def history_screen(view: AdminView, runs: tuple[BroadcastRun, ...]) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    _header(doc, locale, catalog.t("admin.bc.history_count", locale, n=_num(len(runs), locale)))
    doc.h(3, catalog.t("admin.bc.history_title", locale))
    if not runs:
        doc.p(catalog.t("admin.bc.history_empty", locale))
        return Screen(
            doc=doc,
            markup=inline_markup([_back_row(locale)]),
        )
    # One block per run: the state in the heading, then the audience and the real
    # counts on one fact line — never a four-column table.
    for run in runs:
        doc.h(3, catalog.t(f"admin.bc.history_state.{run.status}", locale))
        doc.p(
            f"{_audience_label(run.audience, locale)} · "
            + catalog.t(
                "admin.bc.history_counts_inline",
                locale,
                sent=_num(run.sent, locale),
                failed=_num(run.failed, locale),
            )
            + f" · {format_datetime(run.created_at, locale)}"
        )
    doc.p(fragment(italic(catalog.t("admin.bc.history_resend_note", locale))))
    kb: list[list] = []
    for run in runs:
        # The `(chat, message)` reference is visible, so the admin can see the
        # history is a reference, not a copy of his text.
        kb.append(
            [
                inline(
                    catalog.t("admin.bc.history_resend", locale),
                    admin_cb.broadcast_resend(run.id),
                    intent=Intent.PRIMARY,
                ),
                inline(
                    catalog.t("admin.bc.open_result", locale),
                    admin_cb.broadcast_result(run.id),
                    intent=Intent.PRIMARY,
                ),
            ]
        )
    kb.append(_back_row(locale))
    return Screen(doc=doc, markup=inline_markup(kb))

register(
    AdminSection(
        key="broadcast",
        title_key="admin.section.broadcast",
        order=32,
        icon="📢",
        build=broadcast_screen,
    )
)
