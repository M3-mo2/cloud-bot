"""The event source — one seam.

Handlers do not write to the database. They hand a ``RequestEvents`` recorder the
*intent* to log something (``events.workspace_created(...)``). ``EventSourceMiddleware``
is the single writer: it flushes the intents to the ``EventLog`` **after** the
handler succeeded, and emits ``engine.unreachable`` when the engine could not be
reached. Adding a category is a ``domain/events.py`` member plus one small method
here; the dispatcher and the notifier are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cloudbot.domain.events import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    EventCategory,
    NewEvent,
    Severity,
    actor_admin,
)
from cloudbot.domain.ports.events import EventLog


@dataclass
class RequestEvents:
    """Intent buffer for one update. Never does I/O itself."""

    intents: list[NewEvent] = field(default_factory=list)

    def _record(self, event: NewEvent) -> None:
        self.intents.append(event)

    # -- user lifecycle ------------------------------------------------------
    def user_new(self, *, tg_id: int, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.USER_NEW,
                summary_key="event.summary.user.new",
                actor=ACTOR_USER,
                tg_id=tg_id,
                params={"name": name},
            )
        )

    def user_blocked(self, *, tg_id: int, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.USER_BLOCKED,
                summary_key="event.summary.user.blocked",
                actor=ACTOR_USER,
                tg_id=tg_id,
                params={"name": name},
            )
        )

    # -- workspaces ----------------------------------------------------------
    def workspace_created(self, *, tg_id: int, ws_id: str, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.WORKSPACE_CREATED,
                summary_key="event.summary.workspace.created",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"name": name},
            )
        )

    def workspace_deleted(self, *, tg_id: int, ws_id: str, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.WORKSPACE_DELETED,
                summary_key="event.summary.workspace.deleted",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"name": name},
            )
        )

    def workspace_suspended(self, *, tg_id: int, ws_id: str, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.WORKSPACE_SUSPENDED,
                summary_key="event.summary.workspace.suspended",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"name": name},
            )
        )

    # -- runs / packages / tunnels ------------------------------------------
    def run_started(self, *, tg_id: int, ws_id: str, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.RUN_STARTED,
                summary_key="event.summary.run.started",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"name": name},
            )
        )

    def run_failed(self, *, tg_id: int, ws_id: str, name: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.RUN_FAILED,
                summary_key="event.summary.run.failed",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"name": name},
            )
        )

    def package_install_failed(self, *, tg_id: int, ws_id: str, names: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.PACKAGE_INSTALL_FAILED,
                summary_key="event.summary.package.install_failed",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"names": names},
            )
        )

    def tunnel_created(self, *, tg_id: int, ws_id: str, port: int) -> None:
        self._record(
            NewEvent(
                category=EventCategory.TUNNEL_CREATED,
                summary_key="event.summary.tunnel.created",
                actor=ACTOR_USER,
                tg_id=tg_id,
                ws_id=ws_id,
                params={"port": port},
            )
        )

    # -- admin ---------------------------------------------------------------
    def admin_action(
        self,
        *,
        admin_id: int,
        action: str,
        ws_id: str | None = None,
        owner_tg_id: int | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        """Record an admin action. The actor is the ADMIN, never the subject.

        This is the hard rule: an action taken in entered mode is attributed to
        the admin and to the workspace, and the log keeps the two apart.
        """
        merged = dict(params or {})
        merged["action"] = action
        self._record(
            NewEvent(
                category=EventCategory.ADMIN_ACTION,
                summary_key="event.summary.admin.action",
                actor=actor_admin(admin_id),
                tg_id=owner_tg_id,
                ws_id=ws_id,
                params=merged,
            )
        )

    # -- system --------------------------------------------------------------
    def engine_unreachable(self, *, reason: str) -> None:
        self._record(
            NewEvent(
                category=EventCategory.ENGINE_UNREACHABLE,
                summary_key="event.summary.engine.unreachable",
                actor=ACTOR_SYSTEM,
                severity=Severity.ERROR,
                params={"reason": reason},
            )
        )


class EventsService:
    """The single writer of intents to the persisted log."""

    def __init__(self, log: EventLog) -> None:
        self._log = log

    async def flush(self, events: RequestEvents) -> list[int]:
        written: list[int] = []
        for intent in events.intents:
            written.append(await self._log.append(intent))
        events.intents.clear()
        return written

    async def emit(self, event: NewEvent) -> int:
        return await self._log.append(event)
