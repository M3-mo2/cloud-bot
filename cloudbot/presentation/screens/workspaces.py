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
from cloudbot.presentation.rich import tags
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
#: How many workspace buttons one inline row carries. Two keeps a long name
#: readable on a phone; the API allows up to eight, and using eight here would
#: make every label a two-word abbreviation.
_LIST_COLS = 2


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
    """The workspaces list, as **buttons inside the message** (Bot API 10.3).

    The old screen was a four-column table — name, state, runs, disk — and in a
    right-to-left message that reads as numbers drifting away from their labels.
    The list now carries **no table at all**: a rule, one line of state, then one
    button per workspace, so the name *is* the thing you press and there is nothing
    for the eye to re-align.

    The buttons are inline ``buttons`` blocks rather than a keyboard below the
    message: a press still arrives as an ordinary ``CallbackQuery``, so the whole
    routing layer is untouched. Telegram refuses ``<tg-button callback_data=...>``
    in the html form, which is why this screen builds blocks (measured).
    """
    if not workspaces:
        return empty_workspaces(locale, inside=inside)

    doc = RichDoc(locale)
    rule = catalog.t("ws.head.rule", locale)

    # Blocks carry plain RichText, not html: a tag in a paragraph prints literally.
    # The heading block is what gives the title its weight, so no <b> is needed.
    doc.paragraph(rule)
    doc.heading(f"✦ {catalog.t('ws.list.title', locale).replace('📦 ', '')} ⟸ "
                f"{format_count('count.workspaces', len(workspaces), locale)}")
    doc.paragraph(rule)

    if confirmation:
        doc.paragraph(confirmation)

    # One button per workspace, two to a row: the list itself is the navigation, so
    # there is no table for a right-to-left message to mis-align.
    row: list[tuple[str, str, str | None]] = []
    for ws in workspaces:
        row.append((ws.name, _cb("ws:view", ws.id), "primary"))
        if len(row) == _LIST_COLS:
            doc.buttons(row)
            row = []
    new_btn = (catalog.t("menu.new_workspace", locale), "ws:new", "success")
    doc.buttons(row + [new_btn] if row else [new_btn])

    if total_pages > 1:
        nav_row: list[tuple[str, str, str | None]] = []
        if page > 0:
            nav_row.append((catalog.t("common.prev", locale), _cb("ws:list", page - 1), None))
        if page + 1 < total_pages:
            nav_row.append((catalog.t("common.next", locale), _cb("ws:list", page + 1), None))
        if nav_row:
            doc.buttons(nav_row, align="center")
        doc.footer(page_footer(page, total_pages, locale))

    # The keyboard below carries only what is not a workspace: the shared nav block.
    kb_rows = nav.nav_rows(locale, "workspaces", inside=inside)
    return Screen(doc=doc, markup=inline_markup(kb_rows) if kb_rows else None)


def empty_workspaces(locale: Locale, *, inside: Workspace | None = None) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.list.empty_title", locale))
    doc.p(catalog.t("ws.list.empty_line1", locale))
    doc.p(catalog.t("ws.list.empty_line2", locale))
    rows = [[inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)]]
    rows += nav.nav_rows(locale, "workspaces", inside=inside)
    return Screen(doc=doc, markup=inline_markup(rows))


