"""Admin section: «اشتراكات إجبارية» — the mandatory-subscription gate.

Pure screens (no I/O): they render ``AdminView.forcejoin_chats``, the global
switch, one measured *Test* per chat, and the gate's statistics read model.

A chat the bot does **not** administer is shown as **needs attention** — the gate
lets users through there instead of locking them out, and the screen says that
explicitly so the operator knows an ``unknown`` lets users in.

The chat list is **blocks**: a bold heading per chat with its fact line and its own
*Enable/Disable* + *Test* buttons straight under it, never a four-column table. A
destructive *Remove* gets its own row.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.forcejoin import ForceJoinChat
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_percent
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import (
    add_block_header,
    num,
    subject,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

def forcejoin_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)

    enabled = view.forcejoin_enabled
    chats = view.forcejoin_chats
    attention = [c for c in chats if c.enabled and not c.bot_is_admin]
    add_block_header(
        doc,
        subject(locale, "admin.fj.title"),
        catalog.t("admin.fj.chat_count", locale, n=num(len(chats), locale)),
        locale,
    )

    doc.quote(
        catalog.t("admin.fj.global_on" if enabled else "admin.fj.global_off", locale)
    )

    if not chats:
        doc.paragraph(catalog.t("admin.fj.empty", locale))
    else:
        for chat in chats:
            doc.heading(f"{chat.label}", size=3)
            doc.paragraph(
                " · ".join(
                    [
                        catalog.t("admin.fj.bot_admin_fact", locale)
                        if chat.bot_is_admin
                        else catalog.t("admin.fj.needs_attention", locale),
                        catalog.t(f"admin.fj.kind.{chat.kind}", locale),
                        catalog.t("admin.fj.state_on" if chat.enabled else "admin.fj.state_off", locale),
                        f"@{chat.username}" if chat.username else str(chat.chat_id),
                    ]
                )
            )
            label = _short(chat, locale)
            toggle = catalog.t(
                "admin.fj.disable" if chat.enabled else "admin.fj.enable", locale, chat=label
            )
            doc.buttons(
                [
                    (
                        toggle,
                        admin_cb.forcejoin_toggle(chat.chat_id),
                        None if chat.enabled else "success",
                    ),
                    (
                        catalog.t("admin.fj.test_one", locale, chat=label),
                        admin_cb.forcejoin_test(chat.chat_id),
                        "primary",
                    ),
                ]
            )
            doc.buttons(
                [
                    (
                        catalog.t("admin.fj.remove", locale, chat=label),
                        admin_cb.forcejoin_remove(chat.chat_id),
                        "danger",
                    )
                ]
            )
        if attention:
            doc.paragraph(
                catalog.t("admin.fj.broken_warning", locale, count=num(len(attention), locale))
            )

    # The anti-lockout fact, stated so the operator is never surprised.
    doc.paragraph(catalog.t("admin.fj.antilockout", locale))
    doc.paragraph(catalog.t("admin.fj.add_hint", locale))

    kb: list[list] = []
    kb.append(
        [
            inline(
                catalog.t("admin.fj.add", locale),
                admin_cb.forcejoin_add(),
                intent=Intent.SUCCESS,
            ),
            inline(
                catalog.t("admin.fj.check_me", locale),
                admin_cb.forcejoin_check(),
                intent=Intent.PRIMARY,
            ),
        ]
    )
    kb.append(
        [
            inline(
                catalog.t("admin.fj.stats", locale),
                admin_cb.forcejoin_stats("7d"),
                intent=Intent.PRIMARY,
            )
        ]
    )
    kb.append(
        [
            inline(
                catalog.t(
                    "admin.fj.switch_off" if enabled else "admin.fj.switch_on", locale
                ),
                admin_cb.forcejoin_switch(not enabled),
                intent=Intent.DANGER if enabled else Intent.SUCCESS,
            )
        ]
    )
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))

def _short(chat: ForceJoinChat, locale) -> str:
    """A chat label short enough for a button (the title, else @username)."""
    label = chat.label
    return label if len(label) <= 24 else f"{label[:23]}…"

def add_prompt(view: AdminView) -> Screen:
    """The one-message add: the admin sends a @username / id / t.me link."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.fj.add_title", locale))
    doc.p(catalog.t("admin.fj.add_prompt", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("forcejoin"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )

def check_result_screen(
    view: AdminView,
    *,
    rows: list[tuple[str, bool, bool]],
    ok: bool,
) -> Screen:
    """The admin's end-to-end proof: every required chat with a measured verdict.

    ``rows`` is ``(label, subscribed, unknown)`` — ``unknown`` is honest, never
    reported as "not subscribed".
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.fj.check_title", locale))
    doc.blockquote(
        catalog.t(
            "admin.fj.check_ok" if ok else "admin.fj.check_missing",
            locale,
            count=len(rows),
        )
    )
    table = []
    for label, subscribed, unknown in rows:
        if unknown:
            state = catalog.t("admin.fj.check_unknown", locale)
        elif subscribed:
            state = catalog.t("admin.fj.check_subscribed", locale)
        else:
            state = catalog.t("admin.fj.check_not_subscribed", locale)
        table.append([label, state])
    if table:
        doc.table(
            [catalog.t("admin.fj.col.chat", locale), catalog.t("admin.fj.col.result", locale)],
            table,
        )
    else:
        doc.p(catalog.t("admin.fj.check_none", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("forcejoin"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )

def test_result_screen(
    view: AdminView,
    *,
    chat: ForceJoinChat,
    result,
) -> Screen:
    """One chat's real ``getChatMember`` against the operator, with the raw outcome."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.fj.test_title", locale))
    doc.p(chat.label)
    if result.unknown:
        state = catalog.t("admin.fj.check_unknown", locale)
    elif result.subscribed:
        state = catalog.t("admin.fj.check_subscribed", locale)
    else:
        state = catalog.t("admin.fj.check_not_subscribed", locale)
    doc.kv_block(
        [
            (catalog.t("admin.fj.col.result", locale), state),
            (catalog.t("admin.fj.reason", locale), result.reason or "—"),
            (catalog.t("admin.fj.latency", locale), f"{num(result.latency_ms, locale)} ms"),
        ]
    )
    if result.detail:
        doc.paragraph(result.detail)
    doc.paragraph(catalog.t("admin.fj.antilockout", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("forcejoin"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )

_RANGE_LABEL = {"24h": "24h", "7d": "7d", "30d": "30d", "90d": "90d", "all": "all"}

def stats_screen(view: AdminView, *, stats, range_key: str) -> Screen:
    """The Part 3 numbers for the selected range, in the rich-message style."""
    locale = view.locale
    doc = RichDoc(locale)
    add_block_header(
        doc,
        subject(locale, "admin.fj.stats_title"),
        _RANGE_LABEL.get(range_key, range_key),
        locale,
    )

    if not stats.has_data:
        # Honest empty state: a zero here would look like a healthy zero.
        doc.paragraph(catalog.t("admin.fj.stats_empty", locale))
    else:
        gate = stats.gate
        doc.heading(catalog.t("admin.fj.stats_gate", locale), size=3)
        doc.paragraph(
            " · ".join(
                [
                    catalog.t("admin.fj.stats_enabled", locale)
                    + ": "
                    + catalog.t(
                        "admin.fj.state_on" if gate.enabled else "admin.fj.state_off", locale
                    ),
                    catalog.t("admin.fj.stats_chats", locale)
                    + f": {num(gate.chats_enabled, locale)}/{num(gate.chats_total, locale)}",
                    catalog.t("admin.fj.stats_attention", locale)
                    + f": {num(gate.chats_needing_attention, locale)}",
                ]
            )
        )

        doc.heading(catalog.t("admin.fj.stats_volume", locale), size=3)
        doc.paragraph(
            " · ".join(
                [
                    catalog.t("admin.fj.stats_decisions", locale)
                    + f": {num(stats.volume.decisions, locale)}",
                    catalog.t("admin.fj.stats_users", locale)
                    + f": {num(stats.volume.distinct_users, locale)}",
                ]
            )
        )

        # One line per outcome (no 3-column table: numbers would drift in RTL).
        doc.heading(catalog.t("admin.fj.stats_outcomes", locale), size=3)
        for entry in stats.outcomes:
            doc.paragraph(
                f"{entry.outcome} — {num(entry.count, locale)} · "
                f"{format_percent(entry.percent, locale)}"
            )

        funnel = stats.funnel
        doc.heading(catalog.t("admin.fj.stats_funnel", locale), size=3)
        doc.paragraph(
            " · ".join(
                [
                    catalog.t("admin.fj.stats_hit", locale)
                    + f": {num(funnel.hit_gate, locale)}",
                    catalog.t("admin.fj.stats_refused", locale)
                    + f": {num(funnel.refused, locale)}",
                    catalog.t("admin.fj.stats_then_passed", locale)
                    + f": {num(funnel.then_passed, locale)}",
                ]
            )
        )

        doc.heading(catalog.t("admin.fj.stats_health", locale), size=3)
        doc.paragraph(
            " · ".join(
                [
                    catalog.t("admin.fj.stats_unknown_rate", locale)
                    + f": {format_percent(stats.health.unknown_rate, locale)}",
                    catalog.t("admin.fj.stats_error_rate", locale)
                    + f": {format_percent(stats.health.error_rate, locale)}",
                    catalog.t("admin.fj.stats_cache", locale)
                    + f": {format_percent(stats.cache.hit_rate, locale)}",
                ]
            )
        )
        doc.paragraph(
            catalog.t("admin.fj.stats_latency", locale)
            + f": p50 {num(stats.cache.p50_ms, locale)} ms · "
            + f"p95 {num(stats.cache.p95_ms, locale)} ms"
        )

        if stats.per_chat:
            doc.heading(catalog.t("admin.fj.stats_per_chat", locale), size=3)
            for chat in stats.per_chat:
                chat_name = chat.title or (f"@{chat.username}" if chat.username else str(chat.chat_id))
                doc.paragraph(
                    " · ".join(
                        [
                            chat_name,
                            catalog.t("admin.fj.stats_row", locale,
                                      n=num(chat.decisions, locale),
                                      deny=format_percent(chat.deny_rate, locale),
                                      unknown=format_percent(chat.unknown_rate, locale)),
                            catalog.t("admin.fj.bot_admin_fact", locale)
                            if chat.bot_admin
                            else catalog.t("admin.fj.needs_attention", locale),
                        ]
                    )
                )

    rows: list[list] = []
    range_row = []
    for key in ("24h", "7d", "30d", "90d", "all"):
        range_row.append(
            inline(
                _RANGE_LABEL[key],
                admin_cb.forcejoin_stats(key),
                intent=Intent.PRIMARY if key != range_key else Intent.MUTED,
            )
        )
    rows.append(range_row)
    rows.append(
        [inline(catalog.t("admin.back", locale), admin_cb.section("forcejoin"), intent=Intent.PRIMARY)]
    )
    return Screen(doc=doc, markup=inline_markup(rows))

register(
    AdminSection(
        key="forcejoin",
        title_key="admin.section.forcejoin",
        order=35,
        icon="🔗",
        build=forcejoin_screen,
    )
)
