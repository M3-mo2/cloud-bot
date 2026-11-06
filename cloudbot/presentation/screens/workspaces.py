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
from cloudbot.presentation.pagination import PAGE_SIZE as _PAGE_SIZE
from cloudbot.presentation.pagination import footer as page_footer
from cloudbot.presentation.pagination import pager_rows as _pager_rows
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, fragment, italic
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen

#: Kept as a module-level alias (one project-wide number now lives in pagination).
PAGE_SIZE = _PAGE_SIZE


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
    doc.p(page_footer(page, total_pages, locale))

    kb_rows = []
    for ws in workspaces:
        kb_rows.append(
            [inline(catalog.t("ws.open", locale, name=ws.name), _cb("ws:view", ws.id), intent=Intent.PRIMARY)]
        )
    kb_rows.append([inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)])
    kb_rows += _pager_rows(page, total_pages, lambda target: _cb("ws:list", target), locale)
    # The shared bottom nav block. We are on the workspaces list, so its own
    # destination (Workspaces, and Menu per R2) is dropped rather than muted.
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
    more: bool = False,
) -> Screen:
    """The workspace home: it leads with *state*, not with a specification sheet.

    Opening a workspace should answer three questions in order — which workspace is
    this, what state is it in, what can I do here — and then stop. The full
    specification (runtime, profile, CPU, memory, disk, run counters, timestamps,
    identifier) is genuine information that is simply not what the screen is *for*,
    so it sits behind a collapsed ``<details>`` block: one tap away, and out of the
    way of the visits that do not need it.

    ``more`` reveals the secondary tool destinations in the keyboard; the body never
    depends on it, so the screen reads the same however the user reached it.
    """

    doc = RichDoc(locale)
    doc.h(2, f"📦 {ws.name}")
    label_key, _emoji = display_state(ws.status, ws.summary.state)
    doc.p(_raw(f"<b>{catalog.t('ws.detail.status', locale)}</b> {catalog.t(label_key, locale)}"))

    # Transient, actionable state first: a locked plan, a run already going. These
    # change what the user can do, so they belong above any reference material.
    if ws.status is WorkspaceStatus.LOCKED:
        doc.blockquote(fragment(catalog.t("ws.locked_banner", locale).replace("\n", " ")))
    if active_run:
        doc.p(italic(catalog.t("ws.run_active_caption", locale)))
    if session:
        doc.p(italic(catalog.t("ws.session.inside", locale)))
    if confirmation:
        doc.blockquote(_raw(confirmation))

    if now is None:
        from datetime import timezone

        now = datetime.now(timezone.utc)

    cpu = ws.limits.cpu_cores
    cpu_line = (
        catalog.t("ws.cpu.cores", locale, n=_num(1, locale))
        if cpu == 1
        else catalog.t("ws.cpu.cores_plural", locale, n=_num(int(cpu), locale))
    )
    disk = format_bytes_pair(
        ws.summary.disk_used_bytes,
        ws.summary.disk_limit_bytes,
        locale,
        percent=_disk_percent(ws),
    )

    # Reference material, collapsed. Built as one raw fragment because ``kv_table``
    # appends to the document rather than producing a value to nest inside a block.
    rows = [
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
        (catalog.t("ws.detail.id", locale), _raw(f"<code>{ws.id}</code>")),
    ]
    body = "".join(str(_kv(k, v)) for k, v in rows)
    doc.div()
    doc.raw(
        f'<details><summary>{catalog.t("ws.detail.tech", locale)}</summary>'
        f'<table is_bordered="true">{body}</table></details>'
    )

    return Screen(
        doc=doc,
        markup=workspace_detail_markup(ws, locale, active_run=active_run, session=session, more=more),
    )


#: Tool destinations behind "More", in display order. Each is (label key, callback).
#: These are the screens a user reaches less often than files/runs/terminals, so they
#: are the ones worth collapsing rather than the ones worth deleting.
_EXTRA_TOOLS: tuple[tuple[str, str], ...] = (
    ("ws.packages", "pkg:list"),
    ("ws.public_links", "tnl:list"),
    ("ws.ports", "port:list"),
    ("ws.processes", "proc:list"),
    ("ws.metrics", "met:view"),
    ("ws.archives", "arc:menu"),
)


