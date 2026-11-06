"""Locale-aware formatters: bytes, durations, relative time, percent, dates.

No screen formats a number itself.

**Digits are always ASCII ``0-9``, in every locale.** That is the product rule:
Arabic copy, Arabic punctuation and Arabic word order stay exactly as they are,
but a numeral is Latin even when the UI is Arabic — fifty is ``50``, not the
Arabic-Indic form; ``3.9`` not the Arabic decimal separator; ``3%`` not the
Arabic percent sign. Identifiers (``ws_...``) are never passed through here —
they stay ASCII in ``<code>``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from babel.core import Locale as BabelLocale
from babel.dates import format_datetime as _babel_datetime

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import CATALOGS

#: Arabic-Indic (U+0660–U+0669) and Extended Arabic-Indic (U+06F0–U+06F9).
_ARABIC_DIGITS = re.compile(r"[\u0660-\u0669\u06f0-\u06f9]")


def localize_digits(text: str, locale: Locale) -> str:
    """Return ``text`` unchanged — digits are ASCII in every locale.

    Kept on purpose: it is the single seam a future locale would use if it ever
    needed different numerals, and it is the name the tests assert against. Today
    it is a pass-through so that no caller (there are over a hundred) has to
    change and no formatter can accidentally reintroduce Arabic-Indic digits.
    """
    return text


def has_arabic_digits(text: str) -> bool:
    """Whether ``text`` contains an Arabic-Indic digit (either Unicode block)."""
    return _ARABIC_DIGITS.search(text) is not None


def assert_no_arabic_digits(text: str) -> None:
    """Fail if ``text`` contains an Arabic-Indic or Extended Arabic-Indic digit.

    The test-facing helper shared by the catalog guard, the rendered-screen guard
    and the formatter unit tests, so all three cannot drift apart.
    """
    match = _ARABIC_DIGITS.search(text)
    assert match is None, f"Arabic-Indic digit {match.group()!r} in {text!r}"


def _localize(text: str, locale: Locale) -> str:
    """Locale punctuation seam — deliberately a pass-through today.

    Decision: punctuation is **not** swapped automatically any more.

    * the ASCII full stop → Arabic decimal separator (U+066B) and ``%`` →
      Arabic percent sign (U+066A) replacements are gone: a decimal point and a
      percent sign are not numerals, and the rule is ``3.9 MB`` / ``3%`` in
      Arabic too.
    * ``,`` → ``،`` is gone as well. The helper cannot tell a date/list comma
      from a number's thousands separator, so swapping would turn a formatted
      ``1,200`` into ``1،200``. Arabic catalogs already type ``،`` where they
      mean it; a number keeps its ASCII thousand separator.

    Keeping the function makes the seam explicit for a future locale that has a
    different convention, without changing any caller.
    """
    return text


# -- integers ----------------------------------------------------------------
def format_int(value: int, locale: Locale) -> str:
    """Grouped ASCII integer: ``1200`` → ``1,200`` in every locale.

    The separator is always the ASCII comma. It is *not* swapped to the Arabic
    comma (U+060C): that would change a number into punctuation the user could
    not parse as a number.
    """
    return f"{value:,}"


# -- bytes -------------------------------------------------------------------
_UNITS = ("B", "KB", "MB", "GB", "TB")
_ARABIC_UNITS = {
    "B": "بايت",
    "KB": "كيلوبايت",
    "MB": "ميغابايت",
    "GB": "غيغابايت",
    "TB": "تيرابايت",
}


def _human_bytes(n: float) -> tuple[str, str]:
    if n < 1024:
        return f"{int(n)}", "B"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)}", "B"
            return _trim(value), unit
        value /= 1024
    return _trim(value), "TB"


def _trim(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"
    return f"{value:.1f}"


def format_bytes(n: int | float, locale: Locale) -> str:
    number, unit = _human_bytes(float(n))
    if locale is Locale.AR:
        # The unit is translated; the number keeps ASCII digits and its ASCII
        # decimal point (``3.9``), because a numeral is Latin in every locale.
        return f"{number} {_ARABIC_UNITS[unit]}"
    return f"{number} {unit}"


def format_bytes_pair(
    used: int | float,
    limit: int | float,
    locale: Locale,
    *,
    percent: float | None = None,
) -> str:
    left = format_bytes(used, locale)
    right = format_bytes(limit, locale)
    base = f"{left} / {right}"
    if percent is None:
        return base
    pct = format_percent(percent, locale)
    return f"{base} ({pct})"


# -- percent -----------------------------------------------------------------
def format_percent(percent: float, locale: Locale) -> str:
    rounded = round(percent)
    # ``%`` stays ASCII in Arabic: a percent sign is punctuation, not a numeral.
    return f"{rounded}%"


# -- durations ---------------------------------------------------------------
def format_ms(ms: int, locale: Locale) -> str:
    if locale is Locale.AR:
        return f"{localize_digits(str(ms), locale)} ملّي ثانية"
    return f"{ms} ms"


def format_seconds(seconds: float, locale: Locale) -> str:
    text = f"{seconds:.1f}"
    if locale is Locale.AR:
        return f"{localize_digits(text, locale)} ثانية"
    return f"{text} s"


def format_duration(seconds: float, locale: Locale) -> str:
    """Compact ``2m 05s`` / ``2 د 05 ث`` (same numerals, Arabic units)."""
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}{'h' if locale is Locale.EN else ' س'}")
    if minutes:
        parts.append(f"{minutes:02d}m" if locale is Locale.EN else f"{minutes:02d} د")
    elif hours:
        parts.append("00m" if locale is Locale.EN else "00 د")
    parts.append(f"{secs:02d}s" if locale is Locale.EN else f"{secs:02d} ث")
    text = " ".join(parts)
    return _localize(text, locale)


# -- relative time -----------------------------------------------------------
def format_relative(dt: datetime | None, now: datetime, locale: Locale) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (now - dt).total_seconds()
    return format_relative_seconds(delta, locale)


def format_relative_seconds(delta: float, locale: Locale) -> str:
    future = delta < 0
    secs = abs(int(delta))
    if secs < 45:
        key = "now"
        n = 0
    elif secs < 90:
        key, n = "minute", 1
    elif secs < 3600:
        key, n = "minute", round(secs / 60)
    elif secs < 5400:
        key, n = "hour", 1
    elif secs < 86400:
        key, n = "hour", round(secs / 3600)
    else:
        key, n = "day", round(secs / 86400)
    return _relative_phrase(key, n, locale, future)


def _relative_phrase(key: str, n: int, locale: Locale, future: bool) -> str:
    if locale is Locale.EN:
        if key == "now":
            return "in a moment" if future else "just now"
        unit = {"minute": "min", "hour": "h", "day": "d"}[key]
        if future:
            return f"in {n} {unit}" if n > 1 else f"in 1 {unit}"
        return f"{n} {unit} ago"
    # Arabic
    if key == "now":
        return "بعد قليل" if future else "قبل قليل"
    table = {
        "minute": {1: "قبل دقيقة", 2: "قبل دقيقتين", "few": "قبل {n} دقائق", "many": "قبل {n} دقيقة"},
        "hour": {1: "قبل ساعة", 2: "قبل ساعتين", "few": "قبل {n} ساعات", "many": "قبل {n} ساعة"},
        "day": {1: "قبل يوم", 2: "قبل يومين", "few": "قبل {n} أيام", "many": "قبل {n} يوم"},
    }
    forms = table[key]
    if future:
        return forms.get(1, "").replace("قبل", "خلال") if n == 1 else f"خلال {localize_digits(str(n), locale)}"
    if n in (1, 2):
        return forms[n]
    category = _plural_category(locale, n)
    template = forms.get(category, forms["many"])
    return template.format(n=localize_digits(str(n), locale))


# -- dates -------------------------------------------------------------------
def format_datetime(dt: datetime | None, locale: Locale) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    babel_locale = "en" if locale is Locale.EN else "ar"
    # The Arabic pattern spells its own Arabic comma: punctuation belongs in the
    # pattern, not in a blind post-replace that could hit a numeral. Babel's
    # Arabic locale uses Latin digits by default, so no digit translation here.
    pattern = "d MMM y, HH:mm" if locale is Locale.EN else "d MMM y، HH:mm"
    try:
        text = _babel_datetime(dt, format=pattern, locale=BabelLocale.parse(babel_locale))
    except Exception:  # noqa: BLE001
        text = dt.strftime("%d %b %Y, %H:%M")
        if locale is Locale.AR:
            text = text.replace(",", "،")
    if locale is Locale.AR:
        text = text.replace("\u200f", "").replace("\u200e", "")
    return text


# -- plurals -----------------------------------------------------------------
def _plural_category(locale: Locale, n: int) -> str:
    try:
        return BabelLocale.parse(locale.value).plural_form(n)
    except Exception:  # noqa: BLE001
        return "other"


def pluralize(key: str, n: int, locale: Locale) -> str:
    plurals: dict[str, dict[str, str]] = CATALOGS[locale].PLURALS.get(key, {})
    if not plurals:
        return f"{n}"
    category = _plural_category(locale, n)
    template = plurals.get(category) or plurals.get("other") or plurals.get("many") or ""
    # A count is grouped with the ASCII comma in every locale (``1,200``), never
    # the Arabic comma (``1،200``) and never Arabic-Indic digits.
    rendered = template.format(n=format_int(n, locale))
    return rendered


def format_count(key: str, n: int, locale: Locale) -> str:
    return pluralize(key, n, locale)
