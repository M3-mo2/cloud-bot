"""TASK stars-page: the Star page sells time, not four identical prices.

Before this task the screen was a paragraph plus four identical-looking price
buttons, so a reader could not answer the only question they had — *which one do I
need?* — because nothing connected a Star amount to a plan. Each test here fails
against that old screen:

* it had no per-pack line at all, and no button inside the body;
* the pack→plan mapping was not derived anywhere, so it could not follow the
  catalogue;
* the savings percentages did not exist;
* the affordability sentence did not exist (the balance was a flat line);
* a user who already had enough Stars was still offered only packs, never the
  plan they could actually buy.

The mapping is derived from :mod:`cloudbot.domain.plans` at render time; the
savings are the weekly-rate arithmetic ``plans.py`` documents. Neither is a table
of typed strings, which is what ``test_the_mapping_follows_the_catalogue`` proves.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain import plans
from cloudbot.domain.billing import pack, packs
from cloudbot.domain.subscription import SubscriptionState, SubscriptionStatus
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.screens import wallet as wallet_screens
from cloudbot.presentation.screens.wallet import WalletView

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
LOCALES = (Locale.EN, Locale.AR)
NO_PLAN = SubscriptionStatus(SubscriptionState.NONE, False, 0)


def _view(balance: int, locale: Locale = Locale.AR, **kwargs) -> WalletView:
    return WalletView(
        locale=locale,
        tg_id=6508129575,
        balance=balance,
        subscription=None,
        status=NO_PLAN,
        now=NOW,
        **kwargs,
    )


def _body_text(view: WalletView, **kwargs) -> str:
    """Everything the body shows, in whichever form it built (blocks here)."""
    built = wallet_screens.packs_screen(view, **kwargs).doc.build()
    assert built.html is None, "the pack page must build in blocks for in-body buttons"
    parts: list[str] = []
    for block in built.blocks or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        elif text is not None:
            parts.append(getattr(text, "text", "") or "")
        for button in getattr(block, "buttons", None) or []:
            parts.append(button.text)
    return "\n".join(part for part in parts if part)


def _body_buttons(view: WalletView, **kwargs) -> list:
    built = wallet_screens.packs_screen(view, **kwargs).doc.build()
    return [
        button
        for block in built.blocks or []
        for button in (getattr(block, "buttons", None) or [])
    ]


def _savings_percent(stars: int) -> int:
    """The weekly-reference saving, computed independently of the screen.

    Only the packs that mirror a plan exactly have a single period to divide by;
    any other pack (500 buys 10 weeks, which has no commitment discount) is not
    a saving and returns 0.
    """
    week = plans.plan("week")
    reference_rate = week.amount_stars / week.period_days
    days = {plan.amount_stars: plan.period_days for plan in plans.paid_plans()}.get(stars)
    if days is None:
        return 0
    return round((1 - (stars / days) / reference_rate) * 100)


# -- 1. the bite: today's screen has neither the lines nor the buttons -------
@pytest.mark.parametrize("locale", LOCALES)
def test_every_pack_gets_its_plan_time_line_and_its_own_body_button(locale):
    view = _view(4, locale)
    text = _body_text(view)
    buttons = _body_buttons(view)
    pack_buttons = [b for b in buttons if (b.callback_data or "").startswith("pay:pack:")]
    assert len(pack_buttons) == len(packs()), "one in-body button per pack"

    for star_pack in packs():
        line = catalog.t(
            "wallet.pack.line",
            locale,
            stars=star_pack.stars,
            unit=wallet_screens._pack_time_label(star_pack.stars, plans.paid_plans(), locale),
        )
        # The line and its action both exist: this is what the old page lacked.
        assert line in text
        assert any(
            button.callback_data == f"pay:pack:{star_pack.code}" for button in pack_buttons
        ), star_pack.code
        # And the button label is the action, not the bare price.
        label = catalog.t("wallet.pack.buy", locale, stars=star_pack.stars)
        assert any(button.text == label for button in pack_buttons)


def test_the_page_has_no_two_by_two_grid_of_identical_labels():
    """The old page put the four prices in a 2×2 keyboard; the body is per-pack."""
    view = _view(4)
    built = wallet_screens.packs_screen(view).doc.build()
    button_blocks = [
        block for block in built.blocks or [] if getattr(block, "buttons", None)
    ]
    pack_blocks = [
        block
        for block in button_blocks
        if any((b.callback_data or "").startswith("pay:pack:") for b in block.buttons)
    ]
    # One pack button per block — never four in a grid.
    assert all(len(block.buttons) == 1 for block in pack_blocks)
    assert len(pack_blocks) == len(packs())


# -- 2. the mapping is derived, not typed ------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_mapping_follows_the_catalogue(monkeypatch, locale):
    """Change the week's price and the pack that matches it says «week» instead.

    A hardcoded Arabic/English table would keep saying «شهر»/«month» for 150 and
    fail here, which is the drift this guards.
    """
    before = _body_text(_view(4, locale))
    assert wallet_screens._pack_time_label(150, plans.paid_plans(), locale) == (
        "شهر" if locale is Locale.AR else "one month"
    )
    monkeypatch.setitem(plans.PLANS, "week", replace(plans.PLANS["week"], amount_stars=150))
    after = _body_text(_view(4, locale))
    # 150 is now the weekly price, so its unit is the week, not the month.
    assert wallet_screens._pack_time_label(150, plans.paid_plans(), locale) == (
        "أسبوع" if locale is Locale.AR else "one week"
    )
    assert after != before


# -- 3. the savings are computed, not typed ----------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_savings_percentages_are_the_weekly_reference_arithmetic(locale):
    text = _body_text(_view(4, locale))
    for star_pack in packs():
        expected = _savings_percent(star_pack.stars)
        if expected > 0:
            assert catalog.t(
                "wallet.pack.value.save", locale, percent=str(expected)
            ) in text, (star_pack.code, expected)
    # The two commitment packs' numbers, as the task states them.
    week = plans.plan("week")
    month = plans.plan("month")
    year = plans.plan("year")
    reference = week.amount_stars / week.period_days
    assert round((1 - (month.amount_stars / month.period_days) / reference) * 100) == 30
    assert _savings_percent(year.amount_stars) == 54


# -- 4. the affordability line: 0 / 4 / 50 / 1200 ----------------------------
def _affordability_sentence(balance: int) -> str:
    """The whole affordability message: the bold line plus its follow-up."""
    text = _body_text(_view(balance))
    lines = [
        line
        for line in text.splitlines()
        if "أقرب خطة" in line
        or "رصيدك يكفي" in line
        or "ستحتاج" in line
        or "اشترِ نجومًا" in line
        or "أو أضف نجومًا" in line
    ]
    return " ".join(lines)


def test_the_affordability_line_is_four_distinct_honest_sentences():
    sentences = {balance: _affordability_sentence(balance) for balance in (0, 4, 50, 1200)}
    assert len(set(sentences.values())) == 4, sentences
    # 0: not scolded, just the shape of the gap.
    assert "أقرب خطة تحتاج 50 نجمة — باقي 50" in sentences[0]
    assert "ستحتاج نجومًا" in sentences[0]
    # 4: the exact shortfall the owner's live screen had.
    assert "أقرب خطة تحتاج 50 نجمة — باقي 46" in sentences[4]
    # 50 / 1200: what he can buy *now*, not what he lacks.
    assert "رصيدك يكفي خطة أسبوعية" in sentences[50]
    assert "رصيدك يكفي خطة سنوية" in sentences[1200]
    # No sentence calls the reader poor, wrong, or at fault.
    for sentence in sentences.values():
        for scold in ("خطأ", "لا تملك", "عليك", "يجب"):
            assert scold not in sentence, sentence


# -- 5. enough Stars → the plan is offered, not only packs -------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_enough_balance_offers_the_plan_inside_the_body(locale):
    buttons = _body_buttons(_view(1200, locale))
    plan_buttons = [b for b in buttons if (b.callback_data or "").startswith("pay:buy:")]
    assert plan_buttons, "a user who can already pay must be offered the plan"
    assert plan_buttons[0].callback_data == "pay:buy:year"
    text = _body_text(_view(1200, locale))
    assert catalog.t("wallet.packs.afford.ready", locale).format(
        plan=catalog.t("plan.year", locale), needed="1200"
    ) in text


@pytest.mark.parametrize("locale", LOCALES)
def test_a_short_balance_does_not_offer_a_plan_it_cannot_afford(locale):
    buttons = _body_buttons(_view(4, locale))
    assert not [b for b in buttons if (b.callback_data or "").startswith("pay:buy:")]


# -- 6. callbacks: ≤ 64 bytes and a real pack --------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_every_body_button_is_a_real_pack_and_fits_the_callback_cap(locale):
    buttons = _body_buttons(_view(4, locale))
    pack_buttons = [b for b in buttons if (b.callback_data or "").startswith("pay:pack:")]
    assert pack_buttons
    for button in pack_buttons:
        data = button.callback_data
        assert len(data.encode("utf-8")) <= 64, data
        code = data.rsplit(":", 1)[1]
        assert pack(code) is not None, data


# -- 7. the wizard path keeps its exit ---------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_wizard_back_action_returns_to_the_plan_step(locale):
    screen = wallet_screens.packs_screen(_view(4, locale), back_action="ws:continue")
    callbacks = [
        button.callback_data
        for row in screen.markup.inline_keyboard
        for button in row
    ]
    assert callbacks == ["ws:continue"]
    # The body's pack actions are unchanged: only the exit differs.
    assert len(
        [b for b in _body_buttons(_view(4, locale)) if (b.callback_data or "").startswith("pay:pack:")]
    ) == len(packs())


def test_the_wallet_back_action_is_the_default_exit():
    screen = wallet_screens.packs_screen(_view(4))
    callbacks = [
        button.callback_data
        for row in screen.markup.inline_keyboard
        for button in row
    ]
    assert callbacks == ["pay:wallet"]


# -- 8. digits and no leak ---------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_page_carries_only_ascii_digits(locale):
    from cloudbot.presentation.i18n.formatters import has_arabic_digits

    for balance in (0, 4, 50, 1200):
        view = _view(balance, locale)
        built = wallet_screens.packs_screen(view).doc.build()
        text = _body_text(view)
        assert not has_arabic_digits(text), text
        for button in _body_buttons(view):
            assert not has_arabic_digits(button.text), button.text
        for block in built.blocks or []:
            assert not has_arabic_digits(block.model_dump_json(exclude_none=True))
