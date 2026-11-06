"""The wallet and subscription screens.

Pure functions of money state + locale: no I/O, no clock, no Telegram. The
handlers build the read model (a :class:`WalletView`) and call one of these.

Copy rules followed here: the current plan is named *and* marked with "✓"; an
insufficient balance always says **how many Stars are needed**, not just that it
is insufficient; a trial is labelled a trial in the same place a paid plan is
labelled a plan, so it can never be mistaken for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cloudbot.core.locale import Locale
from cloudbot.domain import plans
from cloudbot.domain.billing import StarPack, StarsPayment, packs
from cloudbot.domain.subscription import (
    Movement,
    Subscription,
    SubscriptionState,
    SubscriptionStatus,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.pagination import (
    PAGE_SIZE,
    footer as page_footer,
    pager_rows,
    total_pages_for,
    window,
)
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, code, fragment, italic
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen

#: A wallet screen's whole read model, assembled once by the handler.
@dataclass(frozen=True)
class WalletView:
    locale: Locale
    tg_id: int
    balance: int
    subscription: Subscription | None
    status: SubscriptionStatus
    now: datetime
    #: A transient banner (a receipt, a needed-stars note) shown above the body.
    banner: Html | None = None
    #: One entry per workspace, because a plan belongs to a workspace now:
    #: ``(workspace name, subscription, its derived status)``.
    workspaces: tuple[tuple[str, "Subscription | None", SubscriptionStatus], ...] = ()
    #: The account's movements, oldest first (the store appends them in order).
    ledger: tuple[Movement, ...] = ()
    #: The account's Stars payments, oldest first.
    payments: tuple[StarsPayment, ...] = ()


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _plan_name(plan_code: str, locale: Locale) -> str:
    key = f"plan.{plan_code}"
    return catalog.t(key, locale) if catalog.has(key, locale) else catalog.t("plan.free", locale)


def _stars(n: int, locale: Locale) -> str:
    return catalog.t("wallet.stars", locale, n=_num(n, locale))


def _status_line(view: WalletView) -> Html:
    locale = view.locale
    status = view.status
    if status.state is SubscriptionState.NONE:
        return fragment(catalog.t("wallet.status.none", locale))
    plan_name = _plan_name(view.subscription.plan_code, locale) if view.subscription else ""
    if status.state is SubscriptionState.TRIALING:
        return fragment(
            catalog.t(
                "wallet.status.trialing",
                locale,
                plan=plan_name,
            )
        )
    if status.state is SubscriptionState.EXPIRED:
        return fragment(catalog.t("wallet.status.expired", locale, plan=plan_name))
    return fragment(
        catalog.t(
            "wallet.status.active",
            locale,
            plan=plan_name,
            days=_num(status.days_left, locale),
        )
    )


def wallet_screen(view: WalletView) -> Screen:
    """The account's money home: balance, plan per workspace, actions.

    The balance leads, because it is the number a user opens this screen to see.
    Plans are per workspace, so each workspace keeps its own plan line. The
    reference material (when the plan ends, the empty-wallet hint) is collapsed
    into ``<details>`` so the body stays a glance, not a manual.
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.title", locale))

    if view.banner is not None:
        doc.blockquote(view.banner)

    doc.h(3, catalog.t("wallet.balance_line", locale, stars=_stars(view.balance, locale)))

    # Current plan per workspace: a plan is attached to a workspace, not to the
    # account, so each workspace shows its own plan and its own days left.
    if view.workspaces:
        rows = []
        for ws_name, subscription, status in view.workspaces:
            rows.append(
                [
                    _raw(ws_name),
                    _raw(_plan_and_state(subscription, status, locale)),
                    _raw(_num(status.days_left, locale)),
                ]
            )
        doc.table(
            [
                catalog.t("wallet.ws.col.workspace", locale),
                catalog.t("wallet.ws.col.plan", locale),
                catalog.t("wallet.ws.col.days", locale),
            ],
            rows,
        )
    else:
        doc.kv_table(
            [(catalog.t("wallet.plan_label", locale), _status_line(view))]
        )

    if view.status.state is SubscriptionState.TRIALING:
        doc.blockquote(fragment(catalog.t("wallet.trial_banner", locale)))
    elif view.status.state is SubscriptionState.EXPIRED:
        doc.blockquote(fragment(catalog.t("wallet.expired_banner", locale)))

    details: list[Html] = []
    if view.subscription is not None and view.subscription.expires_at:
        details.append(
            _raw(
                catalog.t(
                    "wallet.expires_line",
                    locale,
                    when=format_datetime(view.subscription.expires_at, locale),
                )
            )
        )
    if view.status.state is SubscriptionState.NONE:
        details.append(italic(catalog.t("wallet.none_hint", locale)))
    if details:
        doc.details(catalog.t("wallet.details.title", locale), details)

    doc.div()
    rows: list[list] = [
        [
            inline(catalog.t("wallet.buy_stars", locale), "pay:packs", intent=Intent.SUCCESS),
            inline(catalog.t("wallet.buy_plan", locale), "pay:plans", intent=Intent.PRIMARY),
        ],
        [
            inline(catalog.t("wallet.history.button", locale), "pay:history", intent=Intent.PRIMARY),
            inline(catalog.t("common.refresh", locale), "pay:wallet", intent=Intent.PRIMARY),
        ],
    ]
    rows += nav.nav_rows(locale, "wallet")
    return Screen(doc=doc, markup=inline_markup(rows))


