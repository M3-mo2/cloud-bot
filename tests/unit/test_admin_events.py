"""The event source: one seam, and entered-mode attribution (task §3.4, §6 proof 7)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Message, Chat, User

from cloudbot.adapters.persistence.sqlite_events import SqliteEventLog
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.events import (
    EventCategory,
    NewEvent,
    actor_admin,
    actor_label_key,
)
from cloudbot.middleware.context import UserContext
from cloudbot.middleware.events_source import EventSourceMiddleware
from cloudbot.services.events import EventsService, RequestEvents


async def test_events_service_appends_intents_in_order(event_log):
    svc = EventsService(event_log)
    rec = RequestEvents()
    rec.workspace_created(tg_id=1, ws_id="ws_a", name="a")
    rec.run_started(tg_id=1, ws_id="ws_a", name="echo hi")
    written = await svc.flush(rec)
    assert written == [1, 2]
    events = await event_log.events_after(0)
    assert [e.category for e in events] == [
        EventCategory.WORKSPACE_CREATED,
        EventCategory.RUN_STARTED,
    ]
    assert events[0].actor == "user"


async def test_flush_clears_the_buffer(event_log):
    svc = EventsService(event_log)
    rec = RequestEvents()
    rec.user_new(tg_id=1, name="x")
    await svc.flush(rec)
    assert rec.intents == []


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=1, is_bot=False, first_name="A"),
        chat_instance="c",
        data=data,
    )


async def test_entered_mode_action_is_recorded_as_the_admin_not_the_user(event_log):
    """Proof 7: an admin acting inside another user's workspace produces an audit
    row whose actor is the ADMIN id, on the subject's workspace — never the user."""
    svc = EventsService(event_log)
    mw = EventSourceMiddleware(svc)
    # The admin (999) is inside a workspace owned by the user (111).
    ctx = UserContext(
        tg_id=111,
        locale=Locale.EN,
        is_admin=True,
        mode=RunMode.PRODUCTION,
        admin_tg_id=999,
        entered_ws_id="ws_01M6HJASCWM9Z1PF755GT89NK4",
        entered_owner_tg_id=111,
    )

    async def handler(ev, data):
        return None

    await mw(handler, _callback("file:ls:abc"), {"ctx": ctx})
    events = await event_log.events_after(0)
    assert len(events) == 1
    row = events[0]
    assert row.category is EventCategory.ADMIN_ACTION
    assert row.actor == actor_admin(999)
    assert row.actor != "user"
    assert row.ws_id == "ws_01M6HJASCWM9Z1PF755GT89NK4"
    assert row.tg_id == 111  # the subject is still recorded


async def test_not_entered_records_no_admin_action(event_log):
    svc = EventsService(event_log)
    mw = EventSourceMiddleware(svc)
    ctx = UserContext(tg_id=1, locale=Locale.EN, is_admin=True, mode=RunMode.PRODUCTION)

    async def handler(ev, data):
        return None

    await mw(handler, _callback("file:ls:abc"), {"ctx": ctx})
    assert await event_log.events_after(0) == []


async def test_handler_intents_are_flushed_after_the_handler(event_log):
    svc = EventsService(event_log)
    mw = EventSourceMiddleware(svc)
    ctx = UserContext(tg_id=1, locale=Locale.EN, is_admin=False, mode=RunMode.PRODUCTION)

    async def handler(ev, data):
        data["events"].workspace_created(tg_id=1, ws_id="ws_a", name="a")

    await mw(handler, _callback("x"), {"ctx": ctx})
    events = await event_log.events_after(0)
    assert [e.category for e in events] == [EventCategory.WORKSPACE_CREATED]


async def test_engine_unreachable_intent_is_an_error_severity(event_log):
    rec = RequestEvents()
    rec.engine_unreachable(reason="unavailable")
    assert rec.intents[0].category is EventCategory.ENGINE_UNREACHABLE
    assert rec.intents[0].effective_severity.value == "error"


def test_actor_labels():
    assert actor_label_key("user") == "event.actor.user"
    assert actor_label_key("system") == "event.actor.system"
    assert actor_label_key("admin:5") == "event.actor.admin"


async def test_every_category_has_a_catalog_label(services):
    from cloudbot.presentation.i18n.catalog import catalog

    for category in EventCategory:
        for locale in (Locale.EN, Locale.AR):
            assert catalog.has(category.label_key, locale), category


async def test_event_log_query_filters(event_log):
    await event_log.append(
        NewEvent(category=EventCategory.RUN_FAILED, summary_key="s", tg_id=1)
    )
    await event_log.append(
        NewEvent(
            category=EventCategory.ADMIN_ACTION,
            summary_key="s",
            actor="admin:9",
            ws_id="ws_z",
        )
    )
    _events, total = await event_log.query(category=EventCategory.RUN_FAILED)
    assert total == 1
    _events, total = await event_log.query(actor="admin:9")
    assert total == 1
    _events, total = await event_log.query(ws_id="ws_z")
    assert total == 1
