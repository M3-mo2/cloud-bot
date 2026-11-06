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
from cloudbot.domain.billing import StarPack, packs
from cloudbot.domain.subscription import (
    Subscription,
    SubscriptionState,
    SubscriptionStatus,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
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
    """The account's money home: plan, days left, trial banner, balance, actions."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("wallet.title", locale))

    if view.banner is not None:
        doc.blockquote(view.banner)

    # Current plan first: it is the answer to "what am I paying for?". When the
    # caller supplies per-workspace state, each workspace shows its own plan,
    # because a plan is attached to a workspace, not to the account.
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
            [
                (catalog.t("wallet.plan_label", locale), _status_line(view)),
                (
                    catalog.t("wallet.balance_label", locale),
                    _stars(view.balance, locale),
                ),
            ]
        )

    if view.subscription is not None and view.subscription.expires_at:
        doc.p(
            _raw(
                catalog.t(
                    "wallet.expires_line",
                    locale,
                    when=format_datetime(view.subscription.expires_at, locale),
                )
            )
        )

    if view.status.state is SubscriptionState.TRIALING:
        doc.blockquote(fragment(catalog.t("wallet.trial_banner", locale)))
    elif view.status.state is SubscriptionState.EXPIRED:
        doc.blockquote(fragment(catalog.t("wallet.expired_banner", locale)))
    elif view.status.state is SubscriptionState.NONE:
        doc.p(italic(catalog.t("wallet.none_hint", locale)))

    doc.div()
    rows: list[list] = [
        [
            inline(catalog.t("wallet.buy_stars", locale), "pay:packs", intent=Intent.SUCCESS),
            inline(catalog.t("wallet.buy_plan", locale), "pay:plans", intent=Intent.PRIMARY),
        ],
        [
            inline(catalog.t("common.refresh", locale), "pay:wallet", intent=Intent.PRIMARY),
        ],
    ]
    rows += nav.nav_rows(locale, "wallet")
    return Screen(doc=doc, markup=inline_markup(rows))


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
