"""Admin section: the overview (``/admin``)."""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_duration, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import workspace_status_label
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import fragment
from cloudbot.presentation.screens.admin._helpers import add_entered_banner
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def _num(n: int, locale) -> str:
    return localize_digits(str(n), locale)


def overview_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.title", locale))
    add_entered_banner(doc, view)

    ov = view.overview
    if ov is None:
        doc.p(catalog.t("admin.overview.unavailable", locale))
        return Screen(doc=doc, markup=_menu_markup(locale))

    # Engine health first: an unreachable engine makes the fleet numbers unknown,
    # and that must be visible before the zeros, never hidden behind them.
    if not ov.engine.reachable:
        doc.blockquote(fragment(catalog.t("admin.overview.engine_unreachable", locale)))
    else:
        doc.p(
            catalog.t(
                "admin.overview.engine_ok",
                locale,
                version=ov.engine.version,
                api=ov.engine.api_version,
                health=ov.engine.health_status,
                ready=ov.engine.ready_status,
            )
        )

    doc.kv_table(
        [
            (catalog.t("admin.overview.total_users", locale), _num(ov.total_users, locale)),
            (catalog.t("admin.overview.active_24h", locale), _num(ov.active_24h, locale)),
            (
                catalog.t("admin.overview.uptime", locale),
                format_duration(ov.uptime_seconds, locale),
            ),
        ]
    )

    if ov.engine.reachable:
        rows = []
        for status in WorkspaceStatus:
            count = ov.workspaces_by_status.get(status.value, 0)
            if count or status in (WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED):
                rows.append([workspace_status_label(status, locale), _num(count, locale)])
        if rows:
            doc.table(
                [
                    catalog.t("admin.overview.ws_status", locale),
                    catalog.t("admin.overview.ws_count", locale),
                ],
                rows,
            )
        doc.p(
            catalog.t(
                "admin.overview.live",
                locale,
                runs=_num(ov.live_runs, locale),
                sessions=_num(ov.live_sessions, locale),
            )
        )
    else:
        doc.p(catalog.t("admin.overview.fleet_unknown", locale))

    doc.p(
        catalog.t(
            "admin.overview.queue",
            locale,
            state=catalog.t(f"admin.queue.{ov.queue_state}", locale),
        )
    )
    return Screen(doc=doc, markup=_menu_markup(locale))


def _menu_markup(locale):
    from .section import registry

    rows = []
    for section in registry.menu():
        if section.key == "overview":
            continue
        rows.append(
            [
                inline(
                    section.resolve_title(locale),
                    f"adm:sec:{section.key}",
                    intent=Intent.PRIMARY,
                )
            ]
        )
    return inline_markup(rows)


register(
    AdminSection(
        key="overview",
        title_key="admin.section.overview",
        order=0,
        icon="📊",
        build=overview_screen,
    )
)
