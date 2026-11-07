"""Admin section: one user's page."""

from __future__ import annotations

from cloudbot.domain.admin import AdminView, UserPage
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.domain.events import EventRecord
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_bytes_pair,
    format_datetime,
    format_percent,
    format_relative,
)
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import display_state
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, bold, code, fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import num
from cloudbot.presentation.screens.admin.actions import action, action_button
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def _style(intent: Intent) -> str | None:
    """The block-button style for an action's intent (``None`` = app default)."""
    return {
        Intent.DANGER: "danger",
        Intent.SUCCESS: "success",
        Intent.PRIMARY: "primary",
    }.get(intent)


def _facts(wu, view, locale: Locale, sub, now) -> str:
    """One line of what matters about a workspace: plan, then disk.

    The plan is what he asked to see — and it is the *subscription's* line, not the
    engine's ``summary.state``, whose ``unlimited`` reads «بلا انتهاء» to a user who
    pays by the week. Composed here (presentation) from domain data, with the same
    ``plan_state_short`` the wallet and the menu print, so the three cannot drift.
    Disk is the second fact because it is the one that fills up and surprises people.
    """
    from datetime import datetime, timezone

    from cloudbot.domain.subscription import derive_status
    from cloudbot.presentation.screens.wallet import plan_state_short

    ws = wu.workspace
    if wu.usage is not None:
        disk = format_bytes_pair(
            wu.usage.disk_bytes, wu.usage.disk_limit_bytes, locale, percent=wu.usage.disk_percent
        )
    else:
        disk = catalog.t("admin.user.usage_unknown", locale)

    moment = now or datetime.now(timezone.utc)
    plan = plan_state_short(sub, derive_status(sub, moment), locale)
    if str(ws.id) in getattr(view, "workspace_bans", {}):
        plan = catalog.t("admin.user.ws_banned", locale, reason=view.workspace_bans[str(ws.id)] or "—")
    return f"{plan} · {disk}"


def _raw(text: str) -> Html:
    """Trusted HTML fragment built here (the status/usage line)."""
    return Html(text)


