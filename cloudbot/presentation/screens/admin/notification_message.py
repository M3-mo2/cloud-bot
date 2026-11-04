"""Notification message screens (pure; used by the dispatcher)."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.events import NotificationMessage
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import fragment


def _num(n: int, locale: Locale) -> str:
    return localize_digits(str(n), locale)


def notification_message(message: NotificationMessage, locale: Locale) -> RichDoc:
    doc = RichDoc(locale)
    doc.h(2, f"{message.severity.emoji} {catalog.t('admin.notify.delivery_title', locale)}")
    doc.p(catalog.t(message.category.label_key, locale))
    doc.p(catalog.t(message.summary_key, locale, **message.params))
    if message.count > 1:
        doc.p(catalog.t("admin.notify.delivery_count", locale, n=_num(message.count, locale)))
    return doc


def gap_message(count: int, locale: Locale) -> RichDoc:
    doc = RichDoc(locale)
    doc.h(2, f"⚠️ {catalog.t('admin.notify.gap_title', locale)}")
    doc.p(catalog.t("admin.notify.gap_body", locale, n=_num(count, locale)))
    return doc


def suppression_message(count: int, locale: Locale) -> RichDoc:
    doc = RichDoc(locale)
    doc.h(2, f"⚠️ {catalog.t('admin.notify.capped_title', locale)}")
    doc.p(catalog.t("admin.notify.capped_body", locale, n=_num(count, locale)))
    return doc


def backwards_clock_message(locale: Locale) -> RichDoc:
    doc = RichDoc(locale)
    doc.h(2, f"⚠️ {catalog.t('admin.notify.backwards_title', locale)}")
    doc.p(catalog.t("admin.notify.backwards", locale))
    return doc