def plan_state_line(subscription, status: SubscriptionStatus, locale: Locale) -> str:
    """One workspace's state in words, **with the date it actually ends**.

    The menu used to print the *engine's* ``summary.state``, which is
    ``unlimited`` for a workspace that simply has no run-time cap — so every
    workspace read "بلا انتهاء" and the line told a paying user nothing about
    their plan. A plan belongs to a workspace, so the menu now says the plan and
    when it stops working.

    Shared with ``menu.py`` rather than re-derived there: two spellings of one
    fact is how they drift.
    """
    if subscription is None or status.state is SubscriptionState.NONE:
        return catalog.t("wallet.ws.no_plan", locale)
    name = _plan_name(subscription.plan_code, locale)
    if status.state is SubscriptionState.EXPIRED:
        return catalog.t("wallet.ws.expired", locale, plan=name)
    when = format_datetime(subscription.expires_at, locale)
    if status.state is SubscriptionState.TRIALING:
        return catalog.t("wallet.ws.trialing_until", locale, plan=name, when=when)
    return catalog.t("wallet.ws.active_until", locale, plan=name, when=when)


def _plan_and_state(
    subscription: "Subscription | None",
    status: SubscriptionStatus,
    locale: Locale,
) -> str:
    """One workspace's plan and state, in words (never a raw enum)."""
    if status.state is SubscriptionState.NONE or subscription is None:
        return catalog.t("wallet.ws.no_plan", locale)
    name = _plan_name(subscription.plan_code, locale)
    if status.state is SubscriptionState.EXPIRED:
        return catalog.t("wallet.ws.expired", locale, plan=name)
    if status.state is SubscriptionState.TRIALING:
        return catalog.t("wallet.ws.trialing", locale, plan=name)
    return catalog.t("wallet.ws.active", locale, plan=name, days=_num(status.days_left, locale))

