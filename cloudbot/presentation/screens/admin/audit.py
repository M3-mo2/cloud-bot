"""Admin section: the event log / audit trail.

This is the same table the notifier reads (``events``), so every admin action and
every event is queryable and trustworthy.

The log is **blocks**, not a three-column table: a right-to-left message put the
timestamp in its own column, where the eye lands on it first and needs it least.
Each event is a bold heading (what happened) and one compact fact line (the actor,
the category and the time), in the order a person asks it.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView, AuditPage
from cloudbot.domain.events import EventCategory
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime
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

PAGE_SIZE = 15


def _actor(e, locale) -> str:
    if e.actor == "user":
        return catalog.t("event.actor.user", locale)
    if e.actor == "system":
        return catalog.t("event.actor.system", locale)
    return catalog.t("event.actor.admin_short", locale, id=e.actor.split(":")[-1])


def audit_screen(view: AdminView, *, page: int = 0, tg_id: int | None = None) -> Screen:
    """The audit log. ``tg_id`` scopes it to one user (the user-page variant)."""
    locale = view.locale
    doc = RichDoc(locale)

    page_obj: AuditPage | None = view.audit_page

    notices: list[str] = []
    if page_obj is not None and page_obj.actor:
        notices.append(catalog.t("admin.audit.filtering_actor", locale, actor=page_obj.actor))
    if page_obj is not None and page_obj.category:
        notices.append(
            catalog.t(
                "admin.audit.filtering_category",
                locale,
                category=catalog.t(f"event.cat.{page_obj.category}", locale),
            )
        )
    if tg_id is not None:
        notices.append(catalog.t("admin.audit.filtering_user", locale, tg_id=num(tg_id, locale)))

    add_block_header(
        doc,
        subject(locale, "admin.audit.title"),
        catalog.t("admin.audit.count", locale, n=num(page_obj.total if page_obj else 0, locale)),
        locale,
    )
    for notice in notices:
        doc.paragraph(notice)

    if page_obj is None or not page_obj.events:
        doc.paragraph(catalog.t("admin.audit.empty", locale))
    else:
        for e in page_obj.events:
            doc.heading(catalog.t(e.summary_key, locale, **e.params), size=3)
            doc.paragraph(
                f"{_actor(e, locale)} · {catalog.t(e.category.label_key, locale)} · "
                f"{format_datetime(e.ts, locale)}"
            )

    kb: list[list] = []

    # Filters by category (the first few) to keep the keyboard bounded, in the
    # project's two-per-row layout. The label says it is a filter, so a button
    # cannot be mistaken for a fresh subject.
    filters = [
        inline(
            catalog.t(
                "admin.audit.filter_by",
                locale,
                category=catalog.t(category.label_key, locale),
            ),
            admin_cb.audit_cat(category.value, 0),
            intent=Intent.PRIMARY,
        )
        for category in list(EventCategory)[:4]
    ]
    for index in range(0, len(filters), 2):
        kb.append(filters[index : index + 2])
    kb.append(
        [
            inline(
                catalog.t("admin.audit.all", locale),
                admin_cb.audit_all(tg_id, 0),
                intent=Intent.PRIMARY,
            )
        ]
    )
    if page_obj is not None:
        paging = []
        if page > 0:
            paging.append(
                inline(
                    catalog.t("admin.users.prev", locale),
                    _page_callback(tg_id, page - 1),
                    intent=Intent.PRIMARY,
                )
            )
        if (page + 1) * page_obj.limit < page_obj.total:
            paging.append(
                inline(
                    catalog.t("admin.users.next", locale),
                    _page_callback(tg_id, page + 1),
                    intent=Intent.PRIMARY,
                )
            )
        if paging:
            kb.append(paging)
    if tg_id is not None:
        kb.append([inline(catalog.t("admin.back", locale), admin_cb.user(tg_id), intent=Intent.PRIMARY)])
    else:
        kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _page_callback(tg_id: int | None, page: int) -> str:
    """The audit paging callback — per-user when scoped, global otherwise."""
    if tg_id is not None:
        return admin_cb.audit_user(tg_id, page)
    return admin_cb.audit(page)


register(
    AdminSection(
        key="audit",
        title_key="admin.section.audit",
        order=40,
        icon="🧾",
        build=lambda view: audit_screen(view, page=0),
    )
)
