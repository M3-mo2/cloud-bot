"""Terminal use-cases: open, input, output, resize, signal, close.

This service is the **policy** layer for the interactive terminal. It enforces,
in order: the ownership gate (the isolation rule is ours, not the engine's), the
per-workspace concurrency cap, and the signal allowlist. The engine's native
``sessions`` API does the real PTY work; nothing here builds a FIFO or a run-based
pseudo-terminal (INTEGRATION-GUIDE §5.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.entities import TerminalSession
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.ports.sessions import SessionStore
from cloudbot.domain.value_objects import (
    SESSION_SIGNALS,
    LogChunk,
    SessionId,
    SessionStatus,
    WorkspaceId,
)
from cloudbot.services.ownership import OwnershipService

#: PTY size bounds the engine enforces (``1..1000``; outside → 422).
MIN_COLS_ROWS = 1
MAX_COLS_ROWS = 1000
#: The geometry a session is opened with when the caller states none.
#:
#: Deliberately a real terminal baseline rather than whatever the engine would
#: hand us: almost every TUI assumes at least 80×24, and 100×30 gives a little
#: breathing room (long lines, a status line, a prompt) without wrapping so wide
#: that output becomes unreadable in a chat. The chat is only the viewport — the
#: program queries the PTY, so this is what it must see to be usable. A power user
#: can still ask for an explicit size through ``term:open`` (``--cols/--rows``),
#: and ``stty`` remains the in-session tool.
DEFAULT_COLS = 100
DEFAULT_ROWS = 30
#: ``Ctrl-D`` (EOF) as a literal control byte: it is input, not a signal.
CTRL_D = "\u0004"


@dataclass
class SessionOutput:
    data: str
    offset: int
    next: int
    eof: bool
    truncated: bool


class SessionsService:
    def __init__(
        self,
        engine: EnginePort,
        store: SessionStore,
        ownership: OwnershipService,
        moderation: "object | None" = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._ownership = ownership
        self._moderation = moderation

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    # -- open / list ---------------------------------------------------------
    async def open(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        *,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
    ) -> TerminalSession:
        await self._ownership.assert_owned(tg_id, ws_id)
        # Opening a terminal is a new execution: a banned workspace is refused at
        # the service boundary, so no path can start a shell around the ban.
        if self._moderation is not None:
            await self._moderation.assert_executable(ws_id)
        self._validate_size(cols, rows)
        await self._enforce_cap(tg_id, ws_id)
        session = await self._engine.open_session(ws_id, cols=cols, rows=rows)
        await self._store.create(tg_id, ws_id, SessionId(session.id), cols=cols, rows=rows)
        return session

    async def _enforce_cap(self, tg_id: int, ws_id: WorkspaceId) -> None:
        """Refuse before the engine, unless the engine cap can be read.

        The engine enforces ``sessions.max_per_workspace`` (409
        ``too_many_sessions``); this pre-check keeps a user from repeatedly
        hitting a raw refusal and mirrors the documented screen.
        """
        meta = await self._engine.meta()
        cap = meta.sessions.max_per_workspace
        if cap <= 0:
            return
        handles = await self._store.list_for_workspace(tg_id, ws_id)
        running = 0
        for handle in handles:
            try:
                live = await self._engine.get_session(ws_id, handle.id)
            except Exception:  # noqa: BLE001 - a vanished session is not counted
                continue
            if live.status is SessionStatus.RUNNING:
                running += 1
        if running >= cap:
            from cloudbot.core.errors import EngineRefused

            raise EngineRefused("too_many_sessions")

    async def list_for_workspace(
        self, tg_id: int, ws_id: WorkspaceId
    ) -> list[TerminalSession]:
        await self._ownership.assert_owned(tg_id, ws_id)
        handles = await self._store.list_for_workspace(tg_id, ws_id)
        merged: list[TerminalSession] = []
        for handle in handles:
            merged.append(await self._merge(ws_id, handle))
        return merged

    async def list_open_for_workspace(
        self, tg_id: int, ws_id: WorkspaceId
    ) -> list[TerminalSession]:
        sessions = await self.list_for_workspace(tg_id, ws_id)
        return [s for s in sessions if s.status is not SessionStatus.UNKNOWN]

    async def get(self, tg_id: int, ws_id: WorkspaceId, ses_id: str) -> TerminalSession:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        try:
            live = await self._engine.get_session(ws_id, ses_id)
        except Exception:
            handle = await self._store.get(tg_id, SessionId(ses_id))
            if handle is None:
                raise
            return handle
        return live

    async def _merge(self, ws_id: WorkspaceId, handle: TerminalSession) -> TerminalSession:
        try:
            live = await self._engine.get_session(ws_id, handle.id)
        except Exception:  # noqa: BLE001 - engine no longer knows it
            return TerminalSession(
                id=handle.id,
                workspace_id=ws_id,
                status=SessionStatus.EXITED,
                cols=handle.cols,
                rows=handle.rows,
                out_offset=handle.out_offset,
                created_at=handle.created_at,
            )
        return TerminalSession(
            id=live.id,
            workspace_id=ws_id,
            status=live.status,
            cols=live.cols,
            rows=live.rows,
            out_offset=handle.out_offset,
            created_at=live.created_at or handle.created_at,
            reason=live.reason,
            diagnostic=live.diagnostic,
            exit_code=live.exit_code,
            output_truncated=live.output_truncated,
        )

    async def _assert_tracked(self, tg_id: int, ses_id: str) -> None:
        if await self._store.get(tg_id, SessionId(ses_id)) is None:
            from cloudbot.core.errors import NotOwned

            raise NotOwned(ses_id)

    # -- input / output ------------------------------------------------------
    async def send_input(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str, data: str
    ) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        await self._engine.session_input(ws_id, ses_id, data)

    async def send_line(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str, command: str
    ) -> None:
        """Send a typed command to the PTY **with a newline** (Enter).

        The user types ``ls`` and sees a listing, not ``ls`` waiting for Enter.
        """
        await self.send_input(tg_id, ws_id, ses_id, command + "\n")

    async def read_output(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str, offset: int
    ) -> SessionOutput:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        chunk: LogChunk = await self._engine.session_output(ws_id, ses_id, offset=offset)
        await self._store.set_offset(tg_id, SessionId(ses_id), chunk.next)
        return SessionOutput(
            data=chunk.data,
            offset=chunk.offset,
            next=chunk.next,
            eof=chunk.eof,
            truncated=chunk.truncated,
        )

    async def follow_output(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        ses_id: str,
        *,
        initial_offset: int = 0,
        poll_interval: float = 1.0,
        max_iterations: int = 60,
    ) -> AsyncIterator[tuple[str, TerminalSession]]:
        """Yield ``(accumulated_output, session)`` until terminal + EOF.

        Bounded by ``max_iterations`` so a stuck session can never loop forever,
        and paced by ``poll_interval`` so the engine is never hammered — the same
        shape the run-log follower uses.
        """
        import asyncio

        from cloudbot.core.errors import EngineRefused

        offset = initial_offset
        accumulated = ""
        for _ in range(max_iterations):
            try:
                chunk = await self.read_output(tg_id, ws_id, ses_id, offset)
                accumulated += chunk.data
                offset = chunk.next
            except EngineRefused as exc:
                if exc.engine_code != "session_ended":
                    raise
                session = await self.get(tg_id, ws_id, ses_id)
                yield accumulated, session
                return
            session = await self.get(tg_id, ws_id, ses_id)
            yield accumulated, session
            if session.status.is_terminal and chunk.eof:
                return
            await asyncio.sleep(poll_interval)

    # -- resize / signal / close --------------------------------------------
    async def resize(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str, *, cols: int, rows: int
    ) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        self._validate_size(cols, rows)
        await self._engine.session_resize(ws_id, ses_id, cols=cols, rows=rows)
        await self._store.update_size(tg_id, SessionId(ses_id), cols=cols, rows=rows)

    async def signal(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str, signal: str
    ) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        normalized = (signal or "").upper()
        if normalized not in SESSION_SIGNALS:
            raise InvalidInput("signal", "unsupported")
        await self._engine.session_signal(ws_id, ses_id, normalized)

    async def close(
        self, tg_id: int, ws_id: WorkspaceId, ses_id: str
    ) -> TerminalSession:
        """End the session on the engine but **keep** the handle.

        Dropping the handle here would make the very next call hit
        ``_assert_tracked`` and raise ``NotOwned`` — a closed session would read
        as "not yours", which is the isolation answer and must never be produced
        by a session the caller owned. The engine keeps the ended record
        (``status: killed``) and reports ``409 session_ended`` on a later read,
        so keeping the handle lets ``_merge`` surface the documented terminal
        status. The handle is removed explicitly with :meth:`forget`.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        return await self._engine.close_session(ws_id, ses_id)

    async def forget(self, tg_id: int, ws_id: WorkspaceId, ses_id: str) -> None:
        """Drop the bot-side handle for a session the caller owns.

        This is the only path that removes a terminal row. Ownership and
        tracking are enforced first, so a foreign or unknown id still raises
        ``NotOwned``. The engine record is untouched (the engine removes it with
        the workspace); the terminal simply leaves *my terminals*.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._assert_tracked(tg_id, ses_id)
        await self._store.delete(tg_id, SessionId(ses_id))

    # -- validation ----------------------------------------------------------
    @staticmethod
    def _validate_size(cols: int, rows: int) -> None:
        for field, value in (("cols", cols), ("rows", rows)):
            if not (MIN_COLS_ROWS <= int(value) <= MAX_COLS_ROWS):
                raise InvalidInput(field, "out_of_range")
