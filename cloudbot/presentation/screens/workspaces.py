"""Workspace screens: list, detail, create wizard, lifecycle confirms/results."""

from __future__ import annotations

from datetime import datetime

from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.entities import Workspace
from cloudbot.domain.value_objects import Limits, WorkspaceStatus
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_bytes,
    format_bytes_pair,
    format_count,
    format_datetime,
    format_duration,
    format_percent,
    format_relative,
    localize_digits,
)
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import display_state
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, fragment, italic
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen

PAGE_SIZE = 10


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


# -- list --------------------------------------------------------------------
def workspace_list(
    workspaces: list[Workspace],
    locale: Locale,
    *,
    page: int = 0,
    total_pages: int = 1,
    has_more: bool = False,
    inside: Workspace | None = None,
    confirmation: str | None = None,
) -> Screen:
    if not workspaces:
        return empty_workspaces(locale, inside=inside)

    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.list.title", locale))
    if confirmation:
        doc.blockquote(_raw(confirmation))
    doc.p(format_count("count.workspaces", len(workspaces), locale))
    rows = []
    for ws in workspaces:
        label_key, emoji = display_state(ws.status, ws.summary.state)
        status = f"{emoji} {catalog.t(label_key, locale)}"
        runs = (
            catalog.t("ws.runs.active", locale, n=_num(ws.summary.runs_active, locale))
            if ws.summary.runs_active
            else catalog.t("ws.runs.none", locale)
        )
        disk = format_bytes_pair(ws.summary.disk_used_bytes, ws.summary.disk_limit_bytes, locale)
        rows.append([ws.name, status, runs, disk])
    doc.table(
        [
            catalog.t("ws.column.name", locale),
            catalog.t("ws.column.status", locale),
            catalog.t("ws.column.runs", locale),
            catalog.t("ws.column.disk", locale),
        ],
        rows,
    )
    doc.p(italic(catalog.t("ws.page", locale, page=_num(page + 1, locale), total=_num(total_pages, locale))))

    kb_rows = []
    for ws in workspaces:
        kb_rows.append(
            [inline(catalog.t("ws.open", locale, name=ws.name), _cb("ws:view", ws.id), intent=Intent.PRIMARY)]
        )
    kb_rows.append([inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)])
    if has_more:
        kb_rows.append(
            [inline(catalog.t("common.load_more", locale), _cb("ws:list", page + 1), intent=Intent.PRIMARY)]
        )
    # The shared bottom nav block, with Workspaces muted (we are already here).
    kb_rows += nav.nav_rows(locale, "workspaces", inside=inside)
    return Screen(doc=doc, markup=inline_markup(kb_rows))


def empty_workspaces(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.list.empty_title", locale))
    doc.p(catalog.t("ws.list.empty_line1", locale))
    doc.p(catalog.t("ws.list.empty_line2", locale))
    rows = [[inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)]]
    rows += nav.nav_rows(locale, "workspaces", inside=inside)
    return Screen(doc=doc, markup=inline_markup(rows))


