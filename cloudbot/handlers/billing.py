"""Billing handlers: the wallet, plan purchase, Telegram Stars invoices.

The Stars flow follows Telegram's own contract, measured and documented:

``sendInvoice`` → ``pre_checkout_query`` → ``answerPreCheckoutQuery`` within 10 s
→ ``successful_payment`` → deliver.

Only a ``successful_payment`` credits anything. A ``pre_checkout_query`` is a
question, not money: it is approved only when its payload is one of ours **and**
its amount is the catalogue price; otherwise it is cancelled with the catalog's
human sentence (Telegram shows it to the buyer). Delivery is idempotent because
the charge id keys both the payment row and the wallet movement.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from cloudbot.core.errors import DomainError
from cloudbot.core.locale import Locale
from cloudbot.domain.billing import STARS_CURRENCY, decode_payload, encode_payload, pack
from cloudbot.domain.plans import PLANS
from cloudbot.domain.subscription import SubscriptionState
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import wallet as wallet_screens
from cloudbot.presentation.screens.screen import Screen
from cloudbot.presentation.screens.wallet import WalletView
from cloudbot.services.billing import BillingService, quote, quote_rejection
from cloudbot.services.bundle import Services
from cloudbot.services.events import RequestEvents

log = structlog.get_logger("cloudbot.billing")

router = Router(name="billing")

async def _view(
    services: Services, tg_id: int, locale: Locale, *, banner=None
) -> WalletView:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return WalletView(
        locale=locale,
        tg_id=tg_id,
        balance=(await services.billing.wallet(tg_id)).balance_stars,
        subscription=await services.billing.subscription(tg_id),
        status=await services.billing.status(tg_id, now=now),
        now=now,
        banner=banner,
    )

async def open_wallet(
    event, ctx: UserContext, services: Services, sender: RichSender, *, banner=None
) -> None:
    view = await _view(services, ctx.tg_id, ctx.locale, banner=banner)
    await show_screen(event, wallet_screens.wallet_screen(view), ctx=ctx, sender=sender)

async def open_plans(
    event, ctx: UserContext, services: Services, sender: RichSender, *, needed_plan=None
) -> None:
    """The plan picker — the renewal route a locked workspace points at."""
    view = await _view(services, ctx.tg_id, ctx.locale)
    await show_screen(
        event,
        wallet_screens.plan_picker_screen(view, needed_plan=needed_plan),
        ctx=ctx,
        sender=sender,
    )

@router.callback_query(F.data == "pay:wallet")
async def on_wallet(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    await open_wallet(cb, ctx, services, sender)

@router.callback_query(F.data == "pay:plans")
async def on_plans(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    view = await _view(services, ctx.tg_id, ctx.locale)
    await show_screen(cb, wallet_screens.plan_picker_screen(view), ctx=ctx, sender=sender)

@router.callback_query(F.data == "pay:packs")
async def on_packs(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    view = await _view(services, ctx.tg_id, ctx.locale)
    await show_screen(cb, wallet_screens.packs_screen(view), ctx=ctx, sender=sender)

@router.callback_query(F.data.startswith("pay:buy"))
async def on_buy(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    from cloudbot.adapters.store.sqlite_subscriptions import InsufficientFunds

    decoded = decode(cb.data)
    plan_code = decoded.key[0] if decoded.key else ""
    plan = PLANS.get(plan_code)
    view = await _view(services, ctx.tg_id, ctx.locale)
    if plan is None or not plan.purchasable:
        await show_screen(cb, wallet_screens.plan_picker_screen(view), ctx=ctx, sender=sender)
        return
    # Balance is the only thing between the user and a plan: two taps, no checkout.
    if view.balance < plan.amount_stars:
        await show_screen(
            cb,
            wallet_screens.plan_picker_screen(view, needed_plan=plan_code),
            ctx=ctx,
            sender=sender,
        )
        return
    try:
        subscription = await services.billing.buy_plan(ctx.tg_id, plan_code)
    except InsufficientFunds:  # a concurrent purchase took the balance first
        fresh = await _view(services, ctx.tg_id, ctx.locale)
        await show_screen(
            cb,
            wallet_screens.plan_picker_screen(fresh, needed_plan=plan_code),
            ctx=ctx,
            sender=sender,
        )
        return
    fresh = await _view(services, ctx.tg_id, ctx.locale)
    await show_screen(cb, wallet_screens.wallet_screen(fresh), ctx=ctx, sender=sender)

@router.callback_query(F.data.startswith("pay:pack"))
async def on_pack(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    chosen = pack(decoded.key[0]) if decoded.key else None
    if chosen is None:
        view = await _view(services, ctx.tg_id, ctx.locale)
        await show_screen(cb, wallet_screens.packs_screen(view), ctx=ctx, sender=sender)
        return
    await sender.answer_callback(cb)
    # The payload names the user and the pack — never the amount.
    payload = encode_payload(ctx.tg_id, chosen.code)
    try:
        await sender.send_invoice(
            cb.message.chat.id if cb.message is not None else ctx.tg_id,
            title=catalog.t("wallet.packs.title", ctx.locale),
            description=catalog.t("wallet.pack.button", ctx.locale, stars=_num(chosen.stars, ctx.locale)),
            payload=payload,
            prices=[LabeledPrice(label=catalog.t("wallet.pack.title", ctx.locale), amount=chosen.stars)],
            currency=STARS_CURRENCY,
            provider_token="",
        )
    except Exception as exc:  # noqa: BLE001 - the invoice is not the wallet state
        log.warning("billing.invoice_failed", error=type(exc).__name__)
        view = await _view(services, ctx.tg_id, ctx.locale)
        await show_screen(cb, wallet_screens.packs_screen(view), ctx=ctx, sender=sender)

# -- the Telegram handshake --------------------------------------------------
@router.pre_checkout_query()
async def on_pre_checkout(
    query: PreCheckoutQuery,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
) -> None:
    """Approve within the 10 s window, but only for our payload at our price.

    Nothing is credited here. A refusal carries the catalog's human sentence,
    which Telegram shows to the buyer.
    """
    verified = quote(query.invoice_payload, query.total_amount)
    if verified is None:
        await sender.answer_pre_checkout(
            query.id,
            ok=False,
            error_message=catalog.t(quote_rejection(query.invoice_payload, query.total_amount), ctx.locale),
        )
        return
    # The payload names its buyer; a checkout for another account is not ours.
    if verified.tg_id != ctx.tg_id:
        await sender.answer_pre_checkout(
            query.id, ok=False, error_message=catalog.t("pay.checkout.forged", ctx.locale)
        )
        return
    await sender.answer_pre_checkout(query.id, ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    ctx: UserContext,
    services: Services,
    sender: RichSender,
    events: RequestEvents,
) -> None:
    """Credit exactly once, keep the charge id, and show the receipt."""
    payment = message.successful_payment
    if payment is None:
        return
    decoded = decode_payload(payment.invoice_payload)
    if decoded is None:
        # A payment whose payload is not ours: nothing to credit, nothing to leak.
        log.warning("billing.payment_unknown_payload", tg_id=ctx.tg_id)
        return
    payload_tg_id, chosen = decoded
    if payload_tg_id != ctx.tg_id:
        return
    # The catalogue is the price, never ``payment.total_amount``.
    result = await services.billing.deliver_payment(
        tg_id=ctx.tg_id,
        pack=chosen,
        charge_id=payment.telegram_payment_charge_id,
        provider_charge_id=payment.provider_payment_charge_id or "",
    )
    if result.credited:
        events.admin_action(
            admin_id=ctx.tg_id,
            action="stars_paid",
            owner_tg_id=ctx.tg_id,
            params={"stars": chosen.stars},
        )
    view = await _view(services, ctx.tg_id, ctx.locale)
    screen = wallet_screens.receipt_screen(view, chosen, replay=not result.credited)
    await show_screen(message, screen, ctx=ctx, sender=sender, force_send=True)

# -- support -----------------------------------------------------------------
@router.message(Command("paysupport"))
async def on_paysupport(message: Message, ctx: UserContext, sender: RichSender) -> None:
    await show_screen(message, wallet_screens.support_screen(ctx.locale), ctx=ctx, sender=sender)

def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)

__all__ = ["open_plans", "open_wallet", "router"]
