"""The catalog: key + locale → string; key parity is tested.

Catalogs are plain modules with ``MESSAGES`` and ``PLURALS`` dicts. A missing
key renders the key itself (loud) in DEV/DEBUG and a safe fallback in PRODUCTION
— never a crash.
"""

from __future__ import annotations

from typing import Any

from cloudbot.core.locale import Locale

from cloudbot.presentation.i18n.catalogs import ar as _ar
from cloudbot.presentation.i18n.catalogs import en as _en

#: locale → module-like container.
CATALOGS: dict[Locale, Any] = {
    Locale.EN: _en,
    Locale.AR: _ar,
}


class Catalog:
    def t(self, key: str, locale: Locale, /, **args: Any) -> str:
        messages: dict[str, str] = CATALOGS[locale].MESSAGES
        template = messages.get(key)
        if template is None:
            return key
        try:
            return template.format(**args) if args else template
        except (KeyError, IndexError):
            return template

    def has(self, key: str, locale: Locale) -> bool:
        return key in CATALOGS[locale].MESSAGES

    def plural(self, key: str, n: int, locale: Locale) -> str:
        from cloudbot.presentation.i18n.formatters import pluralize

        return pluralize(key, n, locale)


catalog = Catalog()


def keys_for(locale: Locale) -> set[str]:
    return set(CATALOGS[locale].MESSAGES.keys())


def plural_keys_for(locale: Locale) -> set[str]:
    return set(CATALOGS[locale].PLURALS.keys())
