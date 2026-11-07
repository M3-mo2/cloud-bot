"""Admin section: the users list (paged, searchable, honest empty state).

The list is **blocks**, not a three-column table: in a right-to-left message a
row of ``name · id | plan | last seen`` drifts away from its headers. Each user is
a bold heading, one compact fact line, and that user's own *Open* button straight
under it. The two-column identity tables the panel does keep are the ones that
hold a label and its value only.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView, UserListPage
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, format_relative
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, fragment
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import (
    add_block_header,
    num,
    subject,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

PAGE_SIZE = 10


def users_screen(view: AdminView, *, page: int = 0) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)

    page_obj: UserListPage | None = view.users_page
    if page_obj is None:
        add_block_header(doc, subject(locale, "admin.users.title"), "—", locale)
        doc.paragraph(catalog.t("admin.overview.unavailable", locale))
        return Screen(doc=doc, markup=_nav(locale, 0, 1))

    # The header's number is a real measurement: the filtered total when
    # searchable, otherwise the same count line the list prints.
    add_block_header(
        doc,
        subject(locale, "admin.users.title"),
        catalog.t("admin.users.count", locale, n=num(page_obj.total, locale)),
        locale,
    )

    if page_obj.query:
        doc.paragraph(catalog.t("admin.users.searching", locale, query=page_obj.query))

    if not page_obj.users:
        doc.paragraph(catalog.t("admin.users.empty", locale))
    else:
        for u in page_obj.users:
            last_seen = (
                format_relative(u.last_seen_at, view.now, locale)
                if u.last_seen_at and view.now is not None
                else format_datetime(u.last_seen_at, locale)
                if u.last_seen_at
                else "—"
            )
            doc.heading(u.display_name or f"#{num(u.tg_id, locale)}", size=3)
            doc.paragraph(
                f"{catalog.t(f'plan.{u.plan}', locale)} · {last_seen} · #{num(u.tg_id, locale)}"
            )
            doc.buttons(
                [
                    (
                        catalog.t("admin.users.open", locale, name=u.display_name or f"#{num(u.tg_id, locale)}"),
                        admin_cb.user(u.tg_id),
                        "primary",
                    )
                ]
            )

    kb = [
        [
            inline(
                catalog.t("admin.users.search", locale),
                admin_cb.users_search(),
                intent=Intent.PRIMARY,
            )
        ]
    ]
    kb.extend(_paging(locale, page, page_obj.total, page_obj.limit, page_obj.query))
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _nav(locale, page, total_pages):
    return inline_markup(
        [[inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
    )


def _paging(locale, page: int, total: int, limit: int, query: str):
    pages = max(1, (total + limit - 1) // limit)
    row = []
    if page > 0:
        row.append(
            inline(catalog.t("admin.users.prev", locale), admin_cb.users_page(page - 1), intent=Intent.PRIMARY)
        )
    if page + 1 < pages:
        row.append(
            inline(catalog.t("admin.users.next", locale), admin_cb.users_page(page + 1), intent=Intent.PRIMARY)
        )
    return [row] if row else []


register(
    AdminSection(
        key="users",
        title_key="admin.section.users",
        order=10,
        icon="👥",
        build=lambda view: users_screen(view),
    )
)
