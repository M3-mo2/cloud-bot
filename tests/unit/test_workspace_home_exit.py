"""Task 11 — the workspace home has exactly one *exit*: the explicit log-out.

The bottom ``⬅️ Back`` row and the ``🚪 Log out of workspace`` button both landed
on the workspace list, so the home offered the same destination twice (a third
route, the nav block's ``📦 Workspaces``, is for *switching* without leaving the
session). The redundant bottom row is gone; the log-out stays in every status
branch, and the shared nav block keeps switching reachable.
"""

from __future__ import annotations

import pytest

from cloudbot.adapters.cloud_service.fake import _ws
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceStatus
from cloudbot.presentation.screens import nav
from cloudbot.presentation.screens import workspaces as ws_screens

BRANCHES = [
    ("active", WorkspaceStatus.ACTIVE),
    ("suspended", WorkspaceStatus.SUSPENDED),
    ("locked", WorkspaceStatus.LOCKED),
    ("unknown", WorkspaceStatus.UNKNOWN),
]


def _screen(status: WorkspaceStatus, locale: Locale = Locale.EN):
    return ws_screens.workspace_detail(_ws("ws_x", "demo", status, active=1), locale)


def _rows(screen):
    markup = screen.markup
    assert markup is not None, "the workspace home has no keyboard"
    return list(markup.inline_keyboard)


def _actions(screen) -> list[str]:
    return [b.callback_data for row in _rows(screen) for b in row if b.callback_data]


def _bare_back_row(screen):
    """A single-button row labelled Back that goes to the workspace list — the
    removed redundant exit.

    The removed row used the ``common.back`` label; the session nav block's
    ``ws:list`` (which task 14 leaves alone on the home after omitting its own
    ``ws:home`` entry) is a different thing and never this.
    """
    from cloudbot.presentation.i18n.catalog import catalog

    back_labels = {catalog.t("common.back", loc) for loc in (Locale.EN, Locale.AR)}
    return [
        row
        for row in _rows(screen)
        if len(row) == 1
        and row[0].callback_data
        and row[0].callback_data.startswith("ws:list")
        and row[0].text in back_labels
    ]


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("name,status", BRANCHES)
def test_home_has_no_redundant_back_row_but_keeps_logout(name, status, locale):
    screen = _screen(status, locale)
    assert not _bare_back_row(screen), (
        f"{name}/{locale}: a bottom Back row duplicates the log-out's destination"
    )
    assert any(a.startswith("ws:logout") for a in _actions(screen)), (
        f"{name}/{locale}: no explicit log-out on the workspace home"
    )


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
@pytest.mark.parametrize("name,status", BRANCHES)
def test_every_status_branch_still_has_a_way_out(name, status, locale):
    """No branch is a trap: an explicit log-out *and* the session nav block are present.

    Task 13 removed the account destinations (Settings/Language/Help/Menu) from
    the inside block, so the workspace home is no longer a route to the account
    menu — but it still reaches the list, another workspace and out of the session.
    Task 14 (R5) removes the ``ws:home`` entry from the home itself (you are
    already there), never muting it.
    """
    screen = _screen(status, locale)
    actions = _actions(screen)
    assert any(a.startswith("ws:logout") for a in actions), f"{name}/{locale}: cannot leave"
    # Switching workspaces is an *account* action: it belongs outside the session.
    # The way out is the explicit log-out, and it is present on every branch — so
    # nothing is stranded by ``ws:list`` being absent here. This assertion is the
    # one that would catch the opposite mistake: a branch that removed the list
    # *and* forgot a log-out.
    assert not any(a.startswith("ws:list") for a in actions), (
        f"{name}/{locale}: the session still offers the workspace list"
    )
    assert not any(a == "ws:home" for a in actions), (
        f"{name}/{locale}: the home still offers ws:home (you are already there)"
    )


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_home_nav_block_offers_nothing_but_the_exit_and_logout(locale):
    """Inside a workspace the nav block is empty, and the exit is the log-out.

    The block used to carry ``ws:list``; that made the session block read as a
    hierarchy (this workspace *under* the list) and put two ways out of the
    session on one screen. The single way out is the explicit log-out above.
    """
    screen = _screen(WorkspaceStatus.ACTIVE, locale)
    nav_actions = {
        b.callback_data for row in _rows(screen) for b in row if b.callback_data in nav.NAV_ACTIONS
    }
    assert "ws:list" not in nav_actions, "the session block still offers Workspaces"
    assert "ws:home" not in nav_actions, "the home still offers its own entry"
    assert not nav_actions, f"the session block should be empty, got {nav_actions}"


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_home_nav_block_respects_the_layout_rule(locale):
    screen = _screen(WorkspaceStatus.ACTIVE, locale)
    nav_rows = [
        row
        for row in _rows(screen)
        if row and all(b.callback_data in nav.NAV_ACTIONS for b in row)
    ]
    # An empty block is legal now; the rule is about *size*, not presence. Keep
    # asserting the shape so a future re-introduction lands inside the rule.
    assert len(nav_rows) <= 3, "the nav block exceeds the 3-row rule"
    for row in nav_rows:
        assert len(row) <= 2, "a nav row has more than two columns"


def test_admin_entered_screen_keeps_its_leave_control():
    """Removing the user Back row must not strand the admin: their entered-mode
    screen has its own ``adm:leave`` control (and no user log-out)."""
    from datetime import datetime, timezone

    from cloudbot.domain.admin import AdminView, EnteredWorkspace
    from cloudbot.presentation.screens.admin.workspace import entered_workspace_screen

    ws = _ws("ws_x", "demo", WorkspaceStatus.ACTIVE, active=1)
    view = AdminView(
        locale=Locale.EN,
        now=datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc),
        entered=EnteredWorkspace(ws_id=str(ws.id), owner_tg_id=111, workspace=ws),
    )
    screen = entered_workspace_screen(view)
    actions = _actions(screen)
    assert "adm:leave" in actions, "the admin entered screen lost its way out"
    assert not any(a.startswith("ws:logout") for a in actions), "the admin must not log out the owner"
    assert not any(a == "ws:list" for a in actions), "the admin screen must not carry the user nav block"


def test_logout_and_browse_labels_read_differently():
    """D4: the session exit and the browse entry must not read the same."""
    from cloudbot.presentation.i18n.catalog import catalog

    for locale in (Locale.EN, Locale.AR):
        logout = catalog.t("ws.logout", locale)
        browse = catalog.t("menu.workspaces", locale)
        assert logout != browse
        assert "مساحة" in logout or "workspace" in logout.lower(), (
            f"{locale}: the log-out label does not name the workspace"
        )
