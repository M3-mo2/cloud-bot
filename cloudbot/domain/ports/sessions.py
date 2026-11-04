"""``SessionStore`` — terminal session handles (Phase 1.5).

Maps a Telegram user ↔ engine ``ses_...`` plus the output offset cursor.
"""

from __future__ import annotations

from typing import Protocol

from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionId, WorkspaceId


class SessionStore(Protocol):
    async def create(self, tg_id: int, ws_id: WorkspaceId, ses_id: SessionId) -> None: ...
    async def get(self, tg_id: int, ses_id: SessionId) -> TerminalSession | None: ...
    async def set_offset(self, tg_id: int, ses_id: SessionId, offset: int) -> None: ...
    async def list_for_user(self, tg_id: int) -> list[TerminalSession]: ...
    async def delete(self, tg_id: int, ses_id: SessionId) -> None: ...
