"""Admin section: a user's wallet and subscription controls.

This is the "very flexible" admin surface: credit, debit, set the balance;
grant, extend, shorten and revoke a subscription; refund a Stars payment. Every
action is attributed to the admin and carries a reason, and the ledger below the
controls is the audit trail of what actually happened — the balance is always the
SUM of that ledger.
"""

from __future__ import annotations

from datetime import datetime

from cloudbot.core.locale import Locale
from cloudbot.domain.admin import AdminView
from cloudbot.domain.billing import StarsPayment
from cloudbot.domain.subscription import Subscription, SubscriptionState, derive_status
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import add_block_header, num, subject
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register

#: The wallet operations and the catalog key of their prompt. Data, so a new
#: operation is a row rather than another branch in the handler.
OPS: dict[str, str] = {
    "credit": "admin.wallet.prompt_credit",
    "debit": "admin.wallet.prompt_debit",
    "set": "admin.wallet.prompt_set",
    "grant": "admin.wallet.prompt_grant_days",
    "extend": "admin.wallet.prompt_extend_days",
    "shorten": "admin.wallet.prompt_shorten_days",
    "revoke": "admin.wallet.prompt_reason",
    "refund": "admin.wallet.prompt_refund",
}

def wallet_screen(
    view: AdminView,
    *,
    tg_id: int,
    wallet=None,
    subscription: Subscription | None = None,
    payments: tuple[StarsPayment, ...] = (),
) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    balance = wallet.balance_stars if wallet is not None else 0
    add_block_header(
        doc,
        subject(locale, "admin.wallet.title"),
        catalog.t("wallet.stars", locale, n=_num(balance, locale)),
        locale,
    )
    doc.paragraph(f"{catalog.t('admin.user.id', locale)}: {tg_id}")

    if subscription is None:
        plan_line = catalog.t("admin.wallet.none", locale)
    else:
        status = derive_status(subscription, view.now or datetime.now())
        plan = catalog.t(f"plan.{subscription.plan_code}", locale)
        if status.state is SubscriptionState.EXPIRED:
            plan_line = catalog.t("wallet.status.expired", locale, plan=plan)
        elif status.state is SubscriptionState.TRIALING:
            plan_line = catalog.t(
                "wallet.status.trialing",
                locale,
                plan=plan,
            )
        else:
            plan_line = catalog.t(
                "wallet.status.active",
                locale,
                plan=plan,
                days=_num(status.days_left, locale),
            )
    doc.kv_block(
        [
            (catalog.t("admin.wallet.plan", locale), plan_line),
            (
                catalog.t("admin.wallet.balance", locale),
                catalog.t("wallet.stars", locale, n=_num(balance, locale)),
            ),
        ]
    )

    # The ledger: what happened, attributed, with the reason. One heading per
    # movement (kind + signed amount) and one fact line (time and reason) — never
    # a four-column table a right-to-left message drifts apart.
    ledger = () if wallet is None else wallet.ledger
    doc.heading(
        f"✦ {catalog.t('admin.wallet.ledger_title', locale)} ⟸ {num(len(ledger), locale)}",
        size=2,
    )
    if not ledger:
        doc.paragraph(catalog.t("admin.wallet.ledger_empty", locale))
    else:
        for movement in reversed(ledger[-10:]):
            kind = catalog.t(f"admin.wallet.kind.{movement.kind}", locale)
            amount = ("+" if movement.amount > 0 else "") + str(movement.amount)
            doc.heading(f"{kind} · {localize_digits(amount, locale)}", size=3)
            doc.paragraph(
                f"{format_datetime(movement.at, locale)} · {movement.reason or '—'}"
            )

    if payments:
        doc.heading(
            f"✦ {catalog.t('admin.wallet.payments_title', locale)} ⟸ {num(len(payments), locale)}",
            size=2,
        )
        for payment in reversed(payments[-10:]):
            state = (
                catalog.t("admin.wallet.refunded_mark", locale)
                if not payment.refundable
                else catalog.t("admin.wallet.refundable_mark", locale)
            )
            doc.heading(
                catalog.t("wallet.stars", locale, n=_num(payment.stars, locale)),
                size=3,
            )
            doc.paragraph(
                f"{payment.charge_id} · {state} · {format_datetime(payment.created_at, locale)}"
            )

    kb: list[list] = []
    kb.append(
        [
            inline(catalog.t("admin.wallet.credit", locale), admin_cb.wallet_op("credit", tg_id), intent=Intent.SUCCESS),
            inline(catalog.t("admin.wallet.debit", locale), admin_cb.wallet_op("debit", tg_id), intent=Intent.DANGER),
        ]
    )
    kb.append(
        [
            inline(catalog.t("admin.wallet.set", locale), admin_cb.wallet_op("set", tg_id), intent=Intent.PRIMARY),
            inline(catalog.t("admin.wallet.refund", locale), admin_cb.wallet_op("refund", tg_id), intent=Intent.PRIMARY),
        ]
    )
    if subscription is not None:
        kb.append(
            [
                inline(catalog.t("admin.wallet.extend", locale), admin_cb.wallet_op("extend", tg_id), intent=Intent.SUCCESS),
                inline(catalog.t("admin.wallet.shorten", locale), admin_cb.wallet_op("shorten", tg_id), intent=Intent.PRIMARY),
            ]
        )
    kb.append(
        [
            inline(catalog.t("admin.wallet.grant", locale), admin_cb.wallet_op("grant", tg_id), intent=Intent.SUCCESS),
            inline(catalog.t("admin.wallet.revoke", locale), admin_cb.wallet_op("revoke", tg_id), intent=Intent.DANGER),
        ]
    )
    kb.append(
        [
            inline(catalog.t("admin.back", locale), admin_cb.user(tg_id), intent=Intent.PRIMARY),
        ]
    )
    return Screen(doc=doc, markup=inline_markup(kb))

def plan_prompt(tg_id: int, op: str, locale: Locale) -> Screen:
    """Which plan to grant? One row per purchasable plan."""
    from cloudbot.domain.plans import paid_plans

    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.wallet.prompt_plan", locale))
    kb = [
        [
            inline(
                catalog.t("admin.wallet.plan_button", locale, plan=catalog.t(f"plan.{plan.code}", locale)),
                admin_cb.wallet_plan(op, tg_id, plan.code, "ask"),
                intent=Intent.PRIMARY,
            )
        ]
        for plan in paid_plans()
    ]
    kb.append([inline(catalog.t("admin.wallet.cancel", locale), admin_cb.wallet(tg_id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))

def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)

def _raw(text):
    from cloudbot.presentation.rich.tags import Html

    return Html(text)

register(
    AdminSection(
        key="wallet",
        title_key="admin.wallet.title",
        order=25,
        icon="💳",
        build=lambda view: wallet_screen(view, tg_id=0),
        in_menu=False,
    )
)
