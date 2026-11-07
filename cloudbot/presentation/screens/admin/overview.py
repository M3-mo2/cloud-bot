"""Admin section: the overview (``/admin``).

The panel's home answers, in this order: *is anything broken*, then the three
numbers that matter today (users, active workspaces, runs), then the section
shortcuts. It builds entirely in **blocks** (Bot API 10.3): the header carries the
rule/✦/rule shape every other screen opens with, and the block form keeps the
message free of a four-column dump.

The shortcuts are still the registry's (``_menu_markup`` reads ``registry.menu()``)
— this module never hand-writes the section list.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_duration
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import workspace_status_label
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import (
    add_block_header,
    add_entered_banner,
    num,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def _users_fact(count: int, locale) -> str:
    return catalog.t("admin.users.count", locale, n=num(count, locale))


def overview_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)

    ov = view.overview
    if ov is None:
        add_block_header(doc, catalog.t("admin.title", locale), "—", locale)
        add_entered_banner(doc, view)
        doc.paragraph(catalog.t("admin.overview.unavailable", locale))
        return Screen(doc=doc, markup=_menu_markup(locale))

    add_block_header(doc, catalog.t("admin.title", locale), _users_fact(ov.total_users, locale), locale)
    add_entered_banner(doc, view)

    # 1. Is anything broken? An unreachable engine makes the fleet numbers
    #    unknown, not zero, so the warning comes **first** and is a heading so it
    #    cannot be missed.
    broken = False
    if not ov.engine.reachable:
        broken = True
        doc.heading(f"⚠️ {catalog.t('admin.overview.engine_unreachable', locale)}", size=2)
    elif ov.queue_state != "ok":
        broken = True
        doc.heading(
            "⚠️ "
            + catalog.t(
                "admin.overview.queue_broken",
                locale,
                state=catalog.t(f"admin.queue.{ov.queue_state}", locale),
            ),
            size=2,
        )

    # 2. The three numbers that matter today: users (in the header), active
    #    workspaces and runs.
    if ov.engine.reachable:
        active_workspaces = ov.workspaces_by_status.get(WorkspaceStatus.ACTIVE.value, 0)
        doc.heading(
            f"✦ {catalog.t('admin.overview.active_ws', locale)} ⟸ {num(active_workspaces, locale)}",
            size=3,
        )
        doc.heading(
            f"✦ {catalog.t('admin.overview.runs', locale)} ⟸ {num(ov.live_runs, locale)}",
            size=3,
        )
    else:
        doc.paragraph(catalog.t("admin.overview.fleet_unknown", locale))

    # 3. Reference readings sit in a footer: the engine identity, the whole status
    #    breakdown, uptime and queue are not the glance the screen exists for.
    if ov.engine.reachable and ov.workspaces_by_status:
        statuses = []
        for status in WorkspaceStatus:
            count = ov.workspaces_by_status.get(status.value, 0)
            if count or status in (WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED):
                statuses.append(f"{workspace_status_label(status, locale)}: {num(count, locale)}")
        if statuses:
            doc.paragraph(" · ".join(statuses))
    if ov.engine.reachable:
        doc.footer(
            catalog.t(
                "admin.overview.engine_ok",
                locale,
                version=ov.engine.version,
                api=ov.engine.api_version,
                health=ov.engine.health_status,
                ready=ov.engine.ready_status,
            )
        )
    doc.footer(
        " · ".join(
            [
                catalog.t("admin.overview.uptime", locale)
                + f": {format_duration(ov.uptime_seconds, locale)}",
                catalog.t("admin.overview.active_24h", locale)
                + f": {num(ov.active_24h, locale)}",
                catalog.t(
                    "admin.overview.queue",
                    locale,
                    state=catalog.t(f"admin.queue.{ov.queue_state}", locale),
                ),
            ]
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
                    admin_cb.section(section.key),
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
