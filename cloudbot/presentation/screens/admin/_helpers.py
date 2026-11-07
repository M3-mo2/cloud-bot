"""Shared helpers for admin-panel screens (formatting + the banner)."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.admin import AdminView
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_datetime, format_relative, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.screen import Screen


def num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def esc_id(value: str):
    from cloudbot.presentation.rich.tags import code

    return code(value)


def entered_banner(view: AdminView) -> str:
    """The persistent banner shown while an admin is inside a workspace."""
    if view.entered is None:
        return ""
    return catalog.t(
        "admin.entered.banner",
        view.locale,
        ws_id=view.entered.ws_id,
        owner=view.entered.owner_tg_id,
    )


def add_entered_banner(doc: RichDoc, view: AdminView) -> None:
    if view.entered is not None:
        doc.blockquote(entered_banner(view))


def prepend_banner(screen: Screen, ctx) -> None:
    """Add the entered-mode banner + leave button to any screen (one seam).

    ``ctx`` is a ``UserContext``; using a loose parameter avoids a
    presentation→middleware import. Called by ``handlers/render.show_screen`` for
    every screen an admin reaches while entered.
    """
    text = catalog.t(
        "admin.entered.banner",
        ctx.locale,
        ws_id=ctx.entered_ws_id,
        owner=ctx.entered_owner_tg_id,
    )
    screen.doc.prepend_text(f"⚠️ {text}")
    leave = inline(
        catalog.t("admin.entered.leave", ctx.locale), admin_cb.leave(), intent=Intent.PRIMARY
    )
    if screen.markup is None:
        screen.markup = inline_markup([[leave]])
    else:
        screen.markup.inline_keyboard.append([leave])


def leave_entered_row(locale: Locale):
    return [inline(catalog.t("admin.entered.leave", locale), admin_cb.leave(), intent=Intent.PRIMARY)]


def user_display(summary_display: str, tg_id: int, locale: Locale) -> str:
    name = (summary_display or "").strip()
    if name:
        return f"{name} #{num(tg_id, locale)}"
    return f"#{num(tg_id, locale)}"


def rule(locale: Locale) -> str:
    """The one section rule every admin screen opens with."""
    return catalog.t("admin.rule", locale)


def subject(locale: Locale, title_key: str) -> str:
    """A section's subject without its leading emoji (the header adds ``✦``)."""
    text = catalog.t(title_key, locale)
    head, sep, rest = text.partition(" ")
    if sep and not any(ch.isalnum() for ch in head):
        return rest
    return text


def header_line(title: str, fact: str) -> str:
    """``✦ subject ⟸ the number that matters`` — the one glance that orients."""
    return f"✦ {title} ⟸ {fact}"


def add_html_header(doc: RichDoc, title: str, fact: str, locale: Locale) -> None:
    """rule / ✦ title ⟸ fact / rule, in the html form (no in-body buttons)."""
    doc.p(rule(locale))
    doc.h(2, header_line(title, fact))
    doc.p(rule(locale))


def add_block_header(doc: RichDoc, title: str, fact: str, locale: Locale) -> None:
    """rule / ✦ title ⟸ fact / rule, in the blocks form (in-body buttons)."""
    doc.paragraph(rule(locale))
    doc.heading(header_line(title, fact), size=2)
    doc.paragraph(rule(locale))


def prepend_notice(doc: RichDoc, text: str) -> None:
    """Put a plain notice at the top of either an html or a blocks document.

    A block screen cannot take an html fragment (Telegram allows one form), so the
    notice becomes a paragraph block there. One seam for every banner/notice the
    admin panel prepends.
    """
    doc.prepend_text(text)


__all__ = [
    "num",
    "esc_id",
    "entered_banner",
    "add_entered_banner",
    "leave_entered_row",
    "user_display",
    "rule",
    "subject",
    "header_line",
    "add_html_header",
    "add_block_header",
    "prepend_notice",
    "format_datetime",
    "format_relative",
    "catalog",
    "inline",
    "inline_markup",
    "Intent",
    "RichDoc",
]