# -- detail ------------------------------------------------------------------
def workspace_detail(
    ws: Workspace,
    locale: Locale,
    *,
    now: datetime | None = None,
    active_run: bool = False,
    confirmation: str | None = None,
    mode: RunMode = RunMode.PRODUCTION,
    session: bool = True,
) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, f"📦 {ws.name}")
    label_key, _emoji = display_state(ws.status, ws.summary.state)
    doc.p(_raw(f"<b>{catalog.t('ws.detail.status', locale)}</b> {catalog.t(label_key, locale)}"))

    cpu = ws.limits.cpu_cores
    cpu_line = (
        catalog.t("ws.cpu.cores", locale, n=_num(1, locale))
        if cpu == 1
        else catalog.t("ws.cpu.cores_plural", locale, n=_num(int(cpu), locale))
    )
    if now is None:
        from datetime import timezone

        now = datetime.now(timezone.utc)
    disk = format_bytes_pair(
        ws.summary.disk_used_bytes,
        ws.summary.disk_limit_bytes,
        locale,
        percent=_disk_percent(ws),
    )
    doc.kv_table(
        [
            (catalog.t("ws.detail.runtime", locale), "python3"),
            (catalog.t("ws.detail.profile", locale), ws.profile),
            (catalog.t("ws.detail.cpu", locale), _raw(cpu_line)),
            (catalog.t("ws.detail.memory", locale), format_bytes(ws.limits.memory_mb * 1024 * 1024, locale)),
            (catalog.t("ws.detail.disk", locale), disk),
            (
                catalog.t("ws.detail.runs", locale),
                catalog.t(
                    "ws.detail.runs_line",
                    locale,
                    total=_num(ws.summary.runs_total, locale),
                    active=_num(ws.summary.runs_active, locale),
                ),
            ),
            (catalog.t("ws.detail.last_activity", locale), format_relative(ws.last_activity_at, now, locale)),
            (catalog.t("ws.detail.created", locale), format_datetime(ws.created_at, locale)),
        ]
    )

    if ws.status is WorkspaceStatus.LOCKED:
        doc.blockquote(fragment(catalog.t("ws.locked_banner", locale).replace("\n", " ")))
    if active_run:
        doc.p(italic(catalog.t("ws.run_active_caption", locale)))

    doc.div()
    doc.p(_raw(f"<b>{catalog.t('ws.detail.id', locale)}</b> <code>{ws.id}</code>"))
    if session:
        doc.p(italic(catalog.t("ws.session.inside", locale)))
    if confirmation:
        doc.blockquote(_raw(confirmation))

    return Screen(
        doc=doc,
        markup=workspace_detail_markup(ws, locale, active_run=active_run, session=session),
    )


