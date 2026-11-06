"""TASK planws: a subscription belongs to a workspace, and create goes via a plan.

Every test here fails on the old behaviour:

* ``ws:new`` → name → «متابعة» reached no plan step at all, so a workspace could
  be created without a plan;
* one user-level subscription decided the state of *every* workspace, so two
  workspaces on different plans could not show different states;
* the trial was 7 days and could be granted to every new workspace.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.adapters.persistence.database import MIGRATIONS_DIR, Database
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import InsufficientFunds, TrialUsed
from cloudbot.core.locale import Locale
from cloudbot.domain import plans
from cloudbot.domain.subscription import derive_status
from cloudbot.domain.value_objects import WorkspaceStatus

ADMIN = 777
USER = 6508129575
RICH = {"text": "x"}


class RecordingBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.markups: list = []
        self.invoices: list[dict] = []

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(rich_message.html)
        self.markups.append(reply_markup)
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append(getattr(rich_message, "html", ""))
        self.markups.append(reply_markup)
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_invoice(self, **kwargs):
        self.invoices.append(kwargs)
        return Message.model_construct(chat={"id": kwargs["chat_id"]}, message_id=2)

    async def answer_pre_checkout_query(self, pre_checkout_query_id, ok, error_message=None):
        return True

    async def __call__(self, method):
        return True


async def _container():
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


def _text(uid: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=uid,
        message=Message(
            message_id=uid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _actions(bot: RecordingBot) -> set[str]:
    markup = bot.markups[-1]
    if markup is None:
        return set()
    return {b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data}


async def _expire(container, ws_id: str, *, days_ago: int = 1) -> None:
    conn = await container.database.connect()
    past = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    await conn.execute("UPDATE subscriptions SET expires_at = ? WHERE ws_id = ?", (past, ws_id))


# -- 1. no plan, no workspace -------------------------------------------------
async def test_the_create_flow_reaches_the_plan_picker_and_creates_nothing_before_it():
    container = await _container()
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _callback(1, USER, "ws:new"))
        await dispatcher.feed_update(bot, _text(2, USER, "project"))
        # The network step: internet + public links + «متابعة».
        assert "متابعة" in " ".join(b.text for row in bot.markups[-1].inline_keyboard for b in row)
        assert "ws:continue" in _actions(bot)
        # Still no workspace.
        assert await container.services.workspaces.list(USER) == []

        await dispatcher.feed_update(bot, _callback(3, USER, "ws:continue"))
        # The plan picker: a trial row and the paid plans, but no workspace yet.
        assert "Choose a plan" in bot.sent[-1]
        actions = _actions(bot)
        assert "ws:plan:trial" in actions
        assert "ws:plan:week" in actions
        assert await container.services.workspaces.list(USER) == []

        # The Stars packs bought mid-flow return to the plan step, state intact.
        await dispatcher.feed_update(bot, _callback(4, USER, "ws:packs"))
        assert "ws:continue" in _actions(bot)
        await dispatcher.feed_update(bot, _callback(5, USER, "ws:continue"))
        assert "ws:plan:trial" in _actions(bot)

        await dispatcher.feed_update(bot, _callback(6, USER, "ws:plan:trial"))
        listed = await container.services.workspaces.list(USER)
        assert len(listed) == 1
        assert listed[0].name == "project"
    finally:
        await container.aclose()


# -- 2. two workspaces, two plans, two states --------------------------------
async def test_two_workspaces_on_different_plans_show_their_own_states():
    container = await _container()
    try:
        first = await container.services.workspaces.create(USER, "first", plan_code="trial")
        await container.services.admin.credit(USER, 50, "test", actor=ADMIN)
        second = await container.services.workspaces.create(USER, "second", plan_code="week")
        await _expire(container, str(first.id), days_ago=1)

        listed = {ws.id: ws for ws in await container.services.workspaces.list(USER)}
        assert listed[first.id].status is WorkspaceStatus.LOCKED
        assert listed[second.id].status is WorkspaceStatus.ACTIVE

        statuses = {
            str(s.ws_id): derive_status(s, datetime.now(timezone.utc)).state.value
            for s in await container.services.billing.subscriptions(USER)
        }
        assert statuses[str(first.id)] == "expired"
        assert statuses[str(second.id)] == "active"
    finally:
        await container.aclose()


# -- 3. the trial is 3 days, once per user, and disappears after ------------
async def test_the_trial_is_three_days_once_per_user_and_hidden_after():
    container = await _container()
    try:
        assert plans.TRIAL_DAYS == 3
        first = await container.services.workspaces.create(USER, "first", plan_code="trial")
        sub = await container.subscriptions.get(USER, str(first.id))
        assert sub is not None and (sub.expires_at - sub.started_at) == timedelta(days=3)
        assert (await container.subscriptions.wallet(USER)).balance_stars == 0

        # The second workspace cannot take the free trial again.
        with pytest.raises(TrialUsed):
            await container.services.workspaces.create(USER, "freebie", plan_code="trial")
        assert len(await container.services.workspaces.list(USER)) == 1

        # And the picker no longer offers it.
        codes = [p.code for p in await container.services.workspaces.plan_options(USER)]
        assert "trial" not in codes
        assert codes == ["week", "month", "year"]
    finally:
        await container.aclose()


# -- 4. a paid plan: enough balance -> debited once, plan limits + expiry ----
async def test_a_paid_plan_is_debited_once_and_sets_limits_and_expiry():
    container = await _container()
    try:
        await container.services.admin.credit(USER, 200, "test", actor=ADMIN)
        ws = await container.services.workspaces.create(USER, "paid", plan_code="month")
        plan = plans.plan("month")
        maxima = (await container.services.workspaces.meta()).limits_maxima
        expected = plan.to_limits(maxima)
        assert ws.limits == expected
        assert (await container.subscriptions.wallet(USER)).balance_stars == 50

        sub = await container.subscriptions.get(USER, str(ws.id))
        assert sub is not None
        assert sub.plan_code == "month"
        assert (sub.expires_at - sub.started_at) == timedelta(days=plan.period_days)
        # Exactly one debit row, and it is the plan price.
        wallet = await container.subscriptions.wallet(USER)
        debits = [m for m in wallet.ledger if m.kind == "debit"]
        assert [m.amount for m in debits] == [-plan.amount_stars]
    finally:
        await container.aclose()


async def test_a_short_balance_creates_nothing_and_moves_no_money():
    container = await _container()
    try:
        await container.services.admin.credit(USER, 20, "test", actor=ADMIN)
        with pytest.raises(InsufficientFunds):
            await container.services.workspaces.create(USER, "too-pricey", plan_code="month")
        assert await container.services.workspaces.list(USER) == []
        wallet = await container.subscriptions.wallet(USER)
        assert wallet.balance_stars == 20
        assert all(m.kind == "credit" for m in wallet.ledger)
    finally:
        await container.aclose()


# -- 5. the migration: legacy rows land on the first workspace --------------
async def test_legacy_user_subscriptions_move_to_the_first_workspace(tmp_path):
    path = str(tmp_path / "legacy.db")
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.connect()
    conn = await db.connect()
    # Build a database exactly as it stood at migration 6 (one plan per user).
    for name in (
        "0001_init.sql",
        "0002_admin.sql",
        "0003_session.sql",
        "0004_keyboard_reset.sql",
        "0005_subscriptions.sql",
        "0006_stars_payments.sql",
    ):
        await conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (int(name[:4]),),
        )
    await conn.execute(
        "INSERT INTO users (tg_id, locale, plan, created_at, last_seen_at) "
        "VALUES (1, 'en', 'free', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    # Two workspaces: the first by created_at is the one the legacy plan lands on.
    await conn.execute(
        "INSERT INTO user_workspaces (tg_id, ws_id, created_at) VALUES "
        "(1, 'ws_second', '2026-01-02T00:00:00Z'), (1, 'ws_first', '2026-01-01T00:00:00Z')"
    )
    await conn.execute(
        "INSERT INTO subscriptions (tg_id, plan_code, started_at, expires_at, source, updated_at) "
        "VALUES (1, 'week', '2026-01-01T00:00:00Z', '2026-01-08T00:00:00Z', 'stars', "
        "'2026-01-01T00:00:00Z')"
    )
    # A legacy row whose owner has no workspace is preserved, not deleted.
    await conn.execute(
        "INSERT INTO subscriptions (tg_id, plan_code, started_at, expires_at, source, updated_at) "
        "VALUES (2, 'month', '2026-01-01T00:00:00Z', '2026-01-31T00:00:00Z', 'stars', "
        "'2026-01-01T00:00:00Z')"
    )
    await conn.execute(
        "INSERT INTO wallet_ledger (tg_id, kind, amount, reason, at, actor_tg_id) "
        "VALUES (1, 'credit', 40, 'seed', '2026-01-01T00:00:00Z', 1)"
    )
    await conn.commit()

    assert await db.migrate() == [7]
    conn = await db.connect()

    async with conn.execute("SELECT * FROM subscriptions") as cur:
        rows = await cur.fetchall()
    assert [(r["ws_id"], int(r["tg_id"]), r["plan_code"]) for r in rows] == [
        ("ws_first", 1, "week")
    ]
    async with conn.execute("SELECT * FROM subscriptions_legacy_orphans") as cur:
        orphans = await cur.fetchall()
    assert [(int(r["tg_id"]), r["plan_code"]) for r in orphans] == [(2, "month")]

    # The money invariant is untouched: balance == sum(ledger).
    async with conn.execute("SELECT COALESCE(SUM(amount), 0) AS n FROM wallet_ledger") as cur:
        assert int((await cur.fetchone())["n"]) == 40
    # The trial flag still exists and stays keyed by the user.
    async with conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='subscription_trials'"
    ) as cur:
        assert "tg_id" in (await cur.fetchone())["sql"]
    await db.close()
