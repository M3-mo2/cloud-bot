"""Admin section: the event log / audit trail.

This is the same table the notifier reads (``events``), so every admin action and
every event is queryable and trustworthy.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView, AuditPage, UserPage
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

PAGE_SIZE = 15


def audit_screen(view: AdminView, *, page: int = 0, tg_id: int | None = None) -> Screen:
    """The audit log. ``tg_id`` scopes it to one user (the user-page variant)."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.audit.title", locale))

    page_obj: AuditPage | None = view.audit_page
    if page_obj is not None and page_obj.actor:
        doc.p(catalog.t("admin.audit.filtering_actor", locale, actor=page_obj.actor))
    if page_obj is not None and page_obj.category:
        doc.p(
            catalog.t(
                "admin.audit.filtering_category",
                locale,
                category=catalog.t(
                    f"event.cat.{page_obj.category}", locale
                ),
            )
        )
    if tg_id is not None:
        doc.p(catalog.t("admin.audit.filtering_user", locale, tg_id=num(tg_id, locale)))

    if page_obj is None or not page_obj.events:
        doc.p(italic(catalog.t("admin.audit.empty", locale)))
    else:
        doc.p(catalog.t("admin.audit.count", locale, n=num(page_obj.total, locale)))
        rows = []
        for e in page_obj.events:
            # Three columns: when, who, what. The numeric event id is internal
            # and the category is the filter you are already looking through, so
            # neither earns a column on a phone.
            rows.append(
                [
                    format_datetime(e.ts, locale),
                    catalog.t("event.actor.user", locale)
                    if e.actor == "user"
                    else catalog.t("event.actor.system", locale)
                    if e.actor == "system"
                    else catalog.t("event.actor.admin_short", locale, id=e.actor.split(":")[-1]),
                    catalog.t(e.summary_key, locale, **e.params),
                ]
            )
        doc.table(
            [
                catalog.t("admin.audit.col.time", locale),
                catalog.t("admin.audit.col.actor", locale),
                catalog.t("admin.audit.col.summary", locale),
            ],
            rows,
        )

    kb: list[list] = []
    # Filters by category (the first few) to keep the keyboard bounded.
    from cloudbot.domain.events import EventCategory

    filter_row = []
    for category in list(EventCategory)[:4]:
        filter_row.append(
            inline(
                catalog.t(category.label_key, locale),
                admin_cb.audit_cat(category.value, 0),
                intent=Intent.PRIMARY,
            )
        )
    kb.append(filter_row)
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
