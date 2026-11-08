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
    # TASK digits: Arabic copy, Latin numerals and a Latin decimal point.
    assert "2.3" in ar
    assert "٢" not in ar


def test_format_percent():
    assert formatters.format_percent(3, Locale.EN) == "3%"
    # TASK digits: a percent sign is punctuation, not a numeral; it stays ASCII.
    assert formatters.format_percent(3, Locale.AR) == "3%"


def test_format_duration():
    assert formatters.format_duration(125, Locale.EN) == "02m 05s"
    # TASK digits: same numerals in both locales; only the unit words differ.
    assert formatters.format_duration(125, Locale.AR).startswith("02")


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


# -- TASK digits: Latin numerals in every locale ------------------------------
def test_localize_digits_is_a_pass_through():
    assert formatters.localize_digits("50", Locale.AR) == "50"
    assert formatters.localize_digits("50", Locale.EN) == "50"
    assert formatters.localize_digits("0123456789", Locale.AR) == "0123456789"


def test_localize_stops_swapping_decimal_and_percent():
    # A decimal point and a percent sign are punctuation, not numerals.
    assert formatters._localize("3.9 MB", Locale.AR) == "3.9 MB"
    assert formatters._localize("3%", Locale.AR) == "3%"
    # Arabic text with no digits is untouched.
    arabic = "تم إنشاء المساحة بنجاح"
    assert formatters._localize(arabic, Locale.AR) == arabic


def test_thousands_separator_stays_ascii():
    # The catalog plural keeps its Arabic wording; only the number changes, and
    # its thousands separator is the ASCII comma — never ``،`` and never ``١٢٠٠``.
    rendered = formatters.format_count("count.workspaces", 1200, Locale.AR)
    assert rendered.startswith("1,200")
    assert "1،200" not in rendered
    assert formatters.format_count("count.workspaces", 1200, Locale.EN) == "1,200 workspaces"


def test_format_int_groups_with_ascii_comma():
    assert formatters.format_int(1200, Locale.EN) == "1,200"
    # TASK digits: the thousands separator is a comma even in Arabic — never
    # the Arabic comma (U+060C), which would stop reading as a number.
    assert formatters.format_int(1200, Locale.AR) == "1,200"


def test_assert_no_arabic_digits_helper():
    formatters.assert_no_arabic_digits("50 و 3.9 MB و 3%")
    with pytest.raises(AssertionError):
        formatters.assert_no_arabic_digits("٥٠")
    with pytest.raises(AssertionError):
        formatters.assert_no_arabic_digits("۱")  # Extended Arabic-Indic


# -- TASK packages-wording: a pip package is a «مكتبة» in Arabic --------------
#: The owner's vocabulary: a Python hosting user installs **libraries**, not
#: "packages". ``حزم`` (plural) and ``حزمة`` (singular) are forbidden in Arabic
#: user-facing copy; «حزمة» contains «حزم», so one check catches both forms.
_FORBIDDEN_PACKAGE_WORDS = ("حزم", "حزمة")


def _arabic_user_facing_strings() -> list[tuple[str, str]]:
    """Every string an Arabic user can actually read, with a label.

    Only **values** are returned: catalog keys are identifiers, never shown to a
    user. ``PLURALS`` values are user-facing too, so they are scanned alongside
    ``MESSAGES``.
    """
    module = CATALOGS[Locale.AR]
    items = list(module.MESSAGES.items())
    for key, forms in module.PLURALS.items():
        items.extend((f"{key}[{category}]", value) for category, value in forms.items())
    return items


def test_arabic_catalogs_call_pip_packages_maktabat():
    """No Arabic user-facing string may still call a pip package a «حزمة».

    Why keys are allowed to keep saying ``package``: a key is an identifier an
    agent reads, not a user; renaming ``package.*`` would churn every screen and
    every golden without changing one word the user sees. Why the English catalog
    is not scanned: "packages" is the correct English word — the owner asked
    about the Arabic only.

    The bite: put «الحزم» back in a screen's Arabic value and this test fails.
    """
    offenders = [
        f"{key}: {value!r}"
        for key, value in _arabic_user_facing_strings()
        if any(word in value for word in _FORBIDDEN_PACKAGE_WORDS)
    ]
    assert not offenders, (
        "Arabic copy still says «حزم»/«حزمة» for a pip package; use "
        f"«مكتبات»/«مكتبة»: {offenders}"
    )


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "لا مكتبات مثبّتة"),
        (1, "مكتبة واحدة مثبّتة"),
        (2, "مكتبتان مثبّتتان"),
        (3, "3 مكتبات مثبّتة"),
        (11, "11 مكتبة مثبّتة"),
    ],
)
def test_arabic_package_count_is_grammatical(n, expected):
    """The four Arabic categories a count can land in render their own form.

    1 is ``one``, 2 is ``two``, 3 is ``few`` and 11 is ``many``; hardcoding a
    single form («{n} مكتبات») reads wrong for 1, 2 and 11 to a native speaker.
    """
    assert formatters.format_count("count.packages", n, Locale.AR) == expected


def test_arabic_rtl_flag_is_still_true():
    from cloudbot.presentation.rich.document import RichDoc

    assert RichDoc(Locale.AR).build().is_rtl is True
    assert RichDoc(Locale.EN).build().is_rtl is False
