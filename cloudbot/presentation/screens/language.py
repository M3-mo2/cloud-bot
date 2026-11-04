"""Onboarding and the language picker.

The onboarding card is the one screen sent with ``is_rtl=False`` — it is
inherently bilingual.
"""

from __future__ import annotations

from cloudbot.core.locale import LOCALE_LABELS, Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens.screen import Screen


def language_card(locale: Locale) -> Screen:
    """The onboarding picker: bilingual, no back button, sent ``is_rtl=False``."""
    doc = RichDoc(Locale.EN)
    doc.h(2, catalog.t("lang.title", locale))
    doc.p(catalog.t("lang.prompt_ar", locale))
    doc.p(catalog.t("lang.change_later_en", locale))
    doc.p(catalog.t("lang.change_later_ar", locale))
    markup = inline_markup(
        [
            [
                inline("\U0001f1f8\U0001f1e6 العربية", "lang:ar", intent=Intent.PRIMARY),
                inline("\U0001f1ec\U0001f1e7 English", "lang:en", intent=Intent.PRIMARY),
            ],
        ]
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def language_picker(locale: Locale, *, inside=None) -> Screen:
    """Mid-session picker: shows the current language disabled, plus Back."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("lang.title", locale))
    doc.p(catalog.t("lang.prompt_ar", locale))

    def lang_button(target: Locale):
        label = LOCALE_LABELS[target]
        if target is locale:
            marker = catalog.t("lang.current_marker", locale, label=label)
            return inline(marker, f"lang:{target.value}", intent=Intent.PRIMARY, disabled=True)
        return inline(label, f"lang:{target.value}", intent=Intent.PRIMARY)

    markup = inline_markup(
        [
            [lang_button(Locale.AR), lang_button(Locale.EN)],
            *nav.nav_rows(locale, "language", inside=inside),
        ]
    )
    return Screen(doc=doc, markup=markup)
