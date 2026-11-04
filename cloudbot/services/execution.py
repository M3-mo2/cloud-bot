"""Execution use-cases: exec, runs, offset-follow log tailing, stop, purge.

The offset-follow helper is the one place that knows run output is read from the
logs endpoint (never from the run object), per INTEGRATION-GUIDE §4.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.entities import Run
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import ExecResult, LogChunk, RunPage, WorkspaceId
from cloudbot.services.ownership import OwnershipService

#: exec is hard-capped at 60 s by the engine; the bot never offers more.
EXEC_MAX_SEC = 60
#: Default history page size.
RUNS_PAGE_LIMIT = 10


@dataclass
class TailState:
    stdout: str = ""
    stderr: str = ""
    offset_stdout: int = 0
    offset_stderr: int = 0
    truncated: bool = False
    eof: bool = False


class ExecutionService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    # -- exec ----------------------------------------------------------------
    def clamp_exec_timeout(self, timeout_sec: int | None) -> int:
        if timeout_sec is None:
            return EXEC_MAX_SEC
        if timeout_sec <= 0:
            raise InvalidInput("timeout_sec", "non_positive")
        return min(timeout_sec, EXEC_MAX_SEC)

    async def exec(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        cmd: list[str] | str,
        *,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        await self._ownership.assert_owned(tg_id, ws_id)
        clamped = self.clamp_exec_timeout(timeout_sec)
        return await self._engine.exec(ws_id, cmd, cwd=cwd, timeout_sec=clamped)

    # -- runs ----------------------------------------------------------------
    async def run_command(
        self, tg_id: int, ws_id: WorkspaceId, argv: list[str], *, cwd: str | None = None
    ) -> Run:
        await self._ownership.assert_owned(tg_id, ws_id)
        if not argv:
            raise InvalidInput("argv", "empty")
        return await self._engine.create_run(ws_id, kind="command", argv=argv, cwd=cwd)

    async def run_file(
        self, tg_id: int, ws_id: WorkspaceId, path: str, *, args: list[str] | None = None
    ) -> Run:
        await self._ownership.assert_owned(tg_id, ws_id)
        if not path:
            raise InvalidInput("file", "empty")
        return await self._engine.create_run(ws_id, kind="file", file=path, args=args)

    async def get_run(self, tg_id: int, ws_id: WorkspaceId, run_id: str) -> Run:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.get_run(ws_id, run_id)

    async def list_runs(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = RUNS_PAGE_LIMIT,
        offset: int = 0,
    ) -> RunPage:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_runs(
            ws_id, status=status, kind=kind, limit=limit, offset=offset
        )

    async def tail_logs(
        self, tg_id: int, ws_id: WorkspaceId, run_id: str, stream: str, offset: int
    ) -> LogChunk:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.run_logs(ws_id, run_id, stream=stream, offset=offset)

    async def read_all_logs(
        self, tg_id: int, ws_id: WorkspaceId, run_id: str, state: TailState | None = None
    ) -> TailState:
        """Read both streams to EOF once, updating the offset cursor."""
        await self._ownership.assert_owned(tg_id, ws_id)
        st = state or TailState()
        stdout_chunk = await self._engine.run_logs(
            ws_id, run_id, stream="stdout", offset=st.offset_stdout
        )
        st.stdout += stdout_chunk.data
        st.offset_stdout = stdout_chunk.next
        st.truncated = st.truncated or stdout_chunk.truncated
        st.eof = stdout_chunk.eof
        stderr_chunk = await self._engine.run_logs(
            ws_id, run_id, stream="stderr", offset=st.offset_stderr
        )
        st.stderr += stderr_chunk.data
        st.offset_stderr = stderr_chunk.next
        st.truncated = st.truncated or stderr_chunk.truncated
        return st

    async def follow(
        self,
        tg_id: int,
        ws_id: WorkspaceId,
        run_id: str,
        *,
        initial: TailState | None = None,
        poll_interval: float = 1.5,
        max_iterations: int = 200,
    ) -> AsyncIterator[tuple[TailState, Run]]:
        """Yield ``(state, run)`` until the run is terminal and logs are at EOF.

        Bounded by ``max_iterations`` so a stuck run can never loop forever.
        """
        import asyncio

        state = initial or TailState()
        for _ in range(max_iterations):
            state = await self.read_all_logs(tg_id, ws_id, run_id, state)
            run = await self.get_run(tg_id, ws_id, run_id)
            yield state, run
            if run.status.is_terminal and state.eof:
                return
            await asyncio.sleep(poll_interval)

    async def stop_run(self, tg_id: int, ws_id: WorkspaceId, run_id: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._engine.stop_run(ws_id, run_id)

    async def purge(self, tg_id: int, ws_id: WorkspaceId) -> int:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.purge_runs(ws_id)

    async def delete_run(self, tg_id: int, ws_id: WorkspaceId, run_id: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._engine.delete_run(ws_id, run_id)
