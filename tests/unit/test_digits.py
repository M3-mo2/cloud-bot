"""TASK digits — every digit in the rendered UI is ASCII, in every locale.

His rule, verbatim: «انا مش عايز اي ارقام عربي خالص فالواجهه حتي لو كنت مخلي
اللغه عربي».

The guard is deliberately in two halves:

* the **catalog** walk catches Arabic-Indic digits typed as content; and
* the **rendered** walk catches digits reintroduced by a formatter or a screen.

Both halves run for every catalog and for every screen in ``SCREEN_BUILDERS`` in
both locales, and the rendered walk checks the HTML *and* every button label.
"""

from __future__ import annotations

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.i18n.catalog import CATALOGS
from cloudbot.presentation.i18n.formatters import assert_no_arabic_digits, has_arabic_digits

LOCALES = (Locale.EN, Locale.AR)

#: Screens that exercise the numeric formatters (bytes, percent, dates, prices).
FORMATTER_SCREENS = (
    "wallet",
    "wallet_no_plan",
    "wallet_expired",
    "wallet_plans",
    "wallet_packs",
    "wallet_receipt",
    "ws_detail",
    "metrics",
    "admin_user",
    "admin_overview",
)


def _buttons(screen) -> list:
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or []
    return [button for row in rows for button in row]


def _assert_ascii_digits(text: str, where: str) -> None:
    try:
        assert_no_arabic_digits(text)
    except AssertionError as exc:
        raise AssertionError(f"{exc} (screen: {where})") from exc


@pytest.mark.parametrize("locale", LOCALES)
def test_catalog_contains_no_arabic_indic_digits(locale):
    """No catalog value may carry an Arabic-Indic digit, in either locale."""
    module = CATALOGS[locale]
    offenders: list[str] = []
    for key, value in module.MESSAGES.items():
        if has_arabic_digits(value):
            offenders.append(f"{key}: {value!r}")
    for key, forms in module.PLURALS.items():
        for category, value in forms.items():
            if has_arabic_digits(value):
                offenders.append(f"{key}[{category}]: {value!r}")
    assert not offenders, f"{locale.value} catalog has Arabic-Indic digits: {offenders}"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_rendered_screen_uses_ascii_digits(locale):
    """Render the whole product; no HTML and no button label may carry ٠–٩."""
    offenders: list[str] = []
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        screen = screen_catalog.build(name, locale)
        if has_arabic_digits(screen.doc.html):
            offenders.append(f"{name}: HTML")
        for button in _buttons(screen):
            if has_arabic_digits(button.text):
                offenders.append(f"{name}: button {button.text!r}")
    assert not offenders, f"{locale.value} screens with Arabic-Indic digits: {offenders}"


@pytest.mark.parametrize("name", FORMATTER_SCREENS)
@pytest.mark.parametrize("locale", LOCALES)
def test_price_and_formatter_screens_use_ascii_digits(name, locale):
    """The wallet/plan prices and the bytes/percent/datetime screens specifically."""
    screen = screen_catalog.build(name, locale)
    _assert_ascii_digits(screen.doc.html, f"{name}/{locale.value} HTML")
    for button in _buttons(screen):
        _assert_ascii_digits(button.text, f"{name}/{locale.value} button")


@pytest.mark.parametrize("locale", LOCALES)
def test_dev_screens_use_ascii_digits(locale):
    """The /dev inspector renders real screen HTML too, so it is covered."""
    from cloudbot.presentation.screens import dev_goldens

    offenders: list[str] = []
    for name, build in dev_goldens.SCREEN_BUILDERS.items():
        screen = build(locale)
        if has_arabic_digits(screen.doc.html):
            offenders.append(f"{name}: HTML")
        for button in _buttons(screen):
            if has_arabic_digits(button.text):
                offenders.append(f"{name}: button {button.text!r}")
    assert not offenders, f"{locale.value} dev screens with Arabic-Indic digits: {offenders}"
