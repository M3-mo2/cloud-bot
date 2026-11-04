"""Admin section: the entered-workspace view.

Reuses the **existing** workspace-detail screen (and, through its buttons, every
feature screen the user sees). The persistent banner and the leave control are
added by the rendering seam while in entered mode; this section supplies the
workspace itself and the leave button.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def entered_workspace_screen(view: AdminView) -> Screen:
    locale = view.locale
    entered = view.entered
    if entered is None or entered.workspace is None:
        doc = RichDoc(locale)
        doc.h(2, catalog.t("admin.workspace.enter_title", locale))
        doc.p(catalog.t("admin.workspace.not_entered", locale))
        return Screen(
            doc=doc,
            markup=inline_markup(
                [[inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
            ),
        )

    ws = entered.workspace
    # Reuse the user's workspace-detail screen (feature navigation included). The
    # user session UI is suppressed here: the admin's entered mode has its own
    # banner and leave control (the rendering seam), and a "log out" button would
    # log out the *owner*, not the admin.
    screen = ws_screens.workspace_detail(ws, locale, now=view.now, session=False)
    rows = [list(r) for r in screen.markup.inline_keyboard]
    rows.append(
        [inline(catalog.t("admin.entered.leave", locale), admin_cb.leave(), intent=Intent.PRIMARY)]
    )
    return Screen(doc=screen.doc, markup=inline_markup(rows))


register(
    AdminSection(
        key="workspace",
        title_key="admin.section.workspace",
        order=25,
        icon="🗂",
        build=entered_workspace_screen,
        in_menu=False,
    )
)
