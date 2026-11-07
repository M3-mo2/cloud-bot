"""``BroadcastService`` — the audience, the run, and the safety rules.

Everything the task demands to be **code rather than prose** lives here:

* **frozen audience** — a preview computes the recipients once and stores them, so
  the number confirmed is the number attempted (``create_run``);
* **in-flight guard** — one run at a time, enforced by the store's conditional
  ``claim_run`` (atomic at the database), not by an in-memory flag;
* **per-run ceiling** — ``create_run`` refuses an audience over the ceiling unless
  the caller explicitly confirms the bigger number (``confirmed_count``);
* **persisted progress** — the store owns ``sent``/``failed``; a restart reads the
  truth (a run left in ``sending`` is reported ``interrupted``, never a frozen bar);
* **deliberate pace + ``RetryAfter``** — a small ``sleep`` between sends and one
  retry that sleeps *exactly* the value Telegram gave;
* **hard failure rule** — ``failure_threshold`` consecutive failures stop the run.

The service never imports aiogram: the caller passes ``copy`` (a coroutine
``(chat_id, from_chat_id, message_id) -> MessageId``) and ``classify`` (exception
-> failure bucket), which is what makes the whole loop testable with no bot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog

from cloudbot.core.errors import BroadcastInFlight, BroadcastOverCeiling
from cloudbot.domain.broadcast import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_MAX_RECIPIENTS,
    DEFAULT_SEND_DELAY_S,
    HISTORY_LIMIT,
    AUDIENCES,
    AudienceKey,
    AudienceOption,
    BroadcastResult,
    BroadcastRun,
    BroadcastSource,
    BroadcastView,
    FailureKind,
    SourceKind,
    groups_for,
    over_ceiling,
)
from cloudbot.domain.ports.broadcast import BroadcastStore

log = structlog.get_logger("cloudbot.broadcast")

#: ``(chat_id, from_chat_id, message_id) -> the copied message (id readable)``.
CopyFn = Callable[[int, int, int], Awaitable[object]]
#: ``(exception) -> failure bucket`` — the Telegram classifier.
ClassifyFn = Callable[[BaseException], FailureKind]
#: ``(is_retry_after, exception) -> bool`` / seconds — kept injectable for tests.
RetryAfterFn = Callable[[BaseException], int | None]
#: A progress hook: called after every persisted send/failure with the fresh run.
ProgressFn = Callable[[BroadcastRun], Awaitable[None]]

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class BroadcastService:
    def __init__(
        self,
        store: BroadcastStore,
        *,
        send_delay_s: float = DEFAULT_SEND_DELAY_S,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        max_recipients: int = DEFAULT_MAX_RECIPIENTS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._delay = max(0.0, float(send_delay_s))
        self._threshold = max(1, int(failure_threshold))
        self._ceiling = max(0, int(max_recipients))
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or _utcnow
        #: Run ids this process is driving. A ``sending`` row not in this set was
        #: left by a process that died — reported ``interrupted``, not "running".
        self._active: set[int] = set()

    @property
    def max_recipients(self) -> int:
        return self._ceiling

    @property
    def failure_threshold(self) -> int:
        return self._threshold

    # -- read model ----------------------------------------------------------
    async def options(self, *, now: datetime | None = None) -> tuple[AudienceOption, ...]:
        at = now or self._clock()
        options: list[AudienceOption] = []
        for key in AUDIENCES:
            members = await self._store.audience_members(key, now=at)
            options.append(AudienceOption(key=key, count=len(members)))
        return tuple(options)

    async def view(self, *, now: datetime | None = None) -> BroadcastView:
        at = now or self._clock()
        open_run = await self._store.open_run()
        if open_run is not None and open_run.id not in self._active:
            # This process is not driving it: a crash left the row. Record the
            # truth so the panel shows "interrupted" rather than a live bar.
            await self._store.mark_interrupted(open_run.id, now=at)
            open_run = await self._store.get_run(open_run.id)
        history = await self._store.history(limit=HISTORY_LIMIT)
        return BroadcastView(
            options=await self.options(now=at),
            history=tuple(history),
            open_run=open_run,
            max_recipients=self._ceiling,
            failure_threshold=self._threshold,
        )

    async def run(self, run_id: int) -> BroadcastRun | None:
        return await self._store.get_run(run_id)

    async def latest_draft(self, *, created_by: int) -> BroadcastRun | None:
        return await self._store.latest_draft(created_by=created_by)

    async def history(self, *, limit: int = HISTORY_LIMIT) -> list[BroadcastRun]:
        return await self._store.history(limit=limit)

    async def result(self, run_id: int) -> BroadcastResult | None:
        run = await self._store.get_run(run_id)
        if run is None:
            return None
        counts = await self._store.failure_counts(run_id)
        return BroadcastResult(run=run, groups=groups_for(counts))

    # -- compose / preview ---------------------------------------------------
    async def create_draft(
        self,
        *,
        source: BroadcastSource,
        created_by: int,
        now: datetime | None = None,
    ) -> BroadcastRun:
        """Persist the composed message reference before any audience is chosen."""
        return await self._store.create_draft(
            source=source, created_by=created_by, created_at=now or self._clock()
        )

    async def plan(
        self,
        run_id: int,
        *,
        audience: AudienceKey,
        confirmed_count: int | None = None,
        now: datetime | None = None,
    ) -> BroadcastRun:
        """Freeze the audience onto a draft, refusing an unconfirmed over-ceiling set.

        ``confirmed_count`` is the number the admin explicitly confirmed on the
        preview screen. It must be **at least** the live count for the confirm to
        mean anything; a stale confirm (the audience grew) is refused, so the
        stored plan is never bigger than what he agreed to.
        """
        at = now or self._clock()
        recipients = await self._store.audience_members(audience, now=at)
        count = len(recipients)
        if over_ceiling(count, self._ceiling):
            if confirmed_count is None or int(confirmed_count) < count:
                raise BroadcastOverCeiling(count=count, ceiling=self._ceiling)
        run = await self._store.plan_audience(
            run_id, audience=audience, recipients=recipients, now=at
        )
        if run is None:
            raise ValueError("run_missing")
        return run

    @staticmethod
    def copy_reference(source: BroadcastSource) -> tuple[int, int]:
        """The exact ``(from_chat_id, message_id)`` copy_message replays.

        The preview shows *this* pair and the sender passes the same pair, so the
        preview is the copy path — the panel cannot show a re-render and then send
        something else.
        """
        return (source.chat_id, source.message_id)

    # -- the run -------------------------------------------------------------
    async def send(
        self,
        run_id: int,
        *,
        copy: CopyFn,
        classify: ClassifyFn,
        retry_after: RetryAfterFn,
        progress: ProgressFn | None = None,
        retry_failed: bool = False,
    ) -> BroadcastResult:
        """Drive one run to completion, honouring every safety rule.

        Raises :class:`BroadcastInFlight` when another run holds the guard. Never
        raises for one unreachable user: a failed copy is recorded and the loop
        continues, until ``failure_threshold`` failures in a row stop it.
        """
        at = self._clock()
        claimed = await self._store.claim_run(run_id, now=at)
        if not claimed:
            raise BroadcastInFlight()
        self._active.add(run_id)
        if retry_failed:
            await self._store.reset_failed(run_id)
        stop_reason = ""
        consecutive = 0
        try:
            run = await self._store.get_run(run_id)
            assert run is not None
            source = run.source
            pending = await self._store.pending_targets(run_id)
            for index, tg_id in enumerate(pending):
                if index:
                    await self._sleep(self._delay)
                outcome = await self._deliver_one(
                    tg_id, source, copy=copy, classify=classify, retry_after=retry_after
                )
                now = self._clock()
                if outcome.delivered:
                    await self._store.record_success(run_id, tg_id, now=now)
                    consecutive = 0
                else:
                    await self._store.record_failure(
                        run_id, tg_id, outcome.error or "other", now=now
                    )
                    consecutive += 1
                if progress is not None:
                    fresh = await self._store.get_run(run_id)
                    if fresh is not None:
                        await progress(fresh)
                if consecutive >= self._threshold:
                    stop_reason = "consecutive_failures"
                    log.warning(
                        "broadcast.stopped", run_id=run_id, consecutive=consecutive
                    )
                    break
            status = "stopped" if stop_reason else "done"
            await self._store.finish_run(
                run_id, status, stop_reason=stop_reason, now=self._clock()
            )
        finally:
            self._active.discard(run_id)
        result = await self.result(run_id)
        assert result is not None
        return result

    async def _deliver_one(
        self,
        tg_id: int,
        source: BroadcastSource,
        *,
        copy: CopyFn,
        classify: ClassifyFn,
        retry_after: RetryAfterFn,
    ):
        from cloudbot.domain.broadcast import DeliveryOutcome

        # A rate-limit refusal may happen on any attempt. Honour ``retry_after``
        # exactly, then try the same user once per refusal (bounded) — a broadcast
        # that keeps retrying forever would hold the guard open.
        for _ in range(5):
            try:
                message = await copy(tg_id, source.chat_id, source.message_id)
            except Exception as exc:  # noqa: BLE001 - classified, never raised
                wait = retry_after(exc)
                if wait is not None:
                    await self._sleep(float(wait))
                    continue
                return DeliveryOutcome(tg_id=tg_id, delivered=False, error=classify(exc))
            message_id = getattr(message, "message_id", None)
            return DeliveryOutcome(tg_id=tg_id, delivered=True, message_id=message_id)
        return DeliveryOutcome(tg_id=tg_id, delivered=False, error="other")

    # -- retry ---------------------------------------------------------------
    async def pending_failures(self, run_id: int) -> list[int]:
        return await self._store.failed_targets(run_id)

    async def can_retry(self, run_id: int) -> bool:
        run = await self._store.get_run(run_id)
        return bool(run is not None and run.failed > 0 and not run.in_flight)

def validate_source_kind(*, has_text: bool, photo: bool, document: bool, video: bool, album: bool) -> SourceKind:
    """Classify the admin's single composed message, refusing an album.

    Raises :class:`ValueError` with a stable reason (``album`` / ``unsupported``)
    the presentation layer maps — never an English sentence.
    """
    if album:
        raise ValueError("album")
    if photo:
        return "photo"
    if video:
        return "video"
    if document:
        return "document"
    if has_text:
        return "text"
    raise ValueError("unsupported")

__all__ = ["BroadcastService", "CopyFn", "ClassifyFn", "RetryAfterFn", "ProgressFn", "validate_source_kind"]
