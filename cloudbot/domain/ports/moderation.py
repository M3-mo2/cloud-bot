"""Persistence port for user and workspace bans."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from cloudbot.domain.moderation import UserBan, WorkspaceBan

class ModerationStore(Protocol):
    async def ban_user(
        self,
        tg_id: int,
        *,
        reason: str,
        banned_by: int,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> UserBan: ...
    async def unban_user(self, tg_id: int) -> bool: ...
    async def user_ban(self, tg_id: int) -> UserBan | None: ...
    async def banned_users(self) -> list[UserBan]: ...

    async def ban_workspace(
        self,
        ws_id: str,
        *,
        reason: str,
        banned_by: int,
        now: datetime,
    ) -> WorkspaceBan: ...
    async def unban_workspace(self, ws_id: str) -> bool: ...
    async def workspace_ban(self, ws_id: str) -> WorkspaceBan | None: ...
    async def banned_workspaces(self) -> list[WorkspaceBan]: ...
