"""Ports for the force-join gate: chat storage + the Telegram membership reads.

The gate is a middleware. It cannot import aiogram or an adapter, so it depends
on these two protocols: ``ForceJoinStore`` (SQLite) and ``TelegramDirectory``
(the Bot API reads ``getChat`` / ``getChatMember``).
"""

from __future__ import annotations

from typing import Protocol

from cloudbot.domain.forcejoin import ChatKind, ForceJoinChat, MembershipResult

class ForceJoinStore(Protocol):
    async def chats(self) -> list[ForceJoinChat]: ...
    async def enabled_chats(self) -> list[ForceJoinChat]: ...
    async def get(self, chat_id: int) -> ForceJoinChat | None: ...
    async def add(
        self,
        *,
        chat_id: int,
        username: str,
        title: str,
        kind: ChatKind,
        bot_is_admin: bool,
        added_by: int,
    ) -> ForceJoinChat: ...
    async def remove(self, chat_id: int) -> None: ...
    async def set_enabled(self, chat_id: int, enabled: bool) -> None: ...
    async def set_admin(self, chat_id: int, is_admin: bool) -> None: ...
    async def flag(self, key: str, default: bool) -> bool: ...
    async def set_flag(self, key: str, value: bool) -> None: ...

class TelegramDirectory(Protocol):
    """The two Bot API reads the gate and the admin add-flow need."""

    async def resolve_chat(self, reference: str) -> dict:
        """Resolve ``@name`` / numeric id / ``t.me`` link to a ``getChat`` dict.

        Raises :class:`ChatResolutionError` when the chat cannot be read (a wrong
        username, the bot is not a member, a network failure).
        """
        ...

    async def is_admin_in(self, chat_id: int, bot_id: int) -> bool:
        """Whether the bot itself is an administrator/creator of ``chat_id``."""
        ...

    async def membership(self, chat_id: int, user_id: int) -> MembershipResult:
        """One user's membership, with ``unknown`` for every "cannot know" case."""
        ...