def plan_picker_screen(
    view: WalletView, *, needed_plan: str | None = None, ws_id: str | None = None
) -> Screen:
    """The three plans: period, price and allowance, the current one marked.

    ``needed_plan`` is set when a buy was refused for a lack of Stars; the screen
    then says exactly how many are needed for that plan.
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.plans.title", locale))

    if needed_plan is not None:
        needed = plans.plan(needed_plan)
        if view.balance < needed.amount_stars:
            doc.blockquote(needed_note(needed.amount_stars, view.balance, locale))

    current_code = (
        view.subscription.plan_code
        if view.subscription is not None
        and view.status.state in (SubscriptionState.ACTIVE, SubscriptionState.TRIALING)
        else None
    )

    rows = []
    for plan in plans.paid_plans():
        mark = " ✓" if plan.code == current_code else ""
        label = catalog.t(f"plan.{plan.code}", locale) + mark
        rows.append(
            [
                fragment(label),
                _raw(catalog.t("wallet.plan.period", locale, days=_num(plan.period_days, locale))),
                _stars(plan.amount_stars, locale),
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
    doc.p(
        _raw(catalog.t("wallet.plans.balance", locale, stars=_stars(view.balance, locale)))
    )

    kb: list[list] = []
    for plan in plans.paid_plans():
        if plan.code == current_code:
            kb.append(
                [
                    inline(
                        catalog.t("wallet.plan.current_button", locale, plan=_plan_name(plan.code, locale)),
                        None,
                        intent=Intent.MUTED,
                        disabled=True,
                    )
                ]
            )
        else:
            action = (
                f"pay:buy:{ws_id}:{plan.code}" if ws_id else f"pay:buy:{plan.code}"
            )
            kb.append(
                [
                    inline(
                        catalog.t(
                            "wallet.plan.buy_button",
                            locale,
                            plan=_plan_name(plan.code, locale),
                            stars=_num(plan.amount_stars, locale),
                        ),
                        action,
                        intent=Intent.SUCCESS,
                    )
                ]
            )
    kb.append([inline(catalog.t("common.back", locale), "pay:wallet", intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def packs_screen(view: WalletView, *, back_action: str = "pay:wallet") -> Screen:
    """The Star packs to buy through Telegram Stars.

    ``back_action`` lets the create wizard return to its plan step instead of the
    wallet, so buying Stars mid-flow does not lose the half-built workspace.
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.packs.title", locale))
    doc.p(catalog.t("wallet.packs.body", locale))
    doc.p(
        _raw(catalog.t("wallet.packs.balance", locale, stars=_stars(view.balance, locale)))
    )
    doc.p(italic(catalog.t("wallet.packs.note", locale)))

    kb: list[list] = []
    row: list = []
    for pack in packs():
        row.append(
            inline(
                catalog.t("wallet.pack.button", locale, stars=_num(pack.stars, locale)),
                f"pay:pack:{pack.code}",
                intent=Intent.SUCCESS,
            )
        )
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([inline(catalog.t("common.back", locale), back_action, intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def receipt_screen(view: WalletView, pack: StarPack, *, replay: bool = False) -> Screen:
    """The delivery receipt: what was bought, the new balance, and the charge.

    A re-delivery renders the same receipt (``replay`` only changes the caption),
    so the customer's second notification is not an error.
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.receipt.title", locale))
    doc.p(catalog.t("wallet.receipt.body", locale, stars=_stars(pack.stars, locale)))
    doc.kv_table(
        [
            (catalog.t("wallet.receipt.pack", locale), _stars(pack.stars, locale)),
            (catalog.t("wallet.receipt.balance", locale), _stars(view.balance, locale)),
        ]
    )
    if replay:
        doc.p(italic(catalog.t("wallet.receipt.already", locale)))
    doc.p(italic(catalog.t("wallet.receipt.thanks", locale)))
    rows = [
        [
            inline(catalog.t("wallet.buy_plan", locale), "pay:plans", intent=Intent.SUCCESS),
            inline(catalog.t("common.back", locale), "pay:wallet", intent=Intent.PRIMARY),
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))


def needed_note(needed: int, available: int, locale: Locale) -> Html:
    """The precise shortfall, as a banner on the picker."""
    return fragment(
        catalog.t(
            "wallet.plan.need_more",
            locale,
            needed=_num(needed, locale),
            have=_num(available, locale),
            missing=_num(needed - available, locale),
        )
    )


# -- history: movements and Stars payments -----------------------------------
#: The buyer-facing wording of each movement kind. A ``kind`` is an internal
#: discriminator and must never reach the screen raw; this table is the only
#: translation, in one place, for both locales.
KIND_LABEL_KEY: dict[str, str] = {
    "credit": "wallet.history.kind.credit",
    "debit": "wallet.history.kind.debit",
    "admin_set": "wallet.history.kind.admin_set",
    "refund": "wallet.history.kind.refund",
    "reversal": "wallet.history.kind.reversal",
}

#: Reasons are stored as stable codes (``purchase:month``, ``stars:stars_150``)
#: or as free text written by an admin. A known code gets a human sentence; any
#: other reason is shown verbatim (it is already the operator's own words).
REASON_LABEL_KEY: dict[str, str] = {
    "purchase:week": "wallet.history.reason.purchase_week",
    "purchase:month": "wallet.history.reason.purchase_month",
    "purchase:year": "wallet.history.reason.purchase_year",
    "stars:stars_50": "wallet.history.reason.stars",
    "stars:stars_150": "wallet.history.reason.stars",
    "stars:stars_500": "wallet.history.reason.stars",
    "stars:stars_1200": "wallet.history.reason.stars",
}


def _movement_kind(movement: Movement, locale: Locale) -> str:
    key = KIND_LABEL_KEY.get(movement.kind)
    return catalog.t(key, locale) if key else catalog.t("wallet.history.kind.other", locale)


def _movement_reason(movement: Movement, locale: Locale) -> str:
    key = REASON_LABEL_KEY.get(movement.reason)
    if key is not None:
        return catalog.t(key, locale)
    if movement.reason.startswith("refund:"):
        return catalog.t("wallet.history.reason.refund", locale)
    return movement.reason or catalog.t("wallet.history.reason.none", locale)


def _signed(amount: int, locale: Locale) -> Html:
    """The amount with its sign, in a fixed-width ``<code>`` fragment."""
    sign = "+" if amount >= 0 else "-"
    return code(f"{sign}{_num(abs(amount), locale)}")


def history_screen(view: WalletView, *, page: int = 0) -> Screen:
    """Movements and Stars payments, newest first, on one paged history screen.

    A movement's ``kind`` is mapped to buyer-facing wording; a payment's full
    ``telegram_payment_charge_id`` is never shown (it is an internal identifier)
    — the row carries pack, amount, date and refund state, which is what helps a
    user, and the support screen already tells them what to send if a refund is
    needed. One pager (``presentation.pagination``) covers both lists together so
    the screen never grows without bound.
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.history.title", locale))

    # Newest first across both lists: a movement is stamped ``at``, a payment
    # ``created_at``. Both are stored oldest first, so sort the merged stream by
    # its own timestamp (never by insertion order of two separate tables).
    movements = [(m.at, "movement", m) for m in view.ledger]
    payments = [(p.created_at, "payment", p) for p in view.payments]
    entries = sorted(movements + payments, key=lambda item: item[0], reverse=True)
    total = len(entries)
    items, total_pages, _has_more = window(entries, page)
    page = max(0, min(int(page), total_pages - 1))

    if not entries:
        doc.p(italic(catalog.t("wallet.history.empty", locale)))
        doc.p(_raw(catalog.t("wallet.history.empty_hint", locale)))
    else:
        doc.p(catalog.t("wallet.history.count", locale, n=_num(total, locale)))
        rows = []
        for _at, kind, entry in items:
            if kind == "movement":
                movement: Movement = entry
                rows.append(
                    [
                        _movement_kind(movement, locale),
                        _signed(movement.amount, locale),
                        _movement_reason(movement, locale),
                        _raw(format_datetime(movement.at, locale)),
                    ]
                )
            else:
                payment: StarsPayment = entry
                state = (
                    catalog.t("wallet.history.refunded", locale)
                    if not payment.refundable
                    else catalog.t("wallet.history.charged", locale)
                )
                rows.append(
                    [
                        catalog.t("wallet.history.pack_label", locale),
                        _stars(payment.stars, locale),
                        state,
                        _raw(format_datetime(payment.created_at, locale)),
                    ]
                )
        doc.table(
            [
                catalog.t("wallet.history.col.what", locale),
                catalog.t("wallet.history.col.amount", locale),
                catalog.t("wallet.history.col.reason", locale),
                catalog.t("wallet.history.col.when", locale),
            ],
            rows,
        )
        if total > PAGE_SIZE:
            doc.p(page_footer(page, total_pages, locale))

    rows_kb: list[list] = []
    if total > PAGE_SIZE:
        rows_kb += pager_rows(page, total_pages, lambda target: cb("pay:history", target), locale)
    rows_kb.append(
        [
            inline(catalog.t("common.back", locale), "pay:wallet", intent=Intent.PRIMARY),
        ]
    )
    return Screen(doc=doc, markup=inline_markup(rows_kb))


def support_screen(locale: Locale) -> Screen:
    """``/paysupport`` — the support route Telegram's terms require."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.support.title", locale))
    doc.p(catalog.t("wallet.support.body", locale))
    doc.p(_raw(catalog.t("wallet.support.refund", locale)))
    doc.p(_raw(catalog.t("wallet.support.contact", locale)))
    rows = [
        [
            inline(catalog.t("common.back", locale), "pay:wallet", intent=Intent.PRIMARY),
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))


def _raw(text: str) -> Html:
    return Html(text)
