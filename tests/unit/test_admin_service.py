"""``AdminService`` read models and actions (task §3, D2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain.events import EventCategory, NewEvent
from cloudbot.domain.value_objects import WorkspaceId

NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def _seed(users, engine, tg_id=6508129575, name="Mohamed"):
    await users.get_or_create(tg_id, default_locale=Locale.EN)
    await users.set_display_name(tg_id, name)
    ws = await engine.list_workspaces()
    await users.add_workspace(tg_id, ws[0].id)
    return ws[0]


async def test_overview_counts_are_real(services, users, engine):
    await _seed(users, engine)
    ov = await services.admin.overview(now=NOW)
    assert ov.total_users == 1
    assert ov.workspaces_by_status.get("active") == 1
    assert ov.engine.reachable is True
    assert ov.engine.version == "0.0.20"
    assert ov.uptime_seconds >= 0


async def test_overview_active_24h_uses_last_seen(services, users, engine):
    await _seed(users, engine)
    # The user was just seen via get_or_create -> active within 24h.
    ov = await services.admin.overview(now=NOW)
    # last_seen is real "now", not the frozen NOW; just assert it is counted.
    assert ov.active_24h >= 1


async def test_overview_engine_unreachable_is_honest_not_zero(services, users, engine):
    await _seed(users, engine)

    async def boom(*a, **k):
        raise RuntimeError("connection refused")

    engine.list_workspaces = boom
    engine.health = boom
    engine.ready = boom
    engine.meta = boom
    ov = await services.admin.overview(now=NOW)
    assert ov.engine.reachable is False
    # Users are still counted (local DB), but the fleet is not claimed as zero.
    assert ov.total_users == 1
    assert ov.live_runs == 0


async def test_users_page_searches_by_id_and_name(services, users, engine):
    await _seed(users, engine, 111, "Sara")
    await _seed(users, engine, 222, "Omar")
    page = await services.admin.users_page()
    assert page.total == 2
    by_id = await services.admin.users_page(query="111")
    assert [u.tg_id for u in by_id.users] == [111]
    by_name = await services.admin.users_page(query="Omar")
    assert [u.tg_id for u in by_name.users] == [222]


async def test_users_page_empty_is_honest(services):
    page = await services.admin.users_page()
    assert page.users == [] and page.total == 0


async def test_user_page_reads_another_users_data(services, users, engine):
    ws = await _seed(users, engine, 111, "Sara")
    page = await services.admin.user_page(111)
    assert page is not None
    assert page.user.display_name == "Sara"
    assert len(page.workspaces) == 1
    assert page.workspaces[0].workspace.id == ws.id
    # usage was resolved from the engine.
    assert page.workspaces[0].usage is not None


async def test_user_page_missing_returns_none(services):
    assert await services.admin.user_page(999) is None


async def test_admin_actions_use_the_single_exemption(services, users, engine):
    ws = await _seed(users, engine, 111, "Sara")
    # The admin acts on a workspace it does not own; this must not raise.
    await services.admin.suspend_workspace(ws.id)
    got = await services.admin.get_workspace(ws.id)
    assert got.status.value == "suspended"
    await services.admin.resume_workspace(ws.id)
    assert (await services.admin.get_workspace(ws.id)).status.value == "active"


async def test_admin_delete_drops_the_registration(services, users, engine):
    ws = await _seed(users, engine, 111, "Sara")
    await services.admin.delete_workspace(ws.id)
    assert await users.workspace_owners(ws.id) == []
    with pytest.raises(Exception):
        await services.admin.get_workspace(ws.id)


async def test_owner_of(services, users, engine):
    ws = await _seed(users, engine, 111, "Sara")
    assert await services.admin.owner_of(ws.id) == 111


async def test_subscriptions_toggle_and_all_and_pause(services):
    await services.admin.set_subscription(1, EventCategory.RUN_STARTED.value, False)
    assert (await services.admin.subscriptions(1))[EventCategory.RUN_STARTED.value] is False
    assert await services.admin.all_subscribed(1) is False
    await services.admin.set_all_subscriptions(1, True)
    assert await services.admin.all_subscribed(1) is True
    await services.admin.set_paused(1, True)
    cursor, paused = await services.admin.notify_state(1)
    assert paused is True and cursor == 0
    await services.admin.set_paused(1, False)
    assert (await services.admin.notify_state(1))[1] is False


async def test_entered_mode_roundtrip(services, users, engine):
    ws = await _seed(users, engine, 111, "Sara")
    await services.admin.enter_workspace(1, str(ws.id), 111)
    entered = await services.admin.entered(1)
    assert entered is not None
    assert entered.ws_id == str(ws.id)
    assert entered.owner_tg_id == 111
    assert entered.owner_name == "Sara"
    assert entered.workspace is not None
    await services.admin.leave_workspace(1)
    assert await services.admin.entered(1) is None


async def test_audit_page_filters(services, event_log):
    await event_log.append(
        NewEvent(
            category=EventCategory.RUN_STARTED,
            summary_key="event.summary.run.started",
            tg_id=111,
            params={"name": "x"},
        )
    )
    await event_log.append(
        NewEvent(
            category=EventCategory.ADMIN_ACTION,
            summary_key="event.summary.admin.action",
            actor="admin:1",
            params={"action": "suspend"},
        )
    )
    page = await services.admin.audit_page()
    assert page.total == 2
    by_cat = await services.admin.audit_page(category=EventCategory.ADMIN_ACTION.value)
    assert by_cat.total == 1
    by_actor = await services.admin.audit_page(actor="admin:1")
    assert by_actor.total == 1
    by_user = await services.admin.audit_page(tg_id=111)
    assert by_user.total == 1
