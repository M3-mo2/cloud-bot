"""``NotifierService`` — decides *what* to send each admin, never sends itself.

The arithmetic is pure and testable: given an event log and an admin's stored
state, produce a ``NotificationPlan`` (coalesced messages, a new cursor, and the
honest counts for anything suppressed or missed). ``NotificationDispatcher`` (or
the test that stands in for it) sends the plan and then calls ``commit`` so the
cursor advances only after delivery — nothing is lost or duplicated across a
restart.

Rules, all load-bearing:

* **Coalesce per (admin, category)** within ``coalesce_window_s``. A user tapping
  through screens produces many events; the admin sees "N workspace events", not
  N messages.
* **Cap deliveries per minute.** When the cap is hit, ONE message carries the real
  count of suppressed *events*; nothing is queued unboundedly and nothing is
  dropped silently.
* **Gap.** A backlog larger than ``gap_threshold`` (or a paused stretch resumed) is
  summarised in ONE message instead of replayed event-by-event.
* **Backwards clock.** If the persisted cursor is ahead of the log, reset it to
  the log's tip and say so.
* **Paused.** A paused admin gets no messages and the cursor is left alone, so
  resuming reports the backlog as a gap rather than a flood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cloudbot.domain.events import (
    EventCategory,
    EventRecord,
    NotificationMessage,
    NotificationPlan,
    Severity,
)
from cloudbot.domain.ports.events import AdminStateStore, EventLog

#: Events of one category within this many seconds become one message.
COALESCE_WINDOW_S = 60
#: At most this many messages per admin per poll; the rest are reported as one.
MAX_MESSAGES_PER_POLL = 10
#: A backlog larger than this is summarised, never replayed event-by-event.
GAP_THRESHOLD = 200


@dataclass
class _Group:
    category: EventCategory
    severity: Severity
    summary_key: str
    params: dict[str, object] = field(default_factory=dict)
    count: int = 0
    first_event_id: int = 0
    last_event_id: int = 0
    event_ids: list[int] = field(default_factory=list)
    first_ts: datetime | None = None


class NotifierService:
    def __init__(
        self,
        log: EventLog,
        state: AdminStateStore,
        *,
        coalesce_window_s: int = COALESCE_WINDOW_S,
        max_messages_per_poll: int = MAX_MESSAGES_PER_POLL,
        gap_threshold: int = GAP_THRESHOLD,
    ) -> None:
        self._log = log
        self._state = state
        self._window = timedelta(seconds=coalesce_window_s)
        self._cap = max_messages_per_poll
        self._gap = gap_threshold

    async def plan(self, admin_id: int) -> NotificationPlan:
        cursor, paused = await self._state.notify_state(admin_id)
        latest = await self._log.latest_id()

        # Backwards clock: the cursor points past the tip of the log.
        if cursor > latest:
            return NotificationPlan(
                admin_id=admin_id, new_cursor=latest, backwards_clock=True
            )

        # Paused: do not deliver, do not advance — resume reports a gap.
        if paused:
            return NotificationPlan(
                admin_id=admin_id,
                new_cursor=cursor,
                paused=True,
                gap_events=max(0, latest - cursor),
            )

        if latest <= cursor:
            return NotificationPlan(admin_id=admin_id, new_cursor=cursor)

        backlog = latest - cursor
        if backlog > self._gap:
            return NotificationPlan(
                admin_id=admin_id, new_cursor=latest, gap_events=backlog
            )

        events = await self._log.events_after(cursor, limit=self._gap + 1, tg_id=await self._state.notify_scope(admin_id))
        if len(events) > self._gap:
            return NotificationPlan(
                admin_id=admin_id,
                new_cursor=latest,
                gap_events=len(events),
            )

        subscribed = await self._state.subscriptions(admin_id)
        groups = self._coalesce(events, subscribed)

        suppressed_events = 0
        if len(groups) > self._cap:
            suppressed_events = sum(g.count for g in groups[self._cap :])
            groups = groups[: self._cap]

        messages = [
            NotificationMessage(
                category=g.category,
                severity=g.severity,
                count=g.count,
                first_event_id=g.first_event_id,
                last_event_id=g.last_event_id,
                summary_key=g.summary_key,
                params=g.params,
                event_ids=tuple(g.event_ids),
            )
            for g in groups
        ]
        return NotificationPlan(
            admin_id=admin_id,
            messages=messages,
            new_cursor=latest,
            suppressed_events=suppressed_events,
        )

    def _coalesce(
        self, events: list[EventRecord], subscribed: dict[str, bool]
    ) -> list[_Group]:
        groups: list[_Group] = []
        for event in events:
            if not self._is_subscribed(subscribed, event.category):
                continue
            last = groups[-1] if groups else None
            if (
                last is not None
                and last.category is event.category
                and last.first_ts is not None
                and event.ts - last.first_ts <= self._window
            ):
                last.count += 1
                last.last_event_id = event.id
                last.event_ids.append(event.id)
                continue
            groups.append(
                _Group(
                    category=event.category,
                    severity=event.severity,
                    summary_key=event.summary_key,
                    params=event.params,
                    count=1,
                    first_event_id=event.id,
                    last_event_id=event.id,
                    event_ids=[event.id],
                    first_ts=event.ts,
                )
            )
        return groups

    @staticmethod
    def _is_subscribed(subscribed: dict[str, bool], category: EventCategory) -> bool:
        # Default is ON (opt-out): the operator asked for "anything that happens".
        return subscribed.get(category.value, True)

    async def commit(self, plan: NotificationPlan) -> None:
        """Advance the cursor after a successful delivery."""
        await self._state.set_cursor(plan.admin_id, plan.new_cursor)