def user_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    page: UserPage | None = view.user_page
    if page is None:
        doc.h(2, catalog.t("admin.user.missing_title", locale))
        doc.p(catalog.t("admin.user.missing_body", locale))
        return Screen(
            doc=doc,
            markup=inline_markup(
                [[inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
            ),
        )

    u = page.user
    doc.h(2, catalog.t("admin.user.title", locale))
    doc.kv_table(
        [
            (catalog.t("admin.user.id", locale), code(str(u.tg_id))),
            (catalog.t("admin.user.name", locale), u.display_name or "—"),
            (catalog.t("admin.user.locale", locale), catalog.t(f"lang.name.{u.locale.value}", locale)),
            (catalog.t("admin.user.plan", locale), catalog.t(f"plan.{u.plan}", locale)),
            (catalog.t("admin.user.created", locale), format_datetime(u.created_at, locale)),
            (
                catalog.t("admin.user.last_seen", locale),
                format_relative(u.last_seen_at, view.now, locale)
                if u.last_seen_at and view.now is not None
                else "—",
            ),
        ]
    )

    # The ban is a state, shown here and reversible in one tap.
    if view.user_ban is not None:
        doc.blockquote(
            fragment(catalog.t("admin.user.banned", locale, reason=view.user_ban.reason or "—"))
        )

    doc.div()

    # The keyboard below now carries only the account-level actions at the end of
    # this screen; each workspace's own buttons are in-body (see below).
    kb: list[list] = []

    # -- workspaces: facts, then **that workspace's own buttons right under it** --
    # The buttons used to live in the keyboard below everything, so a page with
    # four workspaces had four identical `[دخول] [إيقاف] [حذف] [حظر]` row-pairs at
    # the bottom and no way to tell which belonged to which workspace. In-body
    # buttons (Bot API 10.3 blocks) let each set sit under the workspace it acts on.
    doc.heading(
        f"✦ {catalog.t('admin.user.workspaces', locale)} ⟸ "
        f"{num(len(page.workspaces), locale)}",
        size=2,
    )
    if not page.workspaces:
        doc.paragraph(catalog.t("admin.user.no_workspaces", locale))
    else:
        for wu in page.workspaces:
            ws = wu.workspace
            subject = str(ws.id)
            sub = page.workspace_subscriptions.get(subject)

            doc.heading(ws.name, size=3)
            doc.paragraph(_facts(wu, view, locale, sub, view.now))

            # This workspace's own actions, one row, right under its facts.
            row: list[tuple[str, str, str | None]] = []
            enter = action("enter")
            if enter is not None:
                row.append((enter.label(locale), enter.ask_callback(subject), _style(enter.intent)))
            state_act = action("suspend" if ws.status is WorkspaceStatus.ACTIVE else "resume")
            if state_act is not None:
                row.append((state_act.label(locale), state_act.ask_callback(subject), _style(state_act.intent)))
            if row:
                doc.buttons(row)

            # Destructive actions on their own row: never beside the one a hand taps
            # by habit.
            danger: list[tuple[str, str, str | None]] = []
            delete = action("delete")
            if delete is not None:
                danger.append((delete.label(locale), delete.ask_callback(subject), _style(delete.intent)))
            if subject in view.workspace_bans:
                danger.append(
                    (catalog.t("admin.user.ws_unban", locale), admin_cb.ws_unban(subject), "success")
                )
            else:
                danger.append(
                    (catalog.t("admin.user.ws_ban", locale), admin_cb.ws_ban(subject), "danger")
                )
            doc.buttons(danger)

    doc.div()
    _recent_activity(doc, view, locale)
    kb.append(
        [
            inline(
                catalog.t("admin.user.audit", locale),
                admin_cb.audit_user(u.tg_id, 0),
                intent=Intent.PRIMARY,
            ),
            inline(
                catalog.t("admin.wallet.title", locale),
                admin_cb.wallet(u.tg_id),
                intent=Intent.SUCCESS,
            ),
        ]
    )
    # Moderation: unban (one tap) or ban, and message this user right here.
    if view.user_ban is not None:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.unban", locale),
                    admin_cb.user_unban(u.tg_id),
                    intent=Intent.SUCCESS,
                )
            ]
        )
    else:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.ban", locale),
                    admin_cb.user_ban(u.tg_id),
                    intent=Intent.DANGER,
                ),
                inline(
                    catalog.t("admin.user.message", locale),
                    admin_cb.user_msg(u.tg_id),
                    intent=Intent.PRIMARY,
                ),
            ]
        )
    if view.user_ban is not None:
        kb.append(
            [
                inline(
                    catalog.t("admin.user.message", locale),
                    admin_cb.user_msg(u.tg_id),
                    intent=Intent.PRIMARY,
                )
            ]
        )
    # The notification scope for this user, if the admin wants to watch only them.
    if view.notify_scope == u.tg_id:
        scope_label = catalog.t("admin.user.scope_clear", locale)
    else:
        scope_label = catalog.t("admin.user.scope_set", locale)
    kb.append([inline(scope_label, admin_cb.notify_scope(u.tg_id), intent=Intent.PRIMARY)])
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.section("users"), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _recent_activity(doc: RichDoc, view: AdminView, locale) -> None:
    doc.div()
    doc.p(
        fragment(
            [
                bold(f"✦ {catalog.t('admin.user.recent', locale)} ⟸ "),
                bold(num(len(view.recent_events), locale)),
            ]
        )
    )
    events: list[EventRecord] = view.recent_events
    if not events:
        doc.p(italic(catalog.t("admin.user.no_activity", locale)))
        return
    # One line per event: a bullet, the *what* in bold, then the *when* in
    # parentheses. A three-column table put the timestamp in its own column, which
    # in a right-to-left message is the first thing a reader's eye lands on — and
    # the least useful. The sentence reads in the order a person asks it.
    doc.ul(
        [
            fragment(
                [
                    bold(catalog.t(e.summary_key, locale, **e.params)),
                    f" — {catalog.t(e.category.label_key, locale)}",
                    f" ({format_datetime(e.ts, locale)})",
                ]
            )
            for e in events
        ]
    )


register(
    AdminSection(
        key="user",
        title_key="admin.section.user",
        order=20,
        icon="👤",
        build=user_screen,
        in_menu=False,
    )
)
