"""Admin section: one user's page."""

from __future__ import annotations

from cloudbot.domain.admin import AdminView, UserPage
from cloudbot.domain.events import EventRecord
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_bytes_pair,
    format_datetime,
    format_percent,
    format_relative,
)
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import display_state
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.admin.actions import action, action_button
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def user_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    page: UserPage | None = view.user_page
    if page is None:
        doc.h(2, catalog.t("admin.user.missing_title", locale))
        doc.p(catalog.t("admin.user.missing_body", locale))
        return Screen(
            doc=doc,
            markup=inline_markup(
                [[inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
            ),
        )

    u = page.user
    doc.h(2, catalog.t("admin.user.title", locale))
    doc.kv_table(
        [
            (catalog.t("admin.user.id", locale), code(str(u.tg_id))),
            (catalog.t("admin.user.name", locale), u.display_name or "—"),
            (catalog.t("admin.user.locale", locale), catalog.t(f"lang.name.{u.locale.value}", locale)),
            (catalog.t("admin.user.plan", locale), catalog.t(f"plan.{u.plan}", locale)),
            (catalog.t("admin.user.created", locale), format_datetime(u.created_at, locale)),
            (
                catalog.t("admin.user.last_seen", locale),
                format_relative(u.last_seen_at, view.now, locale)
                if u.last_seen_at and view.now is not None
                else "—",
            ),
        ]
    )

    # The ban is a state, shown here and reversible in one tap.
    if view.user_ban is not None:
        doc.blockquote(
            fragment(catalog.t("admin.user.banned", locale, reason=view.user_ban.reason or "—"))
        )

    doc.div()
    doc.p(catalog.t("admin.user.workspaces", locale))
    kb: list[list] = []
    enter = action("enter")
    if not page.workspaces:
        doc.p(italic(catalog.t("admin.user.no_workspaces", locale)))
    else:
        rows = []
        for wu in page.workspaces:
            ws = wu.workspace
            label_key, emoji = display_state(ws.status, ws.summary.state)
            status = f"{emoji} {catalog.t(label_key, locale)}"
            if wu.usage is not None:
                usage = format_bytes_pair(
                    wu.usage.disk_bytes,
                    wu.usage.disk_limit_bytes,
                    locale,
                    percent=wu.usage.disk_percent,
                )
            else:
                usage = catalog.t("admin.user.usage_unknown", locale)
            ban_reason = view.workspace_bans.get(str(ws.id))
            if ban_reason is not None:
                status = catalog.t("admin.user.ws_banned", locale, reason=ban_reason or "—")
            rows.append(
                [ws.name, code(str(ws.id)), status, usage]
            )
        doc.table(
            [
                catalog.t("admin.user.col.name", locale),
                catalog.t("admin.user.col.id", locale),
                catalog.t("admin.user.col.status", locale),
                catalog.t("admin.user.col.usage", locale),
            ],
            rows,
        )
        for wu in page.workspaces:
            ws = wu.workspace
            subject = str(ws.id)
            kb.append(
                [
                    action_button(enter, subject, locale)
                    if enter
                    else inline(catalog.t("admin.action.enter", locale), "nav:noop")
                ]
            )
            # quick destructive actions are only offered on the workspace row's
            # own line, and each goes through its declaration's confirmation.
            row = []
            for key in ("suspend", "resume", "delete"):
                act = action(key)
                if act is not None:
                    row.append(action_button(act, subject, locale))
            kb.append(row)
            # A workspace ban is reversible from here: lift, or (if not banned) ban.
            if str(ws.id) in view.workspace_bans:
                kb.append(
                    [
                        inline(
                            catalog.t("admin.user.ws_unban", locale),
                            admin_cb.ws_unban(subject),
                            intent=Intent.SUCCESS,
                        )
                    ]
                )
            else:
                kb.append(
                    [
                        inline(
                            catalog.t("admin.user.ws_ban", locale),
                            admin_cb.ws_ban(subject),
                            intent=Intent.DANGER,
                        )
                    ]
                )

    _recent_activity(doc, view, locale)
    kb.append(
        [
            inline(
                catalog.t("admin.user.audit", locale),
                admin_cb.audit_user(u.tg_id, 0),
                intent=Intent.PRIMARY,
            ),
            inline(
                catalog.t("admin.wallet.title", locale),
                admin_cb.wallet(u.tg_id),
                intent=Intent.SUCCESS,
            ),
        ]
    )
    # Moderation: unban (one tap) or ban, and message this user right here.
    if view.user_ban is not None:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.unban", locale),
                    admin_cb.user_unban(u.tg_id),
                    intent=Intent.SUCCESS,
                )
            ]
        )
    else:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.ban", locale),
                    admin_cb.user_ban(u.tg_id),
                    intent=Intent.DANGER,
                ),
                inline(
                    catalog.t("admin.user.message", locale),
                    admin_cb.user_msg(u.tg_id),
                    intent=Intent.PRIMARY,
                ),
            ]
        )
    if view.user_ban is not None:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.message", locale),
                    admin_cb.user_msg(u.tg_id),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    # The notification scope for this user, if the admin wants to watch only them.
    if view.notify_scope == u.tg_id:
        scope_label = catalog.t("admin.user.scope_clear", locale)
    else:
        scope_label = catalog.t("admin.user.scope_set", locale)
    kb.append([inline(scope_label, admin_cb.notify_scope(u.tg_id), intent=Intent.PRIMARY)])
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.section("users"), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _recent_activity(doc: RichDoc, view: AdminView, locale) -> None:
    doc.div()
    doc.p(catalog.t("admin.user.recent", locale))
    events: list[EventRecord] = view.recent_events
    if not events:
        doc.p(italic(catalog.t("admin.user.no_activity", locale)))
        return
    rows = []
    for e in events:
        rows.append(
            [
                format_datetime(e.ts, locale),
                catalog.t(e.category.label_key, locale),
                catalog.t(e.summary_key, locale, **e.params),
            ]
        )
    doc.table(
        [
            catalog.t("admin.audit.col.time", locale),
            catalog.t("admin.audit.col.category", locale),
            catalog.t("admin.audit.col.summary", locale),
        ],
        rows,
    )


register(
    AdminSection(
        key="user",
        title_key="admin.section.user",
        order=20,
        icon="👤",
        build=user_screen,
        in_menu=False,
    )
)
