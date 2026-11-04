"""Admin section: the users list (paged, searchable, honest empty state)."""

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
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

PAGE_SIZE = 10


def users_screen(view: AdminView, *, page: int = 0) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.users.title", locale))

    page_obj: UserListPage | None = view.users_page
    if page_obj is None:
        doc.p(catalog.t("admin.overview.unavailable", locale))
        return Screen(doc=doc, markup=_nav(locale, 0, 1))

    if page_obj.query:
        doc.p(catalog.t("admin.users.searching", locale, query=page_obj.query))

    if not page_obj.users:
        doc.p(catalog.t("admin.users.empty", locale))
    else:
        doc.p(
            catalog.t(
                "admin.users.count", locale, n=num(page_obj.total, locale)
            )
        )
        rows = []
        for u in page_obj.users:
            last_seen = (
                format_relative(u.last_seen_at, view.now, locale)
                if u.last_seen_at and view.now is not None
                else format_datetime(u.last_seen_at, locale)
                if u.last_seen_at
                else "—"
            )
            rows.append(
                [
                    code(str(u.tg_id)),
                    u.display_name or "—",
                    catalog.t(f"lang.name.{u.locale.value}", locale),
                    catalog.t(f"plan.{u.plan}", locale),
                    num(u.workspace_count, locale),
                    last_seen,
                ]
            )
        doc.table(
            [
                catalog.t("admin.users.col.id", locale),
                catalog.t("admin.users.col.name", locale),
                catalog.t("admin.users.col.locale", locale),
                catalog.t("admin.users.col.plan", locale),
                catalog.t("admin.users.col.workspaces", locale),
                catalog.t("admin.users.col.last_seen", locale),
            ],
            rows,
        )

    kb = []
    for u in page_obj.users:
        label = catalog.t(
            "admin.users.open", locale, name=u.display_name or f"#{num(u.tg_id, locale)}"
        )
        kb.append([inline(label, admin_cb.user(u.tg_id), intent=Intent.PRIMARY)])
    kb.append(
        [
            inline(
                catalog.t("admin.users.search", locale),
                admin_cb.users_search(),
                intent=Intent.PRIMARY,
            )
        ]
    )
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
