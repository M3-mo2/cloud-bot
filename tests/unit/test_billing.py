"""Telegram Stars billing: the handshake, the catalogue price, and idempotency.

Offline: a real dispatcher over an in-memory store and a recording bot. No real
invoice and no real Stars are ever created — the bot methods are captured, not
called against Telegram. The "no real payment" guarantee is structural: the
tests construct the updates Telegram would send.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain import plans
from cloudbot.domain.billing import decode_payload, encode_payload
from cloudbot.domain.subscription import SubscriptionSource, SubscriptionState
from cloudbot.domain.value_objects import WorkspaceStatus

ADMIN = 777
USER = 6508129575
RICH = {"text": "x"}

class RecordingBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.invoices: list[dict] = []
        self.pre_checkout_answers: list[dict] = []
        self.refunds: list[dict] = []
        self.deleted: list[int] = []
        self.markups: list = []

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(rich_message.html)
        self.markups.append(reply_markup)
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append(getattr(rich_message, "html", ""))
        self.markups.append(reply_markup)
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True

    async def send_invoice(self, **kwargs):
        self.invoices.append(kwargs)
        return Message.model_construct(chat={"id": kwargs["chat_id"]}, message_id=2)

    async def answer_pre_checkout_query(self, pre_checkout_query_id, ok, error_message=None):
        self.pre_checkout_answers.append(
            {"id": pre_checkout_query_id, "ok": ok, "error": error_message}
        )
        return True

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        self.refunds.append({"user_id": user_id, "charge_id": telegram_payment_charge_id})
        return True

    async def __call__(self, method):
        return True

async def _container():
    # DEV + mock engine: the same dispatcher, but the engine is the fake one.
    # Nothing is ever sent to Telegram — the bot is a recorder.
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids=str(ADMIN),
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    await container.users.get_or_create(USER, default_locale=Locale.EN)
    return container

def _callback(uid: int, user_id: int, data: str) -> Update:
    msg = Message(
        message_id=uid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text="screen",
    )
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c",
            message=msg,
            data=data,
        ),
    )

def _payment_message(uid: int, user_id: int, *, payload: str, amount: int, charge_id: str) -> Update:
    return Update(
        update_id=uid,
        message=Message(
            message_id=uid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            successful_payment=SuccessfulPayment(
                currency="XTR",
                total_amount=amount,
                invoice_payload=payload,
                telegram_payment_charge_id=charge_id,
                provider_payment_charge_id="",
            ),
        ),
    )

def _pre_checkout(uid: int, user_id: int, *, payload: str, amount: int) -> Update:
    return Update(
        update_id=uid,
        pre_checkout_query=PreCheckoutQuery(
            id=f"pcq{uid}",
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            currency="XTR",
            total_amount=amount,
            invoice_payload=payload,
        ),
    )

# -- the catalogue price, not the payload -----------------------------------
async def test_a_valid_pre_checkout_approves():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_150")
        await dispatcher.feed_update(bot, _pre_checkout(1, USER, payload=payload, amount=150))
        assert bot.pre_checkout_answers == [{"id": "pcq1", "ok": True, "error": None}]
    finally:
        await container.aclose()

async def test_a_forged_payload_is_rejected_human_readably():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _pre_checkout(1, USER, payload="free:1:everything", amount=1))
        assert len(bot.pre_checkout_answers) == 1
        answer = bot.pre_checkout_answers[0]
        assert answer["ok"] is False
        assert answer["error"] and "invoice" in answer["error"].lower()
    finally:
        await container.aclose()

async def test_a_mismatched_amount_is_rejected():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_150")
        await dispatcher.feed_update(bot, _pre_checkout(1, USER, payload=payload, amount=1))
        assert bot.pre_checkout_answers[0]["ok"] is False
        assert "amount" in bot.pre_checkout_answers[0]["error"].lower()
    finally:
        await container.aclose()

async def test_a_payload_for_another_user_is_rejected():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER + 1, "stars_50")
        await dispatcher.feed_update(bot, _pre_checkout(1, USER, payload=payload, amount=50))
        assert bot.pre_checkout_answers[0]["ok"] is False
    finally:
        await container.aclose()

# -- delivery is idempotent, and the amount is the catalogue's --------------
async def test_successful_payment_credits_once_and_a_duplicate_is_a_noop():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_150")
        # A forged ``total_amount`` different from the pack still credits the
        # catalogue's 150 — the payload carries no price and is not trusted.
        await dispatcher.feed_update(
            bot, _payment_message(1, USER, payload=payload, amount=999_999, charge_id="ch_1")
        )
        first = await container.services.billing.wallet(USER)
        assert first.balance_stars == 150
        assert bot.sent and "Payment received" in bot.sent[-1]

        # Telegram re-delivers the same charge: no second credit, same outcome.
        await dispatcher.feed_update(
            bot, _payment_message(2, USER, payload=payload, amount=150, charge_id="ch_1")
        )
        second = await container.services.billing.wallet(USER)
        assert second.balance_stars == 150
        assert len(second.ledger) == 1
        assert "repeat receipt" in bot.sent[-1]
    finally:
        await container.aclose()

async def test_the_charge_id_is_stored_for_refunds():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_50")
        await dispatcher.feed_update(
            bot, _payment_message(1, USER, payload=payload, amount=50, charge_id="ch_keep")
        )
        payment = await container.subscriptions.payment("ch_keep")
        assert payment is not None and payment.stars == 50 and payment.refundable
    finally:
        await container.aclose()

# -- the wallet screens -----------------------------------------------------
async def test_the_wallet_screen_is_reachable_and_offers_stars_and_plans():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, USER, "pay:wallet"))
        html = bot.sent[-1]
        assert "Wallet" in html
        actions = {
            b.callback_data
            for row in bot.markups[-1].inline_keyboard
            for b in row
        }
        assert {"pay:packs", "pay:plans"} <= actions
    finally:
        await container.aclose()

async def test_buying_a_pack_sends_an_xtr_invoice_with_an_empty_provider_token():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, USER, "pay:pack:stars_150"))
        invoice = bot.invoices[-1]
        assert invoice["currency"] == "XTR"
        assert invoice["provider_token"] == ""
        assert invoice["prices"][0].amount == 150
        decoded = decode_payload(invoice["payload"])
        assert decoded is not None and decoded[0] == USER and decoded[1].stars == 150
    finally:
        await container.aclose()

async def test_insufficient_balance_says_how_many_stars_are_needed():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await container.subscriptions.credit(USER, 20, "seed", actor=ADMIN)
        await dispatcher.feed_update(bot, _callback(1, USER, "pay:buy:month"))
        html = bot.sent[-1]
        # 150 needed, 20 held, so 130 more.
        assert "130" in html and "150" in html
        # And nothing was bought.
        assert await container.subscriptions.get(USER) is None
    finally:
        await container.aclose()

# -- purchase, expiry, lock, renew (through the real handlers) --------------
async def _set_expired(container, tg_id: int, *, days_ago: int = 1) -> None:
    conn = await container.database.connect()
    past = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    await conn.execute("UPDATE subscriptions SET expires_at = ? WHERE tg_id = ?", (past, tg_id))

async def test_purchase_then_expiry_locks_then_renew_is_active_again():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Credits come from the admin, never Telegram.
        await container.services.admin.credit(USER, 50, "test", actor=ADMIN)
        # Two taps: wallet → buy week.
        await dispatcher.feed_update(bot, _callback(1, USER, "pay:buy:week"))
        subscription = await container.subscriptions.get(USER)
        assert subscription is not None
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0

        # The user creates a workspace while active.
        ws = await container.services.workspaces.create(USER, "mine")
        listed = await container.services.workspaces.list(USER)
        assert listed and listed[0].status is WorkspaceStatus.ACTIVE

        # Time passes: the plan expires; the workspace renders LOCKED, files kept.
        await _set_expired(container, USER, days_ago=1)
        listed = await container.services.workspaces.list(USER)
        assert listed and listed[0].status is WorkspaceStatus.LOCKED
        assert listed[0].id == ws.id

        # Renew: an admin credit funds a new week, then the wallet buy is active again.
        await container.services.admin.credit(USER, 50, "renewal test", actor=ADMIN)
        await dispatcher.feed_update(bot, _callback(2, USER, "pay:buy:week"))
        listed = await container.services.workspaces.list(USER)
        assert listed and listed[0].status is WorkspaceStatus.ACTIVE
    finally:
        await container.aclose()

# -- a trial never becomes a paid plan without a payment --------------------
async def test_a_trial_never_becomes_a_paid_plan_without_payment():
    container = await _container()
    try:
        trial = await container.subscriptions.start_trial(USER)
        assert trial is not None and trial.source is SubscriptionSource.TRIAL
        status = await container.services.billing.status(USER)
        assert status.state is SubscriptionState.TRIALING
        # No payment: even after the trial lapses, it is EXPIRED, never ACTIVE
        # (and never charged for).
        await _set_expired(container, USER, days_ago=1)
        status = await container.services.billing.status(USER)
        assert status.state is SubscriptionState.EXPIRED
        subscription = await container.subscriptions.get(USER)
        assert subscription.source is SubscriptionSource.TRIAL
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0
    finally:
        await container.aclose()

# -- refund: reversal recorded, charge id recorded, idempotent --------------
async def test_admin_refund_reverses_the_credit_once():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_150")
        await dispatcher.feed_update(
            bot, _payment_message(1, USER, payload=payload, amount=150, charge_id="ch_ref")
        )
        payment = await container.services.billing.refund(
            tg_id=USER, charge_id="ch_ref", actor=ADMIN, reason="customer request"
        )
        assert payment is not None and not payment.refundable
        wallet = await container.subscriptions.wallet(USER)
        assert wallet.balance_stars == 0
        assert wallet.ledger[-1].kind == "reversal"
        assert wallet.ledger[-1].amount == -150
        # A retried refund does not double-reverse.
        again = await container.services.billing.refund(
            tg_id=USER, charge_id="ch_ref", actor=ADMIN, reason="customer request"
        )
        assert again is not None and not again.refundable
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0
    finally:
        await container.aclose()

async def test_paysupport_renders_the_support_screen():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        update = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=USER, type="private"),
                from_user=User(id=USER, is_bot=False, first_name="A"),
                text="/paysupport",
            ),
        )
        await dispatcher.feed_update(bot, update)
        assert bot.sent and "Payment support" in bot.sent[-1]
    finally:
        await container.aclose()

# -- the admin's balance controls actually work -----------------------------
async def test_admin_credit_lets_a_user_buy_a_plan_with_nothing_from_telegram():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0
        await container.services.admin.credit(USER, 50, "gift", actor=ADMIN)
        await dispatcher.feed_update(bot, _callback(1, USER, "pay:buy:week"))
        subscription = await container.subscriptions.get(USER)
        assert subscription is not None
        assert subscription.source is SubscriptionSource.STARS
        # No Telegram invoice was ever sent.
        assert bot.invoices == []
    finally:
        await container.aclose()

# -- admin controls, driven through the panel -------------------------------
async def test_admin_credit_debit_and_set_through_the_panel():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Open the wallet control from the user page's own button.
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:wallet:{USER}"))
        assert bot.sent and "Wallet" in bot.sent[-1]

        # Credit 200 via the prompt → amount → reason → perform chain.
        await dispatcher.feed_update(bot, _callback(2, ADMIN, f"adm:wop:credit:{USER}:ask"))
        await _text(dispatcher, bot, 3, ADMIN, "200")
        await _text(dispatcher, bot, 4, ADMIN, "gift")
        assert (await container.subscriptions.wallet(USER)).balance_stars == 200

        # Debit 50.
        await dispatcher.feed_update(bot, _callback(5, ADMIN, f"adm:wop:debit:{USER}:ask"))
        await _text(dispatcher, bot, 6, ADMIN, "50")
        await _text(dispatcher, bot, 7, ADMIN, "correction")
        assert (await container.subscriptions.wallet(USER)).balance_stars == 150

        # Set to 10.
        await dispatcher.feed_update(bot, _callback(8, ADMIN, f"adm:wop:set:{USER}:ask"))
        await _text(dispatcher, bot, 9, ADMIN, "10")
        await _text(dispatcher, bot, 10, ADMIN, "reset")
        wallet = await container.subscriptions.wallet(USER)
        assert wallet.balance_stars == 10
        # Every movement is attributed to the admin and carries a reason.
        assert all(m.actor_tg_id == ADMIN for m in wallet.ledger)
        assert all(m.reason for m in wallet.ledger)
        assert wallet.balanced
    finally:
        await container.aclose()

async def test_admin_grant_extend_shorten_and_revoke_through_the_panel():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Grant month for 30 days: plan picker → days → reason.
        await dispatcher.feed_update(bot, _callback(1, ADMIN, f"adm:wop:grant:{USER}:ask"))
        await dispatcher.feed_update(bot, _callback(2, ADMIN, f"adm:wplan:grant:{USER}:month:ask"))
        await _text(dispatcher, bot, 3, ADMIN, "30")
        await _text(dispatcher, bot, 4, ADMIN, "support")
        sub = await container.services.billing.subscription(USER)
        assert sub is not None and sub.plan_code == "month"

        # Extend by 5 days.
        await dispatcher.feed_update(bot, _callback(5, ADMIN, f"adm:wop:extend:{USER}:ask"))
        await _text(dispatcher, bot, 6, ADMIN, "5")
        await _text(dispatcher, bot, 7, ADMIN, "goodwill")
        extended = await container.services.billing.subscription(USER)
        assert extended.expires_at == sub.expires_at + timedelta(days=5)

        # Shorten by 3 days.
        await dispatcher.feed_update(bot, _callback(8, ADMIN, f"adm:wop:shorten:{USER}:ask"))
        await _text(dispatcher, bot, 9, ADMIN, "3")
        await _text(dispatcher, bot, 10, ADMIN, "correction")
        shortened = await container.services.billing.subscription(USER)
        assert shortened.expires_at == extended.expires_at - timedelta(days=3)

        # Revoke.
        await dispatcher.feed_update(bot, _callback(11, ADMIN, f"adm:wop:revoke:{USER}:ask"))
        await _text(dispatcher, bot, 12, ADMIN, "refund")
        revoked = await container.services.billing.subscription(USER)
        status = await container.services.billing.status(USER)
        assert status.is_active is False
    finally:
        await container.aclose()

async def test_admin_wallet_refund_uses_telegram_then_reverses():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        payload = encode_payload(USER, "stars_50")
        await dispatcher.feed_update(
            bot, _payment_message(1, USER, payload=payload, amount=50, charge_id="ch_admin")
        )
        await dispatcher.feed_update(bot, _callback(2, ADMIN, f"adm:wop:refund:{USER}:ask"))
        await _text(dispatcher, bot, 3, ADMIN, "ch_admin")
        assert bot.refunds == [{"user_id": USER, "charge_id": "ch_admin"}]
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0
    finally:
        await container.aclose()

# -- double delivery in one burst -------------------------------------------
async def test_two_identical_payments_in_parallel_credit_exactly_once():
    import asyncio

    container = await _container()
    try:
        payload = encode_payload(USER, "stars_150")
        from cloudbot.domain.billing import pack

        results = await asyncio.gather(
            *[
                container.services.billing.deliver_payment(
                    tg_id=USER, pack=pack("stars_150"), charge_id="ch_race"
                )
                for _ in range(2)
            ]
        )
        credited = sum(1 for r in results if r.credited)
        assert credited == 1, results
        assert (await container.subscriptions.wallet(USER)).balance_stars == 150
    finally:
        await container.aclose()

def _text(dispatcher, bot, uid: int, user_id: int, text: str):
    return dispatcher.feed_update(
        bot,
        Update(
            update_id=uid,
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text=text,
            ),
        ),
    )

async def test_a_locked_workspace_renew_opens_the_plan_picker():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        ws = await container.services.workspaces.create(USER, "mine")
        # If the plan is active the workspace is active; a revocation locks it.
        await container.services.billing._subscriptions.revoke(USER, actor=ADMIN, reason="test")
        await dispatcher.feed_update(bot, _callback(1, USER, f"ws:renew:{ws.id}"))
        html = bot.sent[-1]
        assert "Choose a plan" in html
        actions = {
            b.callback_data for row in bot.markups[-1].inline_keyboard for b in row
        }
        assert "pay:buy:week" in actions
    finally:
        await container.aclose()
