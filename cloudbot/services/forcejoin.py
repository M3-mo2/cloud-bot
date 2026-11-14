"""``ForceJoinService`` — the mandatory-subscription gate and its admin actions.

Three responsibilities, all in one place because they share the one truth about
which chats are required:

* **the gate** (``status``): what is missing for this user, computed with one
  ``getChatMember`` per enabled chat, a short-lived per-user positive cache and a
  per-user re-check cooldown;
* **the record** (``forcejoin_events``): one row per decision, written here and
  never by a handler, so the middleware, the «تحقّقت» button and the admin test
  are all covered by construction — a cached positive included;
* **the management** (add/remove/enable/disable/global switch/self-check) used by
  the admin section.

The anti-lockout rule is implemented here, not in the middleware: a chat whose
membership is ``unknown`` is never reported as ``missing``. The middleware then
merely renders the missing list. A rate limit and a chat the bot no longer
administers both become ``unknown`` (with their own reason and a cooldown / a
stale-admin flag), never a refusal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from cloudbot.domain.forcejoin import (
    GATE_FLAG,
    ChatResolutionError,
    ChatVerdict,
    ForceJoinChat,
    GateEvent,
    GateStatus,
    MembershipResult,
)
from cloudbot.domain.ports.forcejoin import (
    ForceJoinEventSink,
    ForceJoinStore,
    TelegramDirectory,
)

log = structlog.get_logger("cloudbot.forcejoin")

#: How long a **positive** membership answer may be reused, in seconds.
#:
#: Why 180 s: a chatty user taps buttons every few seconds, and each check is one
#: ``getChatMember`` per required chat on the Bot API's rate budget. Three minutes
#: is short enough that leaving a channel is noticed quickly (the admin can also
#: force a re-read from the panel; the gate's own «تحقّقت» button bypasses the
#: cache), and long enough that a user navigating the bot does not trigger an API
#: call per tap.
#:
#: A **negative** answer is never cached past the next check: the user pressed
#: «تحقّقت», which means they believe they joined, so we must ask again. Refusals
#: are therefore not cached at all (see ``_check`` below).
CACHE_TTL_S = 180.0

#: A user may re-check one chat at most this often (TASK forcejoin-v2 Part 2.4).
#:
#: Why 3 s: it stops a user hammering «تحقّقت» into a 429 loop without hiding a
#: real join — joining and coming back takes longer than three seconds. The admin
#: test passes ``force=True`` and is not held back by it.
USER_COOLDOWN_S = 3.0

@dataclass
class _CacheEntry:
    at: float
    result: MembershipResult

@dataclass
class _GateCache:
    """Per-user, per-chat positive cache. Never persists a refusal."""

    ttl_s: float = CACHE_TTL_S
    entries: dict[tuple[int, int], _CacheEntry] = field(default_factory=dict)

    def get(self, now: float, chat_id: int, user_id: int) -> MembershipResult | None:
        entry = self.entries.get((chat_id, user_id))
        if entry is None:
            return None
        if now - entry.at >= self.ttl_s:
            self.entries.pop((chat_id, user_id), None)
            return None
        return entry.result

    def put_positive(self, now: float, chat_id: int, user_id: int, result: MembershipResult) -> None:
        # Only a known-positive answer is cached; a refusal must be re-asked.
        if result.subscribed and not result.unknown:
            self.entries[(chat_id, user_id)] = _CacheEntry(at=now, result=result)

    def drop_user(self, user_id: int) -> None:
        for key in [k for k in self.entries if k[1] == user_id]:
            self.entries.pop(key, None)

    def drop_chat(self, chat_id: int) -> None:
        for key in [k for k in self.entries if k[0] == chat_id]:
            self.entries.pop(key, None)

@dataclass
class _Cooldown:
    """Per-user, per-chat re-check cooldown: the previous answer, with its stamp."""

    at: float
    result: MembershipResult

class _Cooldowns:
    def __init__(self, window_s: float = USER_COOLDOWN_S) -> None:
        self.window_s = window_s
        self.entries: dict[tuple[int, int], _Cooldown] = {}

    def get(self, now: float, chat_id: int, user_id: int) -> MembershipResult | None:
        entry = self.entries.get((chat_id, user_id))
        if entry is None:
            return None
        if now - entry.at >= self.window_s:
            return None
        # The answer is reused, and marked as cached so the record says so.
        return entry.result.with_measurement(latency_ms=0, cached=True)

    def put(self, now: float, chat_id: int, user_id: int, result: MembershipResult) -> None:
        # A refusal is never reused: the user pressed «تحقّقت», so ask again (the
        # task's rule 3 in Part 5). The cooldown exists for repeated *positive* and
        # *unverifiable* answers, and a rate limit is paused per chat separately.
        if not result.subscribed and not result.unknown:
            self.entries.pop((chat_id, user_id), None)
            return
        self.entries[(chat_id, user_id)] = _Cooldown(at=now, result=result)

    def retry_after(self, now: float, chat_id: int, user_id: int) -> float:
        entry = self.entries.get((chat_id, user_id))
        if entry is None:
            return 0.0
        return max(0.0, self.window_s - (now - entry.at))

    def drop_user(self, user_id: int) -> None:
        for key in [k for k in self.entries if k[1] == user_id]:
            self.entries.pop(key, None)

    def drop_chat(self, chat_id: int) -> None:
        for key in [k for k in self.entries if k[0] == chat_id]:
            self.entries.pop(key, None)

class ForceJoinService:
    def __init__(
        self,
        store: ForceJoinStore,
        directory: TelegramDirectory,
        *,
        bot_id: int = 0,
        events: ForceJoinEventSink | None = None,
        stats=None,
        clock=time.monotonic,
        cooldown_s: float = USER_COOLDOWN_S,
    ) -> None:
        self._store = store
        self._directory = directory
        self._bot_id = bot_id
        self._events = events
        self._stats = stats
        self._clock = clock
        self._cache = _GateCache()
        self._cooldowns = _Cooldowns(cooldown_s)
        #: Per-chat rate-limit pauses: chat_id -> the monotonic time it ends.
        self._paused_until: dict[int, float] = {}
        #: Last «تحقّقت» press per user, for the button's disabled window. This is a
        #: *UI* cooldown (a refusal is never cached, so it cannot live in the cache).
        self._last_press: dict[int, float] = {}
        self._press_window_s = cooldown_s

    def bind(self, directory: TelegramDirectory, bot_id: int) -> None:
        """Attach the real Bot-backed directory once the bot exists (bootstrap)."""
        self._directory = directory
        self._bot_id = bot_id

    # -- the record ----------------------------------------------------------
    async def _record(self, event: GateEvent) -> None:
        """Write one decision row. A missing sink or a write error is logged only."""
        if self._events is None:
            return
        try:
            await self._events.record(event)
        except Exception as exc:  # noqa: BLE001 - never let the record break the gate
            log.warning("forcejoin.event_record_failed", error=type(exc).__name__)

    async def _config(self, reason: str, *, chat_id: int | None = None, tg_id: int | None = None) -> None:
        """Record one operator configuration change (``outcome='config'``)."""
        await self._record(
            GateEvent(
                outcome="config",
                reason=reason,
                chat_id=chat_id,
                tg_id=tg_id,
            )
        )

    # -- the gate ------------------------------------------------------------
    async def enabled(self) -> bool:
        return await self._store.flag(GATE_FLAG, True)

    async def set_enabled(self, enabled: bool, *, actor: int | None = None) -> None:
        await self._store.set_flag(GATE_FLAG, enabled)
        await self._config("gate_on" if enabled else "gate_off", tg_id=actor)

    async def chats(self) -> list[ForceJoinChat]:
        return await self._store.chats()

    async def stats(self, range_key: str | None = None):
        """The Part 3 read model, or ``None`` when no statistics reader is wired."""
        if self._stats is None:
            return None
        return await self._stats.read(range_key)

    async def status(
        self, user_id: int, *, force: bool = False, record: bool = True
    ) -> GateStatus:
        """The current gate verdict for ``user_id``.

        ``force=True`` (the «تحقّقت» button and the admin test) bypasses both the
        positive cache and the per-user cooldown, so a user who just joined is
        admitted immediately. ``record=False`` is used by the boot self-check,
        which is about the *bot's* admin rights, not a user's membership.
        """
        if not await self.enabled():
            return GateStatus(enabled=False)

        required = await self._store.enabled_chats()
        if not required:
            return GateStatus(enabled=True)

        now = self._clock()
        missing: list[ForceJoinChat] = []
        unverifiable: list[int] = []
        verdicts: list[ChatVerdict] = []
        for chat in required:
            result = await self._check(
                chat, user_id, now=now, force=force, record=record
            )
            verdicts.append(
                ChatVerdict(
                    chat=chat,
                    state=result.state,
                    reason=result.reason,
                    cached=result.cached,
                )
            )
            if result.unknown:
                # Cannot know ⇒ allow. A broken configuration never locks a user
                # out; the warnings are surfaced in the admin panel.
                unverifiable.append(chat.chat_id)
                continue
            if not result.subscribed:
                missing.append(chat)
        if unverifiable:
            log.warning(
                "forcejoin.unverifiable",
                user_id=user_id,
                count=len(unverifiable),
                chats=unverifiable,
            )
        return GateStatus(
            enabled=True,
            required=tuple(required),
            missing=tuple(missing),
            warnings=tuple(str(c) for c in unverifiable),
            verdicts=tuple(verdicts),
        )

    async def _check(
        self,
        chat: ForceJoinChat,
        user_id: int,
        *,
        now: float,
        force: bool,
        record: bool = True,
    ) -> MembershipResult:
        if not force:
            cached = self._cache.get(now, chat.chat_id, user_id)
            if cached is not None:
                if record:
                    await self._record_pass(chat.chat_id, user_id, cached, cached=True)
                return cached
            # The 3-second user cooldown: return the previous answer, marked cached.
            recent = self._cooldowns.get(now, chat.chat_id, user_id)
            if recent is not None:
                if record:
                    await self._record_decision(user_id, recent)
                return recent

        # A chat paused by a rate limit is not asked again until its wait is over.
        paused_until = self._paused_until.get(chat.chat_id)
        if paused_until is not None and now < paused_until:
            paused = MembershipResult.unknown_for(chat.chat_id, "rate_limited")
            if record:
                await self._record_decision(user_id, paused)
            return paused
        if paused_until is not None and now >= paused_until:
            self._paused_until.pop(chat.chat_id, None)

        result = await self._directory.membership(chat.chat_id, user_id)
        self._cooldowns.put(now, chat.chat_id, user_id, result)

        if result.unknown:
            await self._handle_unknown(chat, user_id, result)
            if result.reason == "rate_limited":
                wait = max(1, int(result.retry_after or USER_COOLDOWN_S))
                self._paused_until[chat.chat_id] = now + wait
            self._cache.drop_chat(chat.chat_id)
            if record:
                await self._record_decision(user_id, result)
            return result

        self._cache.put_positive(now, chat.chat_id, user_id, result)
        if record:
            await self._record_decision(user_id, result)
        return result

    async def _handle_unknown(
        self, chat: ForceJoinChat, user_id: int, result: MembershipResult
    ) -> None:
        """React to a "cannot know" answer by its reason (never a lockout)."""
        if result.reason == "rate_limited":
            log.warning(
                "forcejoin.rate_limited",
                chat_id=chat.chat_id,
                user_id=user_id,
                retry_after=result.retry_after,
            )
            return
        if result.reason == "not_admin":
            # The chat is gone or the bot lost its rights: mark the row stale but
            # never disable it silently — the operator decides on the panel.
            try:
                await self._store.set_admin(chat.chat_id, False)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "forcejoin.admin_clear_failed",
                    chat_id=chat.chat_id,
                    error=type(exc).__name__,
                )
            log.warning(
                "forcejoin.chat_needs_attention",
                chat_id=chat.chat_id,
                title=chat.label,
                reason=result.reason,
            )
            return
        # A network/API failure is not a fact about the chat; keep its flag.
        log.warning(
            "forcejoin.unverifiable_chat",
            chat_id=chat.chat_id,
            reason=result.reason,
        )

    async def _record_decision(self, user_id: int, result: MembershipResult) -> None:
        outcome = "pass" if result.subscribed and not result.unknown else "deny"
        if result.unknown:
            # An unverifiable chat is recorded by its own outcome so the health
            # numbers can tell a rate limit / a lost admin right from a refusal.
            outcome = "error" if result.reason == "rate_limited" else "unknown"
        await self._record(
            GateEvent(
                outcome=outcome,
                reason=result.reason,
                tg_id=user_id,
                chat_id=result.chat_id,
                latency_ms=result.latency_ms,
                cached=result.cached,
                detail=result.detail,
            )
        )

    async def _record_pass(
        self, chat_id: int, user_id: int, result: MembershipResult, *, cached: bool
    ) -> None:
        await self._record(
            GateEvent(
                outcome="pass",
                reason="cached_positive" if cached else result.reason,
                tg_id=user_id,
                chat_id=chat_id,
                latency_ms=0,
                cached=True,
            )
        )

    async def cooldown_remaining(self, user_id: int, chat_id: int) -> float:
        """Seconds left before this user may re-check ``chat_id`` (for a button)."""
        return self._cooldowns.retry_after(self._clock(), chat_id, user_id)

    def mark_press(self, user_id: int) -> None:
        """Remember a «تحقّقت» press so the button can be disabled for its window."""
        self._last_press[user_id] = self._clock()

    def press_remaining(self, user_id: int) -> float:
        """Seconds left in the button's disabled window after the last press."""
        at = self._last_press.get(user_id)
        if at is None:
            return 0.0
        left = self._press_window_s - (self._clock() - at)
        if left <= 0:
            self._last_press.pop(user_id, None)
            return 0.0
        return left

    # -- admin management ----------------------------------------------------
    async def add_chat(
        self, reference: str, *, added_by: int
    ) -> ForceJoinChat:
        """Resolve, verify the bot is an admin, and store — one message, no wizard.

        Raises :class:`ChatResolutionError` with an exact reason (``format``,
        ``not_found``, ``api_unavailable``, ``not_admin``) rather than storing a
        chat whose membership the gate could never verify.
        """
        info = await self._directory.resolve_chat(reference)
        chat_id = int(info["chat_id"])
        bot_id = self._bot_id_value()
        bot_is_admin = await self._directory.is_admin_in(chat_id, bot_id) if bot_id else False
        if not bot_is_admin:
            raise ChatResolutionError(
                "not_admin",
                f"the bot is not an administrator in {info.get('title') or chat_id}",
            )
        from cloudbot.domain.forcejoin import chat_kind_for_type

        stored = await self._store.add(
            chat_id=chat_id,
            username=str(info.get("username") or ""),
            title=str(info.get("title") or ""),
            kind=chat_kind_for_type(str(info.get("type") or "")),
            bot_is_admin=True,
            added_by=added_by,
        )
        self._cache.drop_chat(chat_id)
        self._cooldowns.drop_chat(chat_id)
        await self._config("add", chat_id=chat_id, tg_id=added_by)
        return stored

    async def recheck(self, chat_id: int) -> ForceJoinChat | None:
        """Re-measure the bot's admin status for a stored chat (the "check me" path)."""
        chat = await self._store.get(chat_id)
        if chat is None:
            return None
        bot_id = self._bot_id_value()
        bot_is_admin = await self._directory.is_admin_in(chat_id, bot_id) if bot_id else False
        await self._store.set_admin(chat_id, bot_is_admin)
        updated = await self._store.get(chat_id)
        return updated

    async def test_chat(self, chat_id: int, user_id: int) -> MembershipResult:
        """One real ``getChatMember`` for the operator, recorded as a config test."""
        result = await self._directory.membership(chat_id, user_id)
        await self._record(
            GateEvent(
                outcome="config",
                reason="test",
                chat_id=chat_id,
                tg_id=user_id,
                latency_ms=result.latency_ms,
                cached=False,
                detail=result.detail,
            )
        )
        return result

    async def remove_chat(self, chat_id: int, *, actor: int | None = None) -> None:
        await self._store.remove(chat_id)
        self._cache.drop_chat(chat_id)
        self._cooldowns.drop_chat(chat_id)
        self._paused_until.pop(chat_id, None)
        await self._config("remove", chat_id=chat_id, tg_id=actor)

    async def set_chat_enabled(
        self, chat_id: int, enabled: bool, *, actor: int | None = None
    ) -> None:
        await self._store.set_enabled(chat_id, enabled)
        self._cache.drop_chat(chat_id)
        self._cooldowns.drop_chat(chat_id)
        await self._config("enable" if enabled else "disable", chat_id=chat_id, tg_id=actor)

    def _bot_id_value(self) -> int:
        return self._bot_id or int(getattr(self._directory, "bot_id", 0) or 0)

    async def probe(self, user_id: int) -> list[tuple[ForceJoinChat, MembershipResult]]:
        """Every required chat with this user's *fresh* membership (``check me``).

        Every entry is a real ``getChatMember`` (via ``test_chat``), so the admin's
        end-to-end proof also leaves a ``config/test`` row per chat.
        """
        required = await self._store.enabled_chats()
        out: list[tuple[ForceJoinChat, MembershipResult]] = []
        for chat in required:
            out.append((chat, await self.test_chat(chat.chat_id, user_id)))
        return out

    def clear_user_cache(self, user_id: int) -> None:
        self._cache.drop_user(user_id)
        self._cooldowns.drop_user(user_id)

    # -- the boot self-check -------------------------------------------------
    async def self_check(self, *, actor: int | None = None) -> tuple[int, int, list[int]]:
        """Verify the bot is an admin in every enabled chat; write ``is_admin``.

        Returns ``(ok, total, needs_attention_ids)`` and logs one line:
        ``forcejoin: 2/3 chats ok, 1 needs attention (chat_id=…)``. Never raises —
        a broken check must not stop the bot from booting.
        """
        try:
            required = await self._store.enabled_chats()
        except Exception as exc:  # noqa: BLE001
            log.warning("forcejoin.self_check_failed", error=type(exc).__name__)
            return 0, 0, []
        bot_id = self._bot_id_value()
        ok = 0
        bad: list[int] = []
        for chat in required:
            try:
                bot_admin = (
                    bool(await self._directory.is_admin_in(chat.chat_id, bot_id))
                    if bot_id
                    else False
                )
            except Exception:  # noqa: BLE001 - an unknown chat is "needs attention"
                bot_admin = False
            try:
                await self._store.set_admin(chat.chat_id, bot_admin)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "forcejoin.self_check_write_failed",
                    chat_id=chat.chat_id,
                    error=type(exc).__name__,
                )
            if bot_admin:
                ok += 1
            else:
                bad.append(chat.chat_id)
        total = len(required)
        if bad:
            log.warning(
                "forcejoin.self_check",
                summary=f"forcejoin: {ok}/{total} chats ok, {len(bad)} needs attention",
                needs_attention=bad,
            )
        else:
            log.info(
                "forcejoin.self_check",
                summary=f"forcejoin: {ok}/{total} chats ok, 0 needs attention",
            )
        return ok, total, bad