# -- detail ------------------------------------------------------------------
def _age_label(created, now: datetime, locale: Locale) -> str:
    """How long this workspace has been alive, in his format: bold, coarse units.

    A workspace is created once and then runs, so "since" is measured from
    ``created_at`` and needs no plural table beyond what babel already gives us:
    minutes, hours, days, months, years. Under a minute reads "just now" rather
    than "0 min ago".
    """
    if created is None:
        return catalog.t("ws.head.just_now", locale)
    if created.tzinfo is None:
        from datetime import timezone as _tz

        created = created.replace(tzinfo=_tz.utc)
    secs = max(0, int((now - created).total_seconds()))

    if secs < 60:
        return catalog.t("ws.head.just_now", locale)

    steps: tuple[tuple[int, str], ...] = (
        (365 * 24 * 3600, "ws.head.years"),
        (30 * 24 * 3600, "ws.head.months"),
        (24 * 3600, "ws.head.days"),
        (3600, "ws.head.hours"),
    )
    for size, key in steps:
        if secs >= size:
            n = max(1, secs // size)
            return catalog.plural(key, n, locale)
    return catalog.plural("ws.head.minutes", max(1, secs // 60), locale)


def workspace_detail(
    ws: Workspace,
    locale: Locale,
    *,
    now: datetime | None = None,
    active_run: bool = False,
    confirmation: str | None = None,
    mode: RunMode = RunMode.PRODUCTION,
    session: bool = True,
    plan_state: str | None = None,
) -> Screen:
    """The workspace home: identity, age, then the specification on request.

    The header is three bold lines and a rule — the name, how long this workspace
    has been running, and nothing else. Everything a user does not act on sits
    behind the collapsed "technical details" table, which stays one tap away and
    out of the way of the visits that only want files or a run.

    The tool grid is flat: every destination is on the page, one shape always.
    There is no disclosure control — a hosting product's home shows the tools.
    """

    if now is None:
        from datetime import timezone

        now = datetime.now(timezone.utc)

    doc = RichDoc(locale)
    rule = tags.bold(catalog.t("ws.head.rule", locale))

    # -- the header: name and age, in bold, between two rules -----------------
    doc.p(rule)
    doc.p(
        tags.fragment(
            [
                tags.bold("✦ " + catalog.t("ws.head.title", locale) + " ⟸ "),
                tags.bold(ws.name),
            ]
        )
    )
    doc.p(
        tags.fragment(
            [
                tags.bold("✦ " + catalog.t("ws.head.since", locale) + " ⟸ "),
                _raw(_age_label(ws.created_at, now, locale)),
            ]
        )
    )
    doc.p(rule)

    # The status line answers the question a paying user actually has: is my plan
    # alive, and until when. It used to print the *engine's* own state, whose
    # ``unlimited`` value means "no run-time cap" — a fact about the sandbox, not
    # about the subscription — so every workspace read "بلا انتهاء". ``plan_state``
    # is the same derived string the list and the wallet print (one source).
    label_key, _emoji = display_state(ws.status, ws.summary.state)
    state_text = plan_state or catalog.t(label_key, locale)
    doc.p(_raw(f"<b>✦ {catalog.t('ws.detail.plan_label', locale)} ⟸ </b><b>{state_text}</b>"))

    # Transient, actionable state first: a locked plan, a run already going. These
    # change what the user can do, so they belong above any reference material.
    if ws.ban_reason:
        # An admin ban: the owner sees a locked workspace carrying the admin's own
        # reason. Distinct from a lapsed plan, and not liftable by the owner.
        doc.blockquote(
            fragment(catalog.t("ws.banned_banner", locale, reason=ws.ban_reason or "—"))
        )
    elif ws.status is WorkspaceStatus.LOCKED:
        doc.blockquote(fragment(catalog.t("ws.locked_banner", locale).replace("\n", " ")))
    if active_run:
        doc.p(italic(catalog.t("ws.run_active_caption", locale)))
    if confirmation:
        doc.blockquote(_raw(confirmation))

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

    # Reference material, collapsed. It is a table with a caption and two
    # section rows rather than one flat list of nine: nine undifferentiated
    # key/value lines are a wall, three labelled groups are scannable. The
    # identifier gets its own full-width row because it is long enough to wrap
    # and is not a value anyone compares against a label.
    sections: tuple[tuple[str, list[tuple[str, object]]], ...] = (
        (
            catalog.t("ws.detail.group.resources", locale),
            [
                (catalog.t("ws.detail.runtime", locale), "python3"),
                (catalog.t("ws.detail.profile", locale), ws.profile),
                (catalog.t("ws.detail.cpu", locale), _raw(cpu_line)),
                (catalog.t("ws.detail.memory", locale), format_bytes(ws.limits.memory_mb * 1024 * 1024, locale)),
                (catalog.t("ws.detail.disk", locale), disk),
            ],
        ),
        (
            catalog.t("ws.detail.group.activity", locale),
            [
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
            ],
        ),
    )

    rows_html: list[str] = []
    for title, pairs in sections:
        rows_html.append(f'<tr><th colspan="2">{title}</th></tr>')
        for label, value in pairs:
            inner = str(value) if isinstance(value, Html) else _raw(str(value))
            rows_html.append(f"<tr><th>{label}</th><td>{inner}</td></tr>")
    rows_html.append(f'<tr><th colspan="2">{catalog.t("ws.detail.group.identity", locale)}</th></tr>')
    rows_html.append(f'<tr><td colspan="2" align="center"><code>{ws.id}</code></td></tr>')

    doc.div()
    doc.raw(
        f"<details><summary><b>{catalog.t('ws.detail.tech', locale)}</b> ⇣</summary>"
        f'<table is_bordered="true" is_striped="true" is_compact="true">'
        f"<caption>{catalog.t('ws.detail.caption', locale)}</caption>"
        f"{''.join(rows_html)}</table></details>"
    )

    return Screen(
        doc=doc,
        markup=workspace_detail_markup(ws, locale, active_run=active_run, session=session),
    )


def workspace_settings(
    ws: Workspace,
    locale: Locale,
    *,
    confirmation: str | None = None,
) -> Screen:
    """Where a workspace is administered: pause it, stop everything, or delete it.

    These three used to sit on the home screen among the everyday tools, where the
    destructive pair crowded the actions a user actually came for. A workspace has
    one settings destination; it is the only place that owns them.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("ws.settings.title", locale))
    doc.p(_raw(catalog.t("ws.settings.intro", locale)))
    doc.p(tags.bold(catalog.t("ws.settings.rule", locale)))
    if confirmation:
        doc.blockquote(_raw(confirmation))

    rows: list[list] = []
    if ws.status is WorkspaceStatus.ACTIVE:
        rows.append(
            [
                inline(
                    catalog.t("ws.settings.stop_all", locale),
                    _cb("ws:stop_confirm", ws.id),
                    intent=Intent.DANGER,
                )
            ]
        )
        rows.append(
            [
                inline(
                    catalog.t("ws.settings.suspend", locale),
                    _cb("ws:suspend_confirm", ws.id),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    elif ws.status is WorkspaceStatus.SUSPENDED:
        rows.append(
            [inline(catalog.t("ws.resume", locale), _cb("ws:resume", ws.id), intent=Intent.SUCCESS)]
        )
    rows.append(
        [inline(catalog.t("ws.settings.delete", locale), _cb("ws:delete_confirm", ws.id), intent=Intent.DANGER)]
    )
    rows.append(
        [inline(catalog.t("ws.settings.back", locale), _cb("ws:view", ws.id), intent=Intent.PRIMARY)]
    )
    return Screen(doc=doc, markup=inline_markup(rows))


#: The tool grid on an active workspace home, in reading order, two per row.
#: Each entry is (label key, callback). The order is a user's day, not a
#: developer's list: everyday work first (files, run, archives, terminals, runs,
#: templates), then "what is my workspace doing" (packages, public links, ports),
#: then observation (processes, metrics). Settings is appended on its own last
#: row — pausing and deleting live there, and a destructive screen is never
#: adjacent to a button a hand taps by habit.
_TOOLS: tuple[tuple[str, str], ...] = (
    ("ws.files", "file:ls"),
    ("ws.run", "run:choose"),
    ("ws.archives", "arc:menu"),
    ("ws.terminals", "term:open"),
    ("ws.runs", "run:history"),
    ("ws.templates", "tpl:list"),
    ("ws.packages", "pkg:list"),
    ("ws.public_links", "tnl:list"),
    ("ws.ports", "port:list"),
    ("ws.processes", "proc:list"),
    ("ws.metrics", "met:view"),
)


def workspace_detail_markup(
    ws: Workspace,
    locale: Locale,
    *,
    active_run: bool = False,
    session: bool = True,
):
    """The language of this screen, in his order — one flat page, no toggle.

    Everyday tools first, then what the workspace is doing, then observation;
    Settings alone on the last row. Pausing, stopping everything and deleting
    left this grid entirely: they are administration, and they have their own
    screen — putting a destructive button among the buttons a user taps all day
    is how it gets tapped.
    """
    ws_id = ws.id
    rows: list[list] = []

    if ws.status is WorkspaceStatus.ACTIVE:
        # Two per row, in ``_TOOLS`` order; the odd count leaves Settings alone
        # rather than padding the row with a placeholder.
        tools = list(_TOOLS)
        # Starter templates write files into this workspace, so only the owner's
        # own home offers them: the admin's entered view (session=False) is a
        # read-only inspection surface, and writing starter files there would be
        # a change to an admin screen this task must not make.
        if not session:
            tools = [entry for entry in tools if entry[1] != "tpl:list"]

        def _tool_button(key: str, action: str):
            if action == "run:choose":
                return inline(
                    catalog.t(key, locale),
                    _cb(action, ws_id),
                    intent=Intent.SUCCESS if not active_run else Intent.MUTED,
                    disabled=active_run,
                )
            if action == "file:ls":
                return inline(catalog.t(key, locale), _cb(action, ws_id, 0), intent=Intent.PRIMARY)
            return inline(catalog.t(key, locale), _cb(action, ws_id), intent=Intent.PRIMARY)

        for index in range(0, len(tools), 2):
            rows.append([_tool_button(key, action) for key, action in tools[index : index + 2]])
        rows.append(
            [inline(catalog.t("ws.settings", locale), _cb("ws:settings", ws_id), intent=Intent.PRIMARY)]
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
                    _cb("term:open", ws_id),
                    intent=Intent.MUTED,
                    disabled=True,
                ),
            ]
        )
        rows.append(
            [inline(catalog.t("ws.settings", locale), _cb("ws:settings", ws_id), intent=Intent.PRIMARY)]
        )
    elif ws.status is WorkspaceStatus.LOCKED:
        if ws.ban_reason:
            # No renew, no resume: an admin must lift this ban.
            rows.append(
                [inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY)]
            )
        else:
            rows.append(
                [
                    inline(catalog.t("ws.renew", locale), _cb("ws:renew", ws_id), intent=Intent.SUCCESS),
                    inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY),
                ]
            )
        rows.append(
            [inline(catalog.t("ws.settings", locale), _cb("ws:settings", ws_id), intent=Intent.PRIMARY)]
        )
    else:
        rows.append([inline(catalog.t("ws.files", locale), _cb("file:ls", ws_id, 0), intent=Intent.PRIMARY)])
        rows.append(
            [inline(catalog.t("ws.settings", locale), _cb("ws:settings", ws_id), intent=Intent.PRIMARY)]
        )

    if session:
        rows.append(
            [inline(catalog.t("ws.logout", locale), _cb("ws:logout", ws_id), intent=Intent.PRIMARY)]
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
