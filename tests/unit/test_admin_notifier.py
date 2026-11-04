"""The notifier: coalescing, the cap, and the cursor rules (task §3.5, §6 proofs 5–6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from cloudbot.bootstrap.notify import NotificationDispatcher
from cloudbot.core.locale import Locale
from cloudbot.domain.events import (
    ACTOR_SYSTEM,
    EventCategory,
    NewEvent,
    NotificationMessage,
    Severity,
)
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.services.notifier import NotifierService

NOW = datetime(2026, 11, 3, 20, 41, 39, tzinfo=timezone.utc)


class FakeLog:
    """An in-memory stand-in for the events table (id-assigned, ts-controllable)."""

    def __init__(self) -> None:
        self._rows: list[Any] = []

    def _make(self, i: int, category: EventCategory, ts: datetime) -> Any:
        return type(
            "Row",
            (),
            {
                "id": i,
                "ts": ts,
                "category": category,
                "severity": category.default_severity,
                "summary_key": "event.summary.admin.action",
                "params": {"action": "x"},
                "actor": ACTOR_SYSTEM,
                "tg_id": None,
                "ws_id": None,
            },
        )()

    def add(self, category: EventCategory, ts: datetime) -> int:
        i = len(self._rows) + 1
        self._rows.append(self._make(i, category, ts))
        return i

    async def append(self, event: NewEvent) -> int:
        return self.add(event.category, NOW)

    async def latest_id(self) -> int:
        return len(self._rows)

    async def count_after(self, after_id: int) -> int:
        return sum(1 for r in self._rows if r.id > after_id)

    async def events_after(self, after_id: int, *, limit: int = 1000, tg_id=None):
        rows = [r for r in self._rows if r.id > after_id]
        if tg_id is not None:
            rows = [r for r in rows if r.tg_id == tg_id]
        return rows[:limit]

    async def query(self, **kwargs):
        return [], 0


class FakeState:
    def __init__(self) -> None:
        self.cursor = 0
        self.paused = False
        self.subs: dict[str, bool] = {}

    async def subscriptions(self, admin_id: int):
        return dict(self.subs)

    async def set_subscription(self, admin_id, category, enabled):
        self.subs[category] = enabled

    async def notify_state(self, admin_id: int):
        return self.cursor, self.paused

    async def set_cursor(self, admin_id, event_id):
        self.cursor = event_id

    async def set_paused(self, admin_id, paused):
        self.paused = paused

    async def notify_scope(self, admin_id):
        return None

    async def set_notify_scope(self, admin_id, tg_id):
        self.scope = tg_id

    async def subscribed_admin_ids(self):
        return [1]

    async def entered(self, admin_id):
        return None

    async def set_entered(self, admin_id, ws_id, owner):
        pass

    async def clear_entered(self, admin_id):
        pass


@pytest.fixture
def log():
    return FakeLog()


@pytest.fixture
def state():
    return FakeState()


def _notifier(log, state, **kw):
    return NotifierService(log, state, **kw)


async def test_coalesce_same_category_within_window(log, state):
    base = NOW
    for k in range(5):
        log.add(EventCategory.WORKSPACE_CREATED, base + timedelta(seconds=k * 5))
    plan = await _notifier(log, state).plan(1)
    assert len(plan.messages) == 1
    assert plan.messages[0].count == 5
    assert plan.messages[0].first_event_id == 1
    assert plan.messages[0].last_event_id == 5


async def test_coalesce_splits_outside_window(log, state):
    log.add(EventCategory.RUN_STARTED, NOW)
    log.add(EventCategory.RUN_STARTED, NOW + timedelta(seconds=120))
    plan = await _notifier(log, state, coalesce_window_s=60).plan(1)
    assert [m.count for m in plan.messages] == [1, 1]


async def test_unsubscribed_category_is_not_delivered_but_cursor_advances(log, state):
    state.subs[EventCategory.RUN_STARTED.value] = False
    log.add(EventCategory.RUN_STARTED, NOW)
    plan = await _notifier(log, state).plan(1)
    assert plan.messages == []
    assert plan.new_cursor == 1  # advanced past the skipped event


async def test_backwards_clock_is_reported_and_reset(log, state):
    log.add(EventCategory.RUN_STARTED, NOW)
    state.cursor = 999  # ahead of the log
    plan = await _notifier(log, state).plan(1)
    assert plan.backwards_clock is True
    assert plan.new_cursor == 1
    assert plan.messages == []


async def test_paused_reports_a_gap_without_advancing(log, state):
    for _ in range(3):
        log.add(EventCategory.RUN_STARTED, NOW)
    state.paused = True
    plan = await _notifier(log, state).plan(1)
    assert plan.paused is True
    assert plan.messages == []
    assert plan.gap_events == 3
    assert plan.new_cursor == 0  # not advanced


async def test_large_backlog_becomes_one_gap_not_replay(log, state):
    for _ in range(250):
        log.add(EventCategory.WORKSPACE_CREATED, NOW)
    plan = await _notifier(log, state, gap_threshold=200).plan(1)
    assert plan.gap_events == 250
    assert plan.messages == []
    assert plan.new_cursor == 250


async def test_cap_is_honest_and_carries_the_real_suppressed_count(log, state):
    """Push more coalesced groups than the cap; assert ONE plan that reports the
    exact number of suppressed events (not silence, not N messages)."""
    # 15 distinct coalesced groups (each a different category / separated window).
    cats = [
        EventCategory.USER_NEW,
        EventCategory.WORKSPACE_CREATED,
        EventCategory.WORKSPACE_DELETED,
        EventCategory.WORKSPACE_SUSPENDED,
        EventCategory.RUN_STARTED,
        EventCategory.RUN_FAILED,
        EventCategory.PACKAGE_INSTALL_FAILED,
        EventCategory.TUNNEL_CREATED,
        EventCategory.ENGINE_UNREACHABLE,
        EventCategory.ADMIN_ACTION,
    ]
    for k, cat in enumerate(cats):
        log.add(cat, NOW + timedelta(seconds=k * 120))
    # 10 groups, cap 4 -> 6 groups suppressed. Give the last two groups counts of 2
    # and 3 so the suppressed EVENT count is not just the group count.
    log.add(cats[8], NOW + timedelta(seconds=2000))
    log.add(cats[9], NOW + timedelta(seconds=2100))
    log.add(cats[9], NOW + timedelta(seconds=2110))
    plan = await _notifier(log, state, max_messages_per_poll=4, coalesce_window_s=60).plan(1)
    assert len(plan.messages) == 4
    # suppressed groups: groups 5..10; events: 1+1+1+1 +2 +2 = ... compute from plan
    assert plan.suppressed_events > 0
    assert plan.suppressed_events == sum(
        m.count for m in (await _notifier(log, state, max_messages_per_poll=100).plan(1)).messages[4:]
    )


async def test_no_loss_or_duplication_across_a_restart(log, state):
    """Emit N, deliver, 'restart' (re-read the cursor), emit more, deliver — and
    assert every event id was delivered exactly once."""
    notifier = _notifier(log, state, coalesce_window_s=0)
    delivered: list[int] = []

    async def deliver(plan):
        for m in plan.messages:
            delivered.extend(m.event_ids)
        await notifier.commit(plan)

    # Batch 1.
    for _ in range(5):
        log.add(EventCategory.RUN_STARTED, NOW)
    await deliver(await notifier.plan(1))

    # 'Restart': a fresh NotifierService reads the persisted cursor.
    notifier2 = _notifier(log, state, coalesce_window_s=0)
    for _ in range(4):
        log.add(EventCategory.WORKSPACE_CREATED, NOW + timedelta(seconds=120))
    await deliver(await notifier2.plan(1))

    assert delivered == list(range(1, 10))
    assert len(delivered) == len(set(delivered)) == 9


async def test_dispatcher_sends_one_cap_message_with_the_real_count(log, state):
    """Proof 6 through the real dispatcher: one suppression message, exact count."""
    for k in range(30):
        log.add(EventCategory.WORKSPACE_CREATED, NOW + timedelta(seconds=k * 120))
    notifier = _notifier(log, state, max_messages_per_poll=3, coalesce_window_s=0)

    sent: list[tuple[int, RichDoc]] = []

    async def send(admin_id: int, doc: RichDoc):
        sent.append((admin_id, doc))

    dispatcher = NotificationDispatcher(
        notifier, admin_ids=lambda: [1], send=send, locale_for=lambda a: _locale()
    )
    await dispatcher.flush()

    # 3 delivered messages + exactly 1 suppression message.
    assert len(sent) == 4
    suppression_html = sent[-1][1].html
    # The suppressed count is 27 (30 - 3); assert the number is in the message.
    assert "٢٧" in suppression_html or "27" in suppression_html
    assert state.cursor == 30


async def test_dispatcher_does_not_advance_cursor_when_send_fails(log, state):
    log.add(EventCategory.RUN_STARTED, NOW)
    notifier = _notifier(log, state, coalesce_window_s=0)

    async def send(admin_id, doc):
        raise RuntimeError("telegram down")

    dispatcher = NotificationDispatcher(
        notifier, admin_ids=lambda: [1], send=send, locale_for=lambda a: _locale()
    )
    await dispatcher.flush()
    assert state.cursor == 0  # nothing lost; retried next flush


async def _locale():
    return Locale.EN
