"""The bilingual ``Locale`` type and its labels.

Lives in ``core`` so that ``core``, ``domain`` and ``presentation`` can all use
it without depending on aiogram or the translation system.
"""

from __future__ import annotations

from enum import Enum


class Locale(str, Enum):
    """The supported UI locales: Arabic (RTL) and English (LTR)."""

    AR = "ar"
    EN = "en"


DEFAULT_LOCALE = Locale.EN


def is_rtl(locale: Locale) -> bool:
    """Return True for right-to-left locales."""
    return locale is Locale.AR


#: Each language shown in its own script, regardless of the current locale.
LOCALE_LABELS: dict[Locale, str] = {
    Locale.AR: "العربية",
    Locale.EN: "English",
}


def coerce_locale(value: object) -> Locale:
    """Best-effort conversion of a stored/engine value to a ``Locale``."""
    if isinstance(value, Locale):
        return value
    try:
        return Locale(str(value))
    except ValueError:
        return DEFAULT_LOCALE
