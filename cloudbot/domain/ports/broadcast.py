"""``BroadcastStore`` — persistence for a broadcast run and its frozen audience.

The service orchestrates; this port only persists. The audience is *computed
here* from stored state (users, workspaces, subscriptions), never guessed, so the
count the admin sees is a number the database produced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from cloudbot.domain.broadcast import (
    AudienceKey,
    BroadcastRun,
    BroadcastSource,
    FailureKind,
)

class BroadcastStore(Protocol):
    # -- audience -----------------------------------------------------------
    async def audience_members(self, audience: AudienceKey, *, now: datetime) -> list[int]:
        """The tg ids the preset selects, computed from stored state."""
        ...

    # -- runs ---------------------------------------------------------------
    async def create_draft(
        self,
        *,
        source: BroadcastSource,
        created_by: int,
        created_at: datetime,
    ) -> BroadcastRun:
        """Persist the composed message reference before any audience is chosen."""
        ...

    async def plan_audience(
        self,
        run_id: int,
        *,
        audience: AudienceKey,
        recipients: list[int],
        now: datetime,
    ) -> BroadcastRun | None:
        """Freeze an audience onto a composing draft (replace any targets)."""
        ...

    async def create_run(
        self,
        *,
        source: BroadcastSource,
        audience: AudienceKey,
        total: int,
        recipients: list[int],
        created_by: int,
        created_at: datetime,
    ) -> BroadcastRun: ...

    async def get_run(self, run_id: int) -> BroadcastRun | None: ...

    async def latest_draft(self, *, created_by: int) -> BroadcastRun | None:
        """This admin's newest ``composing`` run, if any (the restart fast path)."""
        ...

    async def open_run(self) -> BroadcastRun | None:
        """The run in ``sending`` state, if any (the in-flight guard's truth)."""
        ...

    async def claim_run(self, run_id: int, *, now: datetime) -> bool:
        """Atomically move a run to ``sending`` when no other run is sending."""
        ...

    async def mark_interrupted(self, run_id: int, *, now: datetime) -> None: ...

    # -- targets ------------------------------------------------------------
    async def pending_targets(self, run_id: int) -> list[int]: ...

    async def failed_targets(self, run_id: int) -> list[int]: ...

    async def failure_counts(self, run_id: int) -> dict[str, int]: ...

    async def record_success(self, run_id: int, tg_id: int, *, now: datetime) -> None: ...

    async def record_failure(
        self, run_id: int, tg_id: int, error: FailureKind, *, now: datetime
    ) -> None: ...

    async def reset_failed(self, run_id: int) -> int:
        """Return failed targets to pending for a retry run; returns how many."""
        ...

    async def finish_run(
        self, run_id: int, status: str, *, stop_reason: str, now: datetime
    ) -> None: ...

    # -- history ------------------------------------------------------------
    async def history(self, *, limit: int = 10) -> list[BroadcastRun]: ...
