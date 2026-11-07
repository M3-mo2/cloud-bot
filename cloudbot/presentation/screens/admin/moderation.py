"""Admin section: moderation — banned users, banned workspaces, and messaging.

One section «الإدارة والعقوبات» listing both ban kinds with a way into each. The
"message a user" action lives on ``user_page`` (where the user already is), not in
a second search UI: the screen that already finds a user offers the message and
the ban there.

Screens are pure: they render the ``AdminView`` they are given, never call the
store.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.moderation import UserBan
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

def moderation_screen(view: AdminView) -> Screen:
    """The section home: every ban currently in force, split by kind."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.mod.title", locale))

    users = view.banned_users
    workspaces = view.banned_workspaces
    back = [inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]

    if not users and not workspaces:
        doc.p(catalog.t("admin.mod.empty", locale))
        return Screen(doc=doc, markup=inline_markup([back]))

    kb: list[list] = []
    if users:
        doc.p(catalog.t("admin.mod.users_title", locale))
        rows = [
            [code(str(ban.tg_id)), ban.reason or "—", _until(ban, locale)]
            for ban in users
        ]
        doc.table(
            [
                catalog.t("admin.mod.col.user", locale),
                catalog.t("admin.mod.col.reason", locale),
                catalog.t("admin.mod.col.until", locale),
            ],
            rows,
        )
        for ban in users:
            kb.append(
                [
                    inline(
                        catalog.t("admin.mod.open_user", locale, id=num(ban.tg_id, locale)),
                        admin_cb.user(ban.tg_id),
                        intent=Intent.PRIMARY,
                    ),
                    inline(
                        catalog.t("admin.mod.unban_user", locale),
                        admin_cb.user_unban(ban.tg_id),
                        intent=Intent.SUCCESS,
                    ),
                ]
            )

    if workspaces:
        doc.p(catalog.t("admin.mod.ws_title", locale))
        rows = [
            [code(ban.ws_id), ban.reason or "—", format_datetime(ban.banned_at, locale)]
            for ban in workspaces
        ]
        doc.table(
            [
                catalog.t("admin.mod.col.ws", locale),
                catalog.t("admin.mod.col.reason", locale),
                catalog.t("admin.mod.col.when", locale),
            ],
            rows,
        )
        for ban in workspaces:
            kb.append(
                [
                    inline(
                        catalog.t("admin.mod.unban_ws", locale),
                        admin_cb.ws_unban(ban.ws_id),
                        intent=Intent.SUCCESS,
                    )
                ]
            )

    kb.append(back)
    return Screen(doc=doc, markup=inline_markup(kb))

def _until(ban: UserBan, locale) -> str:
    if ban.expires_at is None:
        return catalog.t("admin.mod.until_permanent", locale)
    return format_datetime(ban.expires_at, locale)

def ban_prompt(kind: str, subject: str, label: str, locale, *, reason: str = "") -> Screen:
    """The ban-reason prompt. Every button carries a subject, so there is a way out.

    The reason comes first (the admin's own words); once it is captured the
    duration/confirm buttons appear. ``reason`` empty means "still waiting".
    """
    doc = RichDoc(locale)
    if not reason:
        doc.h(2, catalog.t("admin.mod.reason_title", locale))
        doc.p(catalog.t("admin.mod.ban_target", locale, subject=label))
        doc.p(italic(catalog.t("admin.mod.reason_hint", locale)))
        rows = [
            [inline(catalog.t("common.cancel", locale), _cancel_cb(kind, subject), intent=Intent.PRIMARY)]
        ]
        return Screen(doc=doc, markup=inline_markup(rows))
    doc.p(catalog.t("admin.mod.reason_captured", locale, reason=reason))
    if kind == "ws":
        doc.h(2, catalog.t("admin.mod.ban_ws_title", locale))
        doc.p(catalog.t("admin.mod.ban_ws_intro", locale, subject=label))
        rows = [
            [
                inline(
                    catalog.t("admin.mod.ban_ws_confirm", locale),
                    admin_cb.ban_go("ws", subject, 0),
                    intent=Intent.DANGER,
                )
            ],
            [inline(catalog.t("common.cancel", locale), _cancel_cb("ws", subject), intent=Intent.PRIMARY)],
        ]
    else:
        doc.h(2, catalog.t("admin.mod.ban_user_title", locale))
        doc.p(catalog.t("admin.mod.ban_user_intro", locale, subject=label))
        doc.p(catalog.t("admin.mod.ban_user_hint", locale))
        rows = [
            [
                inline(
                    catalog.t("admin.mod.ban_days", locale, days=num(1, locale)),
                    admin_cb.ban_go("user", subject, 1),
                    intent=Intent.DANGER,
                ),
                inline(
                    catalog.t("admin.mod.ban_days", locale, days=num(7, locale)),
                    admin_cb.ban_go("user", subject, 7),
                    intent=Intent.DANGER,
                ),
            ],
            [
                inline(
                    catalog.t("admin.mod.ban_permanent", locale),
                    admin_cb.ban_go("user", subject, 0),
                    intent=Intent.DANGER,
                )
            ],
            [inline(catalog.t("common.cancel", locale), _cancel_cb("user", subject), intent=Intent.PRIMARY)],
        ]
    return Screen(doc=doc, markup=inline_markup(rows))

def _cancel_cb(kind: str, subject: str) -> str:
    if kind == "ws":
        return admin_cb.section("moderation")
    try:
        return admin_cb.user(int(subject))
    except (TypeError, ValueError):
        return admin_cb.menu()

def message_prompt(tg_id: int, label: str, locale) -> Screen:
    """The message prompt: the next text the admin sends is the message."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.mod.msg_title", locale))
    doc.p(catalog.t("admin.mod.msg_intro", locale, subject=label))
    doc.p(italic(catalog.t("admin.mod.msg_hint", locale)))
    rows = [
        [inline(catalog.t("common.cancel", locale), admin_cb.user(tg_id), intent=Intent.PRIMARY)]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))

def message_result(
    tg_id: int,
    message: str,
    *,
    delivered: bool,
    message_id: int | None,
    reason_code: str,
    locale,
) -> Screen:
    """The **real** send outcome: the message id, or Telegram's own refusal."""
    doc = RichDoc(locale)
    if delivered:
        doc.h(2, catalog.t("admin.mod.msg_sent_title", locale))
        doc.p(catalog.t("admin.mod.msg_sent_body", locale, id=num(message_id or 0, locale)))
    else:
        doc.h(2, catalog.t("admin.mod.msg_failed_title", locale))
        key = f"admin.mod.msg_failed.{reason_code or 'unknown'}"
        if not catalog.has(key, locale):
            key = "admin.mod.msg_failed.unknown"
        doc.p(catalog.t(key, locale))
    doc.blockquote(fragment(message))
    rows = [
        [
            inline(catalog.t("admin.mod.msg_again", locale), admin_cb.user_msg(tg_id), intent=Intent.PRIMARY),
            inline(catalog.t("admin.back", locale), admin_cb.user(tg_id), intent=Intent.PRIMARY),
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))

register(
    AdminSection(
        key="moderation",
        title_key="admin.section.moderation",
        order=15,
        icon="🚫",
        build=moderation_screen,
    )
)
