"""The section registry drives the menu (task §2A, §6 proof 3).

Registering a fake section must make it appear in the built menu, so the
extension point cannot silently rot into a hand-written list.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.admin import AdminView
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.admin import registry
from cloudbot.presentation.screens.admin import overview as overview_mod
from cloudbot.presentation.screens.admin.section import AdminSection, SectionRegistry
from cloudbot.presentation.screens.screen import Screen


def _fake_section() -> AdminSection:
    return AdminSection(
        key="fake_test_section",
        title_key="admin.section.audit",
        order=999,
        icon="🧪",
        build=lambda view: Screen(doc=RichDoc(view.locale)),
    )


def test_menu_is_generated_from_the_registry_in_order():
    sections = registry.all()
    assert [s.key for s in sections][:2] == ["overview", "users"]
    assert all(s.order <= sections[i + 1].order for i, s in enumerate(sections[:-1]))


def test_registering_a_fake_section_makes_it_appear_in_the_menu():
    fake = _fake_section()
    registry.register(fake)
    try:
        view = AdminView(locale=Locale.EN)
        screen = overview_mod.overview_screen(view)
        labels = [b.text for row in screen.markup.inline_keyboard for b in row]
        assert any("🧪" in label for label in labels), labels
        # And it is in the registry, not a hardcoded list.
        assert fake in registry.all()
    finally:
        registry.unregister(fake.key)


def test_a_second_registry_is_independent():
    other = SectionRegistry()
    assert other.all() == []
    other.register(_fake_section())
    assert [s.key for s in other.all()] == ["fake_test_section"]
    # The process-wide registry is untouched.
    assert "fake_test_section" not in [s.key for s in registry.all()]


def test_registering_the_same_key_replaces_not_duplicates():
    other = SectionRegistry()
    other.register(_fake_section())
    replaced = AdminSection(
        key="fake_test_section",
        title_key="admin.section.users",
        order=1,
        icon="✅",
        build=lambda view: Screen(doc=RichDoc(view.locale)),
    )
    other.register(replaced)
    assert [s.key for s in other.all()] == ["fake_test_section"]
    assert other.all()[0].icon == "✅"
