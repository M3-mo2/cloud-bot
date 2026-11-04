"""The admin-panel section registry.

A section is declared as **data** (a ``AdminSection`` descriptor), never as an
``if`` branch in a menu builder. The main menu is generated from the registry, so
adding a section is one module plus one ``register(...)`` call — the menu code is
untouched. ``tests/unit/test_admin_registry.py`` proves this by registering a fake
section and asserting it appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from cloudbot.core.locale import Locale
from cloudbot.presentation.screens.screen import Screen

#: A builder receives whatever read model its screen needs plus the locale, and
#: returns a pure ``Screen``. The panel passes the view model it already built.
SectionBuilder = Callable[..., Screen]


@dataclass(frozen=True)
class AdminSection:
    key: str
    title_key: str
    order: int
    icon: str
    build: SectionBuilder
    #: Whether this section shows as a button on the main menu.
    in_menu: bool = True

    def resolve_title(self, locale: Locale) -> str:
        from cloudbot.presentation.i18n.catalog import catalog

        return f"{self.icon} {catalog.t(self.title_key, locale)}"


class SectionRegistry:
    """Collects sections and orders them deterministically."""

    def __init__(self) -> None:
        self._sections: list[AdminSection] = []

    def register(self, section: AdminSection) -> AdminSection:
        self._sections = [s for s in self._sections if s.key != section.key]
        self._sections.append(section)
        return section

    def unregister(self, key: str) -> None:
        self._sections = [s for s in self._sections if s.key != key]

    def all(self) -> list[AdminSection]:
        return sorted(self._sections, key=lambda s: (s.order, s.key))

    def menu(self) -> list[AdminSection]:
        return [s for s in self.all() if s.in_menu]


#: The process-wide registry. Section modules call ``registry.register(...)`` at
#: import time (see ``admin/__init__.py``).
registry = SectionRegistry()


def register(section: AdminSection) -> AdminSection:
    return registry.register(section)
