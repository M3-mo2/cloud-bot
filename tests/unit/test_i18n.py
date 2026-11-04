"""i18n: key parity, plural grid, and formatter correctness."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import (
    CATALOGS,
    keys_for,
    plural_keys_for,
)
from cloudbot.presentation.i18n import formatters


def test_key_parity():
    en = keys_for(Locale.EN)
    ar = keys_for(Locale.AR)
    assert en == ar, {
        "missing_in_ar": sorted(en - ar),
        "missing_in_en": sorted(ar - en),
    }


def test_plural_key_parity():
    assert plural_keys_for(Locale.EN) == plural_keys_for(Locale.AR)


def test_arabic_has_six_plural_forms():
    for key, forms in CATALOGS[Locale.AR].PLURALS.items():
        for category in ("zero", "one", "two", "few", "many", "other"):
            assert category in forms, f"{key} missing {category}"


@pytest.mark.parametrize("n", [0, 1, 2, 3, 11, 100])
@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_plural_grid_renders(locale, n):
    for key in plural_keys_for(locale):
        rendered = formatters.pluralize(key, n, locale)
        assert rendered
        assert "{" not in rendered


def test_format_bytes_english_and_arabic():
    assert formatters.format_bytes(2_411_724, Locale.EN) == "2.3 MB"
    ar = formatters.format_bytes(2_411_724, Locale.AR)
    assert "ميغابايت" in ar
    assert "٢" in ar  # Arabic-Indic digits


def test_format_percent():
    assert formatters.format_percent(3, Locale.EN) == "3%"
    assert formatters.format_percent(3, Locale.AR) == "٣٪"


def test_format_duration():
    assert formatters.format_duration(125, Locale.EN) == "02m 05s"
    assert formatters.format_duration(125, Locale.AR).startswith("٠٢")


def test_format_relative():
    now = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
    earlier = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)
    assert "min" in formatters.format_relative(earlier, now, Locale.EN)
    ar = formatters.format_relative(earlier, now, Locale.AR)
    assert "قبل" in ar


def test_format_datetime():
    dt = datetime(2026, 11, 3, 20, 41, tzinfo=timezone.utc)
    en = formatters.format_datetime(dt, Locale.EN)
    ar = formatters.format_datetime(dt, Locale.AR)
    assert "Nov" in en
    assert "نوفمبر" in ar
    # ids stay ASCII — not tested here, but digits localize in dates
    assert ar != en
