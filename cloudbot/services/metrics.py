"""Workspace resource-metrics use-cases (Phase 2d): one-shot snapshot.

The panel is a **one-shot snapshot the user refreshes**, not a standing SSE
connection: the stream is SSE over HTTP (already measured to be unusable through
this project's Cloudflare tunnel) and a standing connection does not survive a
Telegram update loop. There is therefore no server-side interval to validate
against for the snapshot call; the interval bounds are still enforced for any
caller that supplies one, because a value below ``metrics.min_interval_sec`` is
refused by the engine with ``422`` and must be caught locally first.

``*_complete`` is carried through untouched: the screen decides whether a false
figure may be shown as an exact number (it may not).
"""

from __future__ import annotations

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceMetrics
from cloudbot.services.ownership import OwnershipService


class MetricsService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def snapshot(
        self, tg_id: int, ws_id: WorkspaceId, *, interval_sec: float | None = None
    ) -> WorkspaceMetrics:
        await self._ownership.assert_owned(tg_id, ws_id)
        if interval_sec is not None:
            validate_interval(interval_sec, await self._engine.meta())
        return await self._engine.workspace_metrics(ws_id)


def validate_interval(interval_sec: float, meta: EngineMeta) -> float:
    """Return the interval if within the engine's advertised bounds.

    A request below ``metrics.min_interval_sec`` is refused locally (the engine
    answers ``422`` rather than silently raising), so the user sees an honest
    "too frequent" message and never hammers the sampler.
    """
    bounds = meta.metrics
    try:
        value = float(interval_sec)
    except (TypeError, ValueError):
        raise InvalidInput("interval", "invalid")
    if value < bounds.min_interval_sec:
        raise InvalidInput("interval", "below_min")
    if value > bounds.max_interval_sec:
        raise InvalidInput("interval", "above_max")
    return value
