"""``SessionStore`` — terminal session handles (Phase 1.5).

Maps a Telegram user ↔ engine ``ses_...`` plus the output offset cursor, the PTY
size and the created time. The engine remains the source of truth for a session's
*status*; this store only holds the bot-side handle so a user can be shown their
own terminals after a restart.
"""

from __future__ import annotations

from typing import Protocol

from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.value_objects import SessionId, WorkspaceId


class SessionStore(Protocol):
    async def create(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        ses_id: SessionId,
        *,
        cols: int = 80,
        rows: int = 24,
    ) -> None: ...
    async def get(self, tg_id: int, ses_id: SessionId) -> TerminalSession | None: ...
    async def set_offset(self, tg_id: int, ses_id: SessionId, offset: int) -> None: ...
    async def update_size(self, tg_id: int, ses_id: SessionId, *, cols: int, rows: int) -> None: ...
    async def list_for_user(self, tg_id: int) -> list[TerminalSession]: ...
    async def list_for_workspace(self, tg_id: int, ws_id: WorkspaceId) -> list[TerminalSession]: ...
    async def delete(self, tg_id: int, ses_id: SessionId) -> None: ...
