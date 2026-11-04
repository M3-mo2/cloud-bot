"""Declarative admin actions.

An action is a small data entry — ``key``, a catalog label key, an ``Intent``,
``dangerous``/``confirm`` flags and the callback action it wires. A new action is
one entry here plus one handler in ``handlers/admin_panel.py``; the confirmation
screen is generated from the same declaration (``confirmation_screen``), so the
dangerous path can never drift from the action.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.screen import Screen

#: Callback-data prefixes for the panel (the codec owns the wire shape).
ADMIN_ACTION_PREFIX = admin_cb.ADMIN_PREFIX


@dataclass(frozen=True)
class AdminAction:
    key: str
    label_key: str
    intent: Intent = Intent.PRIMARY
    dangerous: bool = False
    confirm: bool = False
    #: The catalog key of the confirmation title, used when ``confirm`` is true.
    confirm_title_key: str = "admin.confirm.title"
    #: The catalog key of the confirmation body; ``{subject}`` is substituted.
    confirm_body_key: str = "admin.confirm.body"

    def ask_callback(self, subject: str) -> str:
        """The button on the row: opens the confirmation (or does it if trivially safe)."""
        if self.confirm:
            return admin_cb.ask(self.key, subject)
        return self.callback(subject)

    def callback(self, subject: str) -> str:
        """The callback that actually performs the action on ``subject``."""
        return admin_cb.action(self.key, subject)

    def label(self, locale: Locale) -> str:
        return catalog.t(self.label_key, locale)


# -- the declarations --------------------------------------------------------
ACTIONS: list[AdminAction] = [
    AdminAction(
        key="enter",
        label_key="admin.action.enter",
        intent=Intent.PRIMARY,
    ),
    AdminAction(
        key="suspend",
        label_key="admin.action.suspend",
        intent=Intent.PRIMARY,
        confirm=True,
        confirm_title_key="admin.action.suspend.confirm_title",
        confirm_body_key="admin.action.suspend.confirm_body",
    ),
    AdminAction(
        key="resume",
        label_key="admin.action.resume",
        intent=Intent.SUCCESS,
    ),
    AdminAction(
        key="delete",
        label_key="admin.action.delete",
        intent=Intent.DANGER,
        dangerous=True,
        confirm=True,
        confirm_title_key="admin.action.delete.confirm_title",
        confirm_body_key="admin.action.delete.confirm_body",
    ),
]

_BY_KEY: dict[str, AdminAction] = {a.key: a for a in ACTIONS}


def action(key: str) -> AdminAction | None:
    return _BY_KEY.get(key)


def action_button(act: AdminAction, subject: str, locale: Locale):
    return inline(act.label(locale), act.ask_callback(subject), intent=act.intent)


def confirmation_screen(act: AdminAction, subject_label: str, subject: str, locale: Locale) -> Screen:
    """Build a confirmation screen from the action declaration.

    The body always names *whose* workspace it is (``{subject}``), which is the
    hard requirement for admin actions on someone else's data.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t(act.confirm_title_key, locale))
    doc.p(catalog.t(act.confirm_body_key, locale, subject=subject_label))
    rows = [
        [
            inline(
                catalog.t("admin.confirm.proceed", locale),
                act.callback(subject),
                intent=Intent.DANGER if act.dangerous else Intent.PRIMARY,
            ),
            inline(catalog.t("common.cancel", locale), admin_cb.cancel(subject), intent=Intent.PRIMARY),
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(rows))
