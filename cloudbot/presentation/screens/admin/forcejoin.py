"""Admin section: «اشتراكات إجبارية» — the mandatory-subscription gate.

Pure screen (no I/O): it renders ``AdminView.forcejoin_chats`` and the global
switch. A chat the bot does **not** administer is a red warning — the gate lets
users through there instead of locking them out, so the panel must show it.

The chat list is **blocks**: a bold heading per chat with its fact line and its own
*Enable/Disable* + *Remove* buttons straight under it, never a four-column table.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.forcejoin import ForceJoinChat
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import fragment
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import (
    add_block_header,
    num,
    subject,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def forcejoin_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)

    enabled = view.forcejoin_enabled
    chats = view.forcejoin_chats
    add_block_header(
        doc,
        subject(locale, "admin.fj.title"),
        catalog.t("admin.fj.chat_count", locale, n=num(len(chats), locale)),
        locale,
    )

    doc.quote(
        catalog.t("admin.fj.global_on" if enabled else "admin.fj.global_off", locale)
    )

    if not chats:
        doc.paragraph(catalog.t("admin.fj.empty", locale))
    else:
        for chat in chats:
            doc.heading(f"{chat.label}", size=3)
            doc.paragraph(
                " · ".join(
                    [
                        catalog.t("admin.fj.bot_admin_fact", locale)
                        if chat.bot_is_admin
                        else catalog.t("admin.fj.bot_not_admin_fact", locale),
                        catalog.t(f"admin.fj.kind.{chat.kind}", locale),
                        catalog.t("admin.fj.state_on" if chat.enabled else "admin.fj.state_off", locale),
                        f"@{chat.username}" if chat.username else str(chat.chat_id),
                    ]
                )
            )
            label = _short(chat, locale)
            toggle = catalog.t(
                "admin.fj.disable" if chat.enabled else "admin.fj.enable", locale, chat=label
            )
            remove = catalog.t("admin.fj.remove", locale, chat=label)
            doc.buttons(
                [
                    (
                        toggle,
                        admin_cb.forcejoin_toggle(chat.chat_id),
                        None if chat.enabled else "success",
                    ),
                    (remove, admin_cb.forcejoin_remove(chat.chat_id), "danger"),
                ]
            )
        broken = [c for c in chats if not c.bot_is_admin]
        if broken:
            doc.paragraph(catalog.t("admin.fj.broken_warning", locale, count=num(len(broken), locale)))

    doc.paragraph(catalog.t("admin.fj.add_hint", locale))

    kb: list[list] = []
    kb.append(
        [
            inline(
                catalog.t("admin.fj.add", locale),
                admin_cb.forcejoin_add(),
                intent=Intent.SUCCESS,
            ),
            inline(
                catalog.t("admin.fj.check_me", locale),
                admin_cb.forcejoin_check(),
                intent=Intent.PRIMARY,
            ),
        ]
    )
    kb.append(
        [
            inline(
                catalog.t(
                    "admin.fj.switch_off" if enabled else "admin.fj.switch_on", locale
                ),
                admin_cb.forcejoin_switch(not enabled),
                intent=Intent.DANGER if enabled else Intent.SUCCESS,
            )
        ]
    )
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _short(chat: ForceJoinChat, locale) -> str:
    """A chat label short enough for a button (the title, else @username)."""
    label = chat.label
    return label if len(label) <= 24 else f"{label[:23]}…"


def add_prompt(view: AdminView) -> Screen:
    """The one-message add: the admin sends a @username / id / t.me link."""
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.fj.add_title", locale))
    doc.p(catalog.t("admin.fj.add_prompt", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("forcejoin"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )


def check_result_screen(
    view: AdminView,
    *,
    rows: list[tuple[str, bool, bool]],
    ok: bool,
) -> Screen:
    """The admin's end-to-end proof: every required chat with a measured verdict.

    ``rows`` is ``(label, subscribed, unknown)`` — ``unknown`` is honest, never
    reported as "not subscribed".
    """
    locale = view.locale
    doc = RichDoc(locale)
    doc.h(2, catalog.t("admin.fj.check_title", locale))
    doc.blockquote(
        fragment(
            catalog.t(
                "admin.fj.check_ok" if ok else "admin.fj.check_missing",
                locale,
                count=len(rows),
            )
        )
    )
    table = []
    for label, subscribed, unknown in rows:
        if unknown:
            state = catalog.t("admin.fj.check_unknown", locale)
        elif subscribed:
            state = catalog.t("admin.fj.check_subscribed", locale)
        else:
            state = catalog.t("admin.fj.check_not_subscribed", locale)
        table.append([label, state])
    if table:
        doc.table(
            [catalog.t("admin.fj.col.chat", locale), catalog.t("admin.fj.col.result", locale)],
            table,
        )
    else:
        doc.p(catalog.t("admin.fj.check_none", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("forcejoin"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )


register(
    AdminSection(
        key="forcejoin",
        title_key="admin.section.forcejoin",
        order=35,
        icon="🔗",
        build=forcejoin_screen,
    )
)
