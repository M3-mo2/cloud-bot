"""TASK wallet-home: the wallet is the money home, and its history is a screen.

Each test here would fail before the task:

* the wallet screen never rendered the balance as its lead, and the history
  screen did not exist at all;
* nothing rendered the domain's ledger, so a user could not see past movements;
* a wallet with two workspaces on different plans had to show two states — the
  ``planws`` acceptance case;
* a refunded payment had to be visibly distinct from a normal one.

The raw-enum guard is the golden suite's ``RAW_TOKENS`` idea applied to this
family of screens: a movement's ``kind`` and a subscription's ``source``/``state``
are internal discriminators and must never surface as text.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain.billing import StarsPayment
from cloudbot.domain.subscription import (
    Movement,
    Subscription,
    SubscriptionSource,
    SubscriptionState,
    SubscriptionStatus,
    derive_status,
)
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.pagination import PAGE_SIZE
from cloudbot.presentation.screens import wallet as wallet_screens
from cloudbot.presentation.screens.wallet import WalletView

NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
LOCALES = (Locale.EN, Locale.AR)

#: Every raw enum value that must never appear as visible text.
RAW_ENUM_TOKENS = (
    # MovementKind
    "credit",
    "debit",
    "admin_set",
    "refund",
    "reversal",
    # SubscriptionSource
    "trial",
    "stars",
    "admin",
    # SubscriptionState
    "trialing",
    "active",
    "expired",
    "none",
)


def _active_subscription(plan_code: str = "month", *, days: int = 28) -> Subscription:
    return Subscription(
        tg_id=6508129575,
        plan_code=plan_code,
        started_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=days),
        source=SubscriptionSource.STARS,
    )


def _view(
    *,
    ledger: tuple[Movement, ...] = (),
    payments: tuple[StarsPayment, ...] = (),
    workspaces=None,
) -> WalletView:
    subscription = _active_subscription()
    status = derive_status(subscription, NOW)
    return WalletView(
        locale=Locale.EN,
        tg_id=6508129575,
        balance=350,
        subscription=subscription,
        status=status,
        now=NOW,
        workspaces=workspaces if workspaces is not None else (("demo", subscription, status),),
        ledger=ledger,
        payments=payments,
    )


def _movement(index: int, *, kind: str = "credit", amount: int = 10) -> Movement:
    return Movement(
        tg_id=6508129575,
        kind=kind,  # type: ignore[arg-type]
        amount=amount,
        reason="seed",
        at=NOW - timedelta(hours=index),
        actor_tg_id=777,
    )


def _payment(*, refunded_at=None, charge_id: str = "ch_normal") -> StarsPayment:
    return StarsPayment(
        tg_id=6508129575,
        pack_code="stars_150",
        stars=150,
        charge_id=charge_id,
        provider_charge_id="",
        created_at=NOW - timedelta(days=2),
        refunded_at=refunded_at,
    )


def _visible_text(html: str) -> str:
    """The rendered text with tags removed (attributes are not user-visible)."""
    return re.sub(r"<[^>]+>", " ", html)


# -- 1. the wallet leads with the balance and one line per workspace ----------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_wallet_leads_with_the_balance(locale):
    view = _view()
    view = WalletView(**{**view.__dict__, "locale": locale})
    html = wallet_screens.wallet_screen(view).doc.html
    balance = catalog.t(
        "wallet.balance_line",
        locale,
        stars=catalog.t("wallet.stars", locale, n="350"),
    )
    assert balance in html
    # And it is above the workspace table: the number he asked to see comes first.
    assert html.index(balance) < html.index(catalog.t("wallet.ws.col.workspace", locale))


@pytest.mark.parametrize("locale", LOCALES)
def test_two_workspaces_on_different_plans_show_two_different_states(locale):
    active = _active_subscription("month", days=28)
    lapsed = Subscription(
        tg_id=6508129575,
        plan_code="week",
        started_at=NOW - timedelta(days=10),
        expires_at=NOW - timedelta(days=3),
        source=SubscriptionSource.STARS,
    )
    view = _view(
        workspaces=(
            ("demo", active, derive_status(active, NOW)),
            ("scraper", lapsed, derive_status(lapsed, NOW)),
        )
    )
    view = WalletView(**{**view.__dict__, "locale": locale})
    html = wallet_screens.wallet_screen(view).doc.html
    # Both names, and two different states: one live, one expired.
    assert "demo" in html and "scraper" in html
    assert catalog.t("wallet.ws.active", locale, plan=catalog.t("plan.month", locale), days="28") in html
    assert catalog.t("wallet.ws.expired", locale, plan=catalog.t("plan.week", locale)) in html


# -- 2. history: newest first, signed amounts, one shared pager ---------------
def test_history_lists_movements_newest_first_with_signed_amounts():
    ledger = (
        _movement(3, kind="credit", amount=500),
        _movement(1, kind="debit", amount=-150),
    )
    screen = wallet_screens.history_screen(_view(ledger=ledger))
    html = screen.doc.html
    # The newest movement (index 1) is above the oldest (index 3).
    assert html.index("-150") < html.index("+500")
    assert "<code>+500</code>" in html
    assert "<code>-150</code>" in html


def test_history_merges_payments_with_movements_newest_first():
    ledger = (_movement(1, kind="credit", amount=500),)
    payments = (_payment(refunded_at=NOW - timedelta(days=1)),)
    html = wallet_screens.history_screen(_view(ledger=ledger, payments=payments)).doc.html
    # The payment was 2 days ago; the movement 1 hour ago, so the movement is first.
    assert html.index("+500") < html.index("150 Stars")


def test_history_pages_at_page_size():
    ledger = tuple(_movement(index, amount=10 + index) for index in range(PAGE_SIZE + 2))
    first = wallet_screens.history_screen(_view(ledger=ledger), page=0)
    second = wallet_screens.history_screen(_view(ledger=ledger), page=1)
    # Page 1 holds exactly PAGE_SIZE data rows; page 2 holds the remainder.
    assert first.doc.html.count("<tr>") - 1 == PAGE_SIZE
    assert second.doc.html.count("<tr>") - 1 == 2
    assert "Page 1 of 2" in first.doc.html
    assert "Page 2 of 2" in second.doc.html
    nexts = [
        b.callback_data
        for row in first.markup.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("pay:history")
    ]
    assert nexts == ["pay:history:1"]


# -- 3. the empty state is a sentence, not an empty table --------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_an_empty_ledger_renders_the_empty_state_not_an_empty_table(locale):
    view = _view()
    view = WalletView(**{**view.__dict__, "locale": locale})
    html = wallet_screens.history_screen(view).doc.html
    assert catalog.t("wallet.history.empty", locale) in html
    assert catalog.t("wallet.history.empty_hint", locale) in html
    assert "<table" not in html


# -- 4. refunded vs normal payments ------------------------------------------
def test_a_refunded_payment_reads_as_refunded_and_a_normal_one_does_not():
    refunded = wallet_screens.history_screen(
        _view(payments=(_payment(refunded_at=NOW - timedelta(days=1), charge_id="ch_r"),))
    ).doc.html
    normal = wallet_screens.history_screen(
        _view(payments=(_payment(charge_id="ch_n"),))
    ).doc.html
    assert catalog.t("wallet.history.refunded", Locale.EN) in refunded
    assert catalog.t("wallet.history.charged", Locale.EN) not in refunded
    assert catalog.t("wallet.history.charged", Locale.EN) in normal
    assert catalog.t("wallet.history.refunded", Locale.EN) not in normal


def test_the_full_charge_id_never_reaches_the_history():
    """A charge id is internal: the history shows the pack and state, not the id."""
    html = wallet_screens.history_screen(
        _view(payments=(_payment(charge_id="ch_secret_0123456789"),))
    ).doc.html
    assert "ch_secret_0123456789" not in html


# -- 5. no raw enum may surface ----------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_no_raw_enum_reaches_a_wallet_or_history_screen(locale):
    ledger = (
        _movement(5, kind="credit", amount=500),
        _movement(4, kind="debit", amount=-150),
        _movement(3, kind="admin_set", amount=20),
        _movement(2, kind="refund", amount=10),
        _movement(1, kind="reversal", amount=-10),
    )
    payments = (_payment(refunded_at=NOW),)
    view = _view(ledger=ledger, payments=payments)
    view = WalletView(**{**view.__dict__, "locale": locale})
    screens = [
        wallet_screens.wallet_screen(view),
        wallet_screens.history_screen(view),
    ]
    for screen in screens:
        text = _visible_text(screen.doc.html)
        for token in RAW_ENUM_TOKENS:
            assert not re.search(rf"\b{re.escape(token)}\b", text), (
                f"raw enum value {token!r} reached the screen: {text!r}"
            )
