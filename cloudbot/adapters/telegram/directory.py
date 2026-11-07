"""Bot API directory reads for the force-join gate (the only Telegram admin reads).

The engine adapter is the only module that knows HTTP *to the engine*; this
module is the only one that calls ``getChat`` / ``getChatMember`` on Telegram.
It is deliberately a thin translation of API answers into domain objects, and it
never raises for a "cannot know" case: a network failure or a missing admin
right becomes a ``MembershipResult.unknown`` so the gate can allow the user.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError

from cloudbot.domain.forcejoin import (
    ChatResolutionError,
    MembershipResult,
    status_is_subscribed,
)

log = structlog.get_logger("cloudbot.telegram.directory")

_TME_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:joinchat/)?([A-Za-z0-9_+\-]+)/?$", re.I)
_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,})$")
_NUMERIC_RE = re.compile(r"^(-?\d+)$")

def normalize_reference(reference: str) -> str | int:
    """Turn an admin's input into what ``getChat`` accepts.

    Accepts ``@name``, a bare ``name``, a ``t.me/name`` / ``t.me/+hash`` link, or
    a numeric chat id. Raises :class:`ChatResolutionError` with reason
    ``"format"`` for anything else — the admin is told the exact problem.
    """
    text = (reference or "").strip()
    if not text:
        raise ChatResolutionError("format", "empty")
    match = _NUMERIC_RE.match(text)
    if match:
        return int(match.group(1))
    link = _TME_RE.match(text)
    if link:
        name = link.group(1)
        if name.startswith("+") or name.startswith("-"):
            # A private invite hash is not resolvable by getChat without joining.
            raise ChatResolutionError("invite_link", text)
        return f"@{name}"
    user = _USERNAME_RE.match(text)
    if user:
        return f"@{user.group(1)}"
    raise ChatResolutionError("format", text)

class TelegramDirectory:
    def __init__(self, bot: Any) -> None:
        self._bot = bot

    @property
    def bot_id(self) -> int:
        try:
            return int(getattr(self._bot, "id", 0) or 0)
        except Exception:  # noqa: BLE001 - an un-loaded bot id is simply unknown
            return 0

    async def resolve_chat(self, reference: str) -> dict:
        """Resolve a reference to a chat info mapping, or raise ``ChatResolutionError``."""
        chat_ref = normalize_reference(reference)
        try:
            chat = await self._bot.get_chat(chat_ref)
        except TelegramBadRequest as exc:
            message = str(getattr(exc, "message", "") or exc)
            log.warning("forcejoin.resolve_failed", reference=str(reference), error=message[:120])
            raise ChatResolutionError("not_found", message) from exc
        except TelegramAPIError as exc:
            log.warning("forcejoin.resolve_failed", reference=str(reference), error=type(exc).__name__)
            raise ChatResolutionError("api_unavailable", type(exc).__name__) from exc
        return {
            "chat_id": int(chat.id),
            "username": str(getattr(chat, "username", "") or ""),
            "title": str(getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""),
            "type": str(getattr(chat, "type", "") or ""),
        }

    async def is_admin_in(self, chat_id: int, bot_id: int) -> bool:
        """Whether the bot is an administrator/creator there.

        ``False`` for every failure (the caller must treat "cannot know" as "not
        an admin here" when *adding* — the task is explicit that a chat the bot
        cannot verify is refused rather than stored).
        """
        try:
            member = await self._bot.get_chat_member(chat_id=chat_id, user_id=bot_id)
        except TelegramAPIError as exc:
            log.warning(
                "forcejoin.bot_member_failed",
                chat_id=chat_id,
                error=type(exc).__name__,
            )
            return False
        return str(getattr(member, "status", "")) in ("administrator", "creator")

    async def membership(self, chat_id: int, user_id: int) -> MembershipResult:
        """One user's membership. Every "cannot know" result is ``unknown``."""
        try:
            member = await self._bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        except TelegramBadRequest as exc:
            message = str(getattr(exc, "message", "") or exc)
            # A deleted chat, or the bot lost its admin right: cannot verify.
            log.warning(
                "forcejoin.membership_unknown",
                chat_id=chat_id,
                user_id=user_id,
                error=message[:120],
            )
            return MembershipResult.unknown_for(chat_id, "bad_request")
        except TelegramNetworkError as exc:
            log.warning(
                "forcejoin.membership_network",
                chat_id=chat_id,
                user_id=user_id,
                error=type(exc).__name__,
            )
            return MembershipResult.unknown_for(chat_id, "network")
        except TelegramAPIError as exc:
            log.warning(
                "forcejoin.membership_unknown",
                chat_id=chat_id,
                user_id=user_id,
                error=type(exc).__name__,
            )
            return MembershipResult.unknown_for(chat_id, "api")
        status = str(getattr(member, "status", ""))
        is_member = bool(getattr(member, "is_member", False))
        if status_is_subscribed(status, is_member=is_member):
            return MembershipResult.allowed(chat_id)
        return MembershipResult.refused(chat_id)