def workspace_detail_markup(
    ws: Workspace, locale: Locale, *, active_run: bool = False, session: bool = True
):
    ws_id = ws.id
    rows: list[list] = []
    if ws.status is WorkspaceStatus.ACTIVE:
        rows.append(
            [
                inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
                inline(
                    catalog.t("ws.run", locale),
                    _cb("run:choose", ws_id),
                    intent=Intent.SUCCESS if not active_run else Intent.MUTED,
                    disabled=active_run,
                ),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.runs", locale), _cb("run:history", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("ws.terminals", locale), _cb("term:list", ws_id), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.packages", locale), _cb("pkg:list", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("ws.public_links", locale), _cb("tnl:list", ws_id), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.ports", locale), _cb("port:list", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("ws.processes", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.metrics", locale), _cb("met:view", ws_id), intent=Intent.PRIMARY),
                inline(catalog.t("ws.archives", locale), _cb("arc:menu", ws_id), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.suspend", locale), _cb("ws:suspend_confirm", ws_id), intent=Intent.PRIMARY),
            ]
        )
    elif ws.status is WorkspaceStatus.SUSPENDED:
        rows.append(
            [
                inline(catalog.t("ws.resume", locale), _cb("ws:resume", ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.run", locale), _cb("run:choose", ws_id), intent=Intent.MUTED, disabled=True),
                inline(catalog.t("ws.terminals", locale), _cb("term:list", ws_id), intent=Intent.MUTED, disabled=True),
            ]
        )
    elif ws.status is WorkspaceStatus.LOCKED:
        rows.append(
            [
                inline(catalog.t("ws.renew", locale), _cb("ws:renew", ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
            ]
        )
        rows.append(
            [
                inline(catalog.t("ws.run", locale), _cb("run:choose", ws_id), intent=Intent.MUTED, disabled=True),
                inline(catalog.t("ws.terminals", locale), _cb("term:list", ws_id), intent=Intent.MUTED, disabled=True),
            ]
        )
    else:
        rows.append([inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY)])

    if session:
        rows.append(
            [inline(catalog.t("ws.logout", locale), _cb("ws:logout", ws_id), intent=Intent.PRIMARY)]
        )
    rows.append(
        [
            inline(catalog.t("ws.stop_all", locale), _cb("ws:stop_confirm", ws_id), intent=Intent.DANGER),
            inline(catalog.t("ws.delete", locale), _cb("ws:delete_confirm", ws_id), intent=Intent.DANGER),
        ]
    )
    # The workspace home has exactly one *exit* — the explicit log-out above — and
    # no redundant bottom "Back" row: leaving and going "back" to the list were the
    # same destination. Switching workspaces (without leaving the session first) is
    # the shared nav block's job, so it sits last, at the bottom, as everywhere else.
    if session:
        rows += nav.nav_rows(locale, "workspace_home", inside=ws)
    return inline_markup(rows)


def _th(label: str) -> Html:
    return _raw(f"<th>{label}</th>")


def _kv(label: str, value) -> Html:
    """A key/value row as one HTML fragment (avoids th/td nesting surprises)."""
    inner = str(value) if isinstance(value, Html) else _raw(str(value))
    return _raw(f"<tr><th>{label}</th><td>{inner}</td></tr>")


def _disk_percent(ws: Workspace) -> float | None:
    if ws.summary.disk_limit_bytes <= 0:
        return None
    return ws.summary.disk_used_bytes / ws.summary.disk_limit_bytes * 100


# -- create wizard -----------------------------------------------------------
def create_name_prompt(locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.create.title", locale))
    doc.p(catalog.t("ws.create.prompt", locale))
    doc.p(italic(catalog.t("ws.create.hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def create_profile(locales: list[tuple[str, bool]], locale: Locale, default: str) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.create.profile_title", locale))
    doc.p(catalog.t("ws.create.profile_body", locale))
    rows = []
    for name, is_default in locales:
        label = f"✓ {name}" if is_default else name
        icon = "🐍 " if name == "python" else "📦 "
        intent = Intent.SUCCESS if is_default else Intent.PRIMARY
        rows.append([inline(f"{icon}{label}", _cb("ws:profile", name), intent=intent)])
    rows.append([inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


def create_advanced(
    limits: Limits,
    maxima: Limits,
    locale: Locale,
    *,
    selected_profile: str,
    allow_net: bool | None = None,
    allow_tunnels: bool | None = None,
) -> Screen:
    """Advanced create options: numeric limits plus the two boolean switches.

    ``allow_net`` and ``allow_tunnels`` were already on the domain ``Limits``;
    this screen makes them reachable (a user could not enable publishing at all
    before). Each is a toggle button whose callback flips the value in the wizard
    state and re-renders this screen.
    """
    net = limits.allow_net if allow_net is None else allow_net
    tunnels = limits.allow_tunnels if allow_tunnels is None else allow_tunnels

    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.create.advanced_title", locale))
    cpu_line = catalog.t(
        "ws.create.cpu_value",
        locale,
        n=_num(int(limits.cpu_cores), locale),
        max=_num(int(maxima.cpu_cores), locale),
    )
    doc.table(
        None,
        [
            [_th(catalog.t("ws.detail.cpu", locale)), _raw(cpu_line)],
            [
                _th(catalog.t("ws.detail.memory", locale)),
                _raw(
                    catalog.t(
                        "ws.create.limit_value",
                        locale,
                        n=_num(limits.memory_mb, locale),
                        unit="MB",
                        max=_num(maxima.memory_mb, locale),
                    )
                ),
            ],
            [
                _th(catalog.t("ws.detail.disk", locale)),
                _raw(
                    catalog.t(
                        "ws.create.limit_value",
                        locale,
                        n=_num(limits.disk_mb, locale),
                        unit="MB",
                        max=_num(maxima.disk_mb, locale),
                    )
                ),
            ],
            [
                _th(catalog.t("ws.create.network_label", locale)),
                catalog.t("ws.create.network_on" if net else "ws.create.network_off", locale),
            ],
            [
                _th(catalog.t("ws.create.tunnels_label", locale)),
                catalog.t("ws.create.network_on" if tunnels else "ws.create.network_off", locale),
            ],
        ],
    )
    doc.p(italic(catalog.t("ws.create.advanced_hint", locale)))

    def _toggle(active: bool, label: str, action: str):
        text = catalog.t(
            "ws.create.toggle_on" if active else "ws.create.toggle_off",
            locale,
            label=label,
        )
        return inline(text, action, intent=Intent.SUCCESS if active else Intent.MUTED)

    markup = inline_markup(
        [
            [
                _toggle(net, catalog.t("ws.create.network_label", locale), _cb("ws:tgl", "net")),
                _toggle(tunnels, catalog.t("ws.create.tunnels_label", locale), _cb("ws:tgl", "tnl")),
            ],
            [
                inline(catalog.t("common.create", locale), _cb("ws:create", selected_profile), intent=Intent.SUCCESS),
                inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY),
            ],
        ]
    )
    return Screen(doc=doc, markup=markup)


def wizard_limits(defaults: Limits, data: dict) -> Limits:
    """Build the create wizard's limits from the FSM data, defaulting safely.

    Only the two boolean switches are user-toggled today; the numeric values come
    from the engine's ``limits_defaults``. ``allow_tunnels`` is shown here so a
    user can actually enable publishing at create time.
    """
    return Limits(
        cpu_cores=defaults.cpu_cores,
        memory_mb=defaults.memory_mb,
        disk_mb=defaults.disk_mb,
        max_wall_time_sec=defaults.max_wall_time_sec,
        max_runs=defaults.max_runs,
        allow_net=bool(data.get("ws_allow_net", defaults.allow_net)),
        allow_unlimited_runs=defaults.allow_unlimited_runs,
        allow_tunnels=bool(data.get("ws_allow_tunnels", defaults.allow_tunnels)),
        write_rate_mb_s=defaults.write_rate_mb_s,
    )


# -- lifecycle confirms / results -------------------------------------------
def delete_confirm(ws: Workspace, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.delete.title", locale))
    doc.p(_raw(catalog.t("ws.delete.body", locale, name=ws.name)))
    doc.blockquote(catalog.t("ws.delete.warning", locale))
    markup = inline_markup(
        [
            [
                inline(
                    catalog.t("common.delete_permanently", locale),
                    _cb("ws:delete", ws.id),
                    intent=Intent.DANGER,
                ),
                inline(catalog.t("common.cancel", locale), _cb("ws:view", ws.id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def stop_confirm(ws: Workspace, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.stop.title", locale))
    doc.p(_raw(catalog.t("ws.stop.body", locale, name=ws.name)))
    doc.p(italic(catalog.t("ws.stop.hint", locale)))
    markup = inline_markup(
        [
            [
                inline(catalog.t("ws.stop_all", locale), _cb("ws:stop", ws.id), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("ws:view", ws.id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def suspend_confirm(ws: Workspace, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.suspend.title", locale))
    doc.p(_raw(catalog.t("ws.suspend.body", locale, name=ws.name)))
    doc.p(italic(catalog.t("ws.suspend.hint", locale)))
    markup = inline_markup(
        [
            [
                inline(catalog.t("ws.suspend", locale), _cb("ws:suspend", ws.id), intent=Intent.PRIMARY),
                inline(catalog.t("common.cancel", locale), _cb("ws:view", ws.id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def lifecycle_result(
    ws: Workspace,
    locale: Locale,
    *,
    kind: str,
    stopped_run_count: int = 0,
    stopped_session_count: int = 0,
    stopped_process_count: int = 0,
    changed: bool = True,
) -> Screen:
    summary = lifecycle_summary(
        locale,
        stopped_run_count=stopped_run_count,
        stopped_session_count=stopped_session_count,
        stopped_process_count=stopped_process_count,
        changed=changed,
    )
    if kind == "stop":
        return workspace_detail(ws, locale, confirmation=catalog.t("ws.stop.done", locale, summary=summary))
    if kind == "suspend":
        return workspace_detail(ws, locale, confirmation=catalog.t("ws.suspend.done", locale, name=ws.name))
    if kind == "resume":
        return workspace_detail(ws, locale, confirmation=catalog.t("ws.resume.done", locale, name=ws.name))
    return workspace_detail(ws, locale)


def lifecycle_summary(
    locale: Locale,
    *,
    stopped_run_count: int,
    stopped_session_count: int,
    stopped_process_count: int,
    changed: bool = True,
) -> str:
    if (
        stopped_run_count == 0
        and stopped_session_count == 0
        and stopped_process_count == 0
    ):
        if not changed:
            return catalog.t("ws.already_done", locale)
        return catalog.t("ws.stop.nothing", locale)
    parts = []
    if stopped_run_count:
        parts.append(catalog.t("lifecycle.stopped_run", locale, n=_num(stopped_run_count, locale)))
    if stopped_session_count:
        parts.append(catalog.t("lifecycle.stopped_session", locale, n=_num(stopped_session_count, locale)))
    if stopped_process_count:
        parts.append(catalog.t("lifecycle.stopped_process", locale, n=_num(stopped_process_count, locale)))
    joiner = " " if locale is Locale.EN else " "
    return joiner.join(parts)
