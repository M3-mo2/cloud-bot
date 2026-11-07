"""``ForceJoinService`` — the mandatory-subscription gate and its admin actions.

Two responsibilities, both in one place because they share the one truth about
which chats are required:

* **the gate** (``status``): what is missing for this user, computed with one
  ``getChatMember`` per enabled chat, plus a short-lived per-user cache;
* **the management** (add/remove/enable/disable/global switch) used by the admin
  section.

The anti-lockout rule is implemented here, not in the middleware: a chat whose
membership is ``unknown`` is never reported as ``missing``. The middleware then
merely renders the missing list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from cloudbot.domain.forcejoin import (
    GATE_FLAG,
    ChatResolutionError,
    ForceJoinChat,
    GateStatus,
    MembershipResult,
)
from cloudbot.domain.ports.forcejoin import ForceJoinStore, TelegramDirectory

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

class ForceJoinService:
    def __init__(
        self,
        store: ForceJoinStore,
        directory: TelegramDirectory,
        *,
        bot_id: int = 0,
        clock=time.monotonic,
    ) -> None:
        self._store = store
        self._directory = directory
        self._bot_id = bot_id
        self._clock = clock
        self._cache = _GateCache()

    def bind(self, directory: TelegramDirectory, bot_id: int) -> None:
        """Attach the real Bot-backed directory once the bot exists (bootstrap)."""
        self._directory = directory
        self._bot_id = bot_id

    # -- the gate ------------------------------------------------------------
    async def enabled(self) -> bool:
        return await self._store.flag(GATE_FLAG, True)

    async def set_enabled(self, enabled: bool) -> None:
        await self._store.set_flag(GATE_FLAG, enabled)

    async def chats(self) -> list[ForceJoinChat]:
        return await self._store.chats()

    async def status(self, user_id: int, *, force: bool = False) -> GateStatus:
        """The current gate verdict for ``user_id``.

        ``force=True`` (the «تحقّقت» button) bypasses the positive cache so a user
        who just joined is admitted immediately.
        """
        if not await self.enabled():
            return GateStatus(enabled=False)

        required = await self._store.enabled_chats()
        if not required:
            return GateStatus(enabled=True)

        now = self._clock()
        missing: list[ForceJoinChat] = []
        unverifiable: list[int] = []
        for chat in required:
            result = await self._check(chat, user_id, now=now, force=force)
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
        )

    async def _check(
        self, chat: ForceJoinChat, user_id: int, *, now: float, force: bool
    ) -> MembershipResult:
        if not force:
            cached = self._cache.get(now, chat.chat_id, user_id)
            if cached is not None:
                return cached
        result = await self._directory.membership(chat.chat_id, user_id)
        if result.unknown:
            # A chat we cannot read is a *configuration* problem, not a user
            # answer: report it (so the panel can show a red warning) and allow.
            log.warning(
                "forcejoin.unverifiable_chat",
                chat_id=chat.chat_id,
                reason=result.reason,
            )
            self._cache.drop_chat(chat.chat_id)
            return result
        self._cache.put_positive(now, chat.chat_id, user_id, result)
        return result

    # -- admin management ----------------------------------------------------
    async def add_chat(self, reference: str, *, added_by: int) -> ForceJoinChat:
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

    async def remove_chat(self, chat_id: int) -> None:
        await self._store.remove(chat_id)
        self._cache.drop_chat(chat_id)

    async def set_chat_enabled(self, chat_id: int, enabled: bool) -> None:
        await self._store.set_enabled(chat_id, enabled)
        self._cache.drop_chat(chat_id)

    def _bot_id_value(self) -> int:
        return self._bot_id or int(getattr(self._directory, "bot_id", 0) or 0)

    async def probe(self, user_id: int) -> list[tuple[ForceJoinChat, MembershipResult]]:
        """Every required chat with this user's *fresh* membership (``check me``)."""
        required = await self._store.enabled_chats()
        out: list[tuple[ForceJoinChat, MembershipResult]] = []
        for chat in required:
            out.append((chat, await self._directory.membership(chat.chat_id, user_id)))
        return out

    def clear_user_cache(self, user_id: int) -> None:
        self._cache.drop_user(user_id)
