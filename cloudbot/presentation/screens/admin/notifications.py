"""Admin section: notification settings (per-category toggles + all + pause)."""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.events import EventCategory
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def notifications_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.notify.title", locale))

    paused = view.notify_paused
    doc.blockquote(
        fragment(
            catalog.t(
                "admin.notify.paused" if paused else "admin.notify.active", locale
            )
        )
    )
    doc.p(catalog.t("admin.notify.cursor", locale, cursor=num(view.notification_cursor, locale)))
    if view.notify_scope is None:
        doc.p(italic(catalog.t("admin.notify.scope_none", locale)))
    else:
        doc.p(catalog.t("admin.notify.scope", locale, tg_id=num(view.notify_scope, locale)))
    doc.p(italic(catalog.t("admin.notify.hint", locale)))

    rows = []
    for category in EventCategory:
        enabled = view.subscriptions.get(category.value, True)
        rows.append(
            [
                catalog.t(category.label_key, locale),
                catalog.t("admin.notify.on" if enabled else "admin.notify.off", locale),
            ]
        )
    doc.table(
        [
            catalog.t("admin.notify.col.category", locale),
            catalog.t("admin.notify.col.state", locale),
        ],
        rows,
    )

    all_on = all(view.subscriptions.get(c.value, True) for c in EventCategory)
    kb = []
    for category in EventCategory:
        enabled = view.subscriptions.get(category.value, True)
        label = catalog.t(
            "admin.notify.toggle_on" if enabled else "admin.notify.toggle_off",
            locale,
            category=catalog.t(category.label_key, locale),
        )
        kb.append(
            [
                inline(
                    label,
                    admin_cb.notify_toggle(category.value),
                    intent=Intent.SUCCESS if enabled else Intent.MUTED,
                )
            ]
        )
    kb.append(
        [
            inline(
                catalog.t("admin.notify.all_off" if all_on else "admin.notify.all_on", locale),
                admin_cb.notify_all(not all_on),
                intent=Intent.PRIMARY,
            ),
            inline(
                catalog.t("admin.notify.resume" if paused else "admin.notify.pause", locale),
                admin_cb.notify_pause(not paused),
                intent=Intent.SUCCESS if paused else Intent.PRIMARY,
            ),
        ]
    )
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


register(
    AdminSection(
        key="notify",
        title_key="admin.section.notify",
        order=30,
        icon="🔔",
        build=notifications_screen,
    )
)
