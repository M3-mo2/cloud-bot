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
from cloudbot.presentation.screens.admin._helpers import (
    add_html_header,
    num,
    subject,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def _enabled_count(view: AdminView) -> int:
    return sum(
        1 for category in EventCategory if view.notification_subscriptions.get(category.value, True)
    )


def notifications_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)

    paused = view.notify_paused
    subscribed = _enabled_count(view)
    add_html_header(
        doc,
        subject(locale, "admin.notify.title"),
        catalog.t("admin.notify.enabled_count", locale, n=num(subscribed, locale)),
        locale,
    )

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

    # One line per category (label, then its state), never a two-column table that
    # a right-to-left message misaligns. The two-column table is reserved for a
    # label and its value; a list of subjects is a list.
    for category in EventCategory:
        enabled = view.notification_subscriptions.get(category.value, True)
        doc.p(
            fragment(
                [
                    catalog.t(category.label_key, locale),
                    " — ",
                    catalog.t("admin.notify.on" if enabled else "admin.notify.off", locale),
                ]
            )
        )

    all_on = all(view.notification_subscriptions.get(c.value, True) for c in EventCategory)
    kb = []
    for category in EventCategory:
        enabled = view.notification_subscriptions.get(category.value, True)
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