def workspace_detail_markup(
    ws: Workspace,
    locale: Locale,
    *,
    active_run: bool = False,
    session: bool = True,
    more: bool = False,
):
    ws_id = ws.id
    rows: list[list] = []

    def _more_row(expanded: bool) -> list:
        label = catalog.t("ws.less" if expanded else "ws.more", locale)
        action = _cb("ws:less" if expanded else "ws:more", ws_id)
        return [inline(label, action, intent=Intent.PRIMARY)]

    if ws.status is WorkspaceStatus.ACTIVE:
        # What a user does on every visit: their files, and running something.
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
        if more:
            extra = [
                inline(catalog.t(key, locale), _cb(action, ws_id), intent=Intent.PRIMARY)
                for key, action in _EXTRA_TOOLS
            ]
            rows += [extra[i : i + 2] for i in range(0, len(extra), 2)]
        rows.append(_more_row(more))
        rows.append(
            [inline(catalog.t("ws.suspend", locale), _cb("ws:suspend_confirm", ws_id), intent=Intent.PRIMARY)]
        )
    elif ws.status is WorkspaceStatus.SUSPENDED:
        rows.append(
            [
                inline(catalog.t("ws.resume", locale), _cb("ws:resume", ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
            ]
        )
        # Deliberately present but disabled: "not now, resume first" is information.
        # A user shown nothing here is left wondering why the screen looks different.
        rows.append(
            [
                inline(catalog.t("ws.run", locale), _cb("run:choose", ws_id), intent=Intent.MUTED, disabled=True),
                inline(
                    catalog.t("ws.terminals", locale),
                    _cb("term:list", ws_id),
                    intent=Intent.MUTED,
                    disabled=True,
                ),
            ]
        )
    elif ws.status is WorkspaceStatus.LOCKED:
        rows.append(
            [
                inline(catalog.t("ws.renew", locale), _cb("ws:renew", ws_id), intent=Intent.SUCCESS),
                inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
            ]
        )
    else:
        rows.append([inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY)])

    if session:
        rows.append(
            [inline(catalog.t("ws.logout", locale), _cb("ws:logout", ws_id), intent=Intent.PRIMARY)]
        )

    # Destructive actions last, after the exit, and never adjacent to a tool a user
    # taps repeatedly.
    if ws.status is WorkspaceStatus.ACTIVE:
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


def create_network(
    defaults: Limits,
    locale: Locale,
    *,
    allow_net: bool | None = None,
    allow_tunnels: bool | None = None,
) -> Screen:
    """The create flow's second step: the two network switches, then «متابعة».

    This replaces the old numeric "advanced" table. The plan decides cpu, memory
    and disk; this screen only carries the two booleans a plan cannot know. One
    linear path: toggles, «متابعة», or cancel.
    """
    net = defaults.allow_net if allow_net is None else allow_net
    tunnels = defaults.allow_tunnels if allow_tunnels is None else allow_tunnels

    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.create.network_title", locale))
    doc.p(catalog.t("ws.create.network_body", locale))
    doc.kv_table(
        [
            (
                catalog.t("ws.create.network_label", locale),
                catalog.t("ws.create.network_on" if net else "ws.create.network_off", locale),
            ),
            (
                catalog.t("ws.create.tunnels_label", locale),
                catalog.t("ws.create.network_on" if tunnels else "ws.create.network_off", locale),
            ),
        ]
    )

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
            [inline(catalog.t("ws.create.continue", locale), "ws:continue", intent=Intent.SUCCESS)],
            [inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)

def create_plan_picker(
    options: tuple,
    balance: int,
    locale: Locale,
    *,
    workspace_name: str = "",
    needed_plan: str | None = None,
) -> Screen:
    """The plan step the create flow was missing.

    Each row says the period, the price and the allowance the plan grants — the
    plan is the source of the workspace's limits. The trial (when it is still
    available) reads differently: free, once, 3 days. A short balance is not a
    dead end: the screen says exactly how many Stars are missing and offers the
    packs.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.create.plan_title", locale))
    if workspace_name:
        doc.p(_raw(catalog.t("ws.create.plan_for", locale, name=workspace_name)))
    doc.p(catalog.t("ws.create.plan_body", locale))

    if needed_plan is not None:
        needed = next((p for p in options if p.code == needed_plan), None)
        if needed is not None and balance < needed.amount_stars:
            missing = needed.amount_stars - balance
            doc.blockquote(
                fragment(
                    catalog.t(
                        "ws.create.plan_need_more",
                        locale,
                        needed=_num(needed.amount_stars, locale),
                        have=_num(balance, locale),
                        missing=_num(missing, locale),
                    )
                )
            )

    rows = []
    for plan in options:
        if plan.amount_stars == 0:
            price = catalog.t("ws.create.plan_free", locale)
            plan_label = catalog.t("plan.trial", locale)
        else:
            price = _stars(plan.amount_stars, locale)
            plan_label = catalog.t(f"plan.{plan.code}", locale)
        rows.append(
            [
                _raw(plan_label),
                _raw(catalog.t("wallet.plan.period", locale, days=_num(plan.period_days, locale))),
                _raw(price),
                _raw(
                    catalog.t(
                        "wallet.plan.allowance",
                        locale,
                        ws=_num(plan.max_workspaces, locale),
                        cpu=_num(int(plan.limits.cpu_cores), locale),
                        ram=_num(plan.limits.memory_mb, locale),
                        disk=_num(plan.limits.disk_mb, locale),
                    )
                ),
            ]
        )
    doc.table(
        [
            catalog.t("wallet.plan.col.plan", locale),
            catalog.t("wallet.plan.col.period", locale),
            catalog.t("wallet.plan.col.price", locale),
            catalog.t("wallet.plan.col.includes", locale),
        ],
        rows,
    )
    doc.p(_raw(catalog.t("ws.create.plan_balance", locale, stars=_stars(balance, locale))))

    kb: list[list] = []
    for plan in options:
        mark = " ✓" if plan.code == needed_plan else ""
        if plan.amount_stars == 0:
            label = catalog.t("ws.create.plan_trial_button", locale) + mark
        else:
            label = catalog.t(
                "ws.create.plan_buy_button",
                locale,
                plan=catalog.t(f"plan.{plan.code}", locale),
                stars=_num(plan.amount_stars, locale),
            ) + mark
        kb.append([inline(label, _cb("ws:plan", plan.code), intent=Intent.SUCCESS)])
    short = needed_plan is not None and not any(
        p.code == needed_plan and balance >= p.amount_stars for p in options
    )
    if short:
        kb.append([inline(catalog.t("wallet.buy_stars", locale), "ws:packs", intent=Intent.SUCCESS)])
    kb.append([inline(catalog.t("common.back", locale), "ws:back_net", intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))

def wizard_network(defaults: Limits, data: dict) -> Limits:
    """The network toggles from the wizard FSM data, defaulting to the engine's."""
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

def _stars(n: int, locale: Locale) -> str:
    return catalog.t("wallet.stars", locale, n=_num(n, locale))

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
