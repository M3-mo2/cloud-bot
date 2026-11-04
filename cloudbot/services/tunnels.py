"""Tunnel use-cases (Phase 2b): publish a port, list, remove.

The URL is **ephemeral** by the roadmap's rule: it is returned only for the live
screen and is never persisted here (there is no store for tunnels at all). The
engine gates a create on three switches — ``tunnels.enabled`` (server),
``limits.allow_tunnels`` (workspace) and ``limits.allow_net`` (cloudflared needs
egress) — and each must be explained to the user; the domain simply surfaces the
engine's ``422 tunnels_disabled`` refusal (the presentation layer maps its
``details.config``/``details.field`` to the right sentence).
"""

from __future__ import annotations

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import Tunnel, TunnelList, WorkspaceId
from cloudbot.services.ownership import OwnershipService

#: A publishable port range (documented 1024–65535); anything below is reserved
#: and the engine would refuse it, so we refuse locally instead.
MIN_PORT = 1024
MAX_PORT = 65535


class TunnelsService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership

    async def meta(self) -> EngineMeta:
        return await self._engine.meta()

    async def list(self, tg_id: int, ws_id: WorkspaceId) -> TunnelList:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_tunnels(ws_id)

    async def publish(self, tg_id: int, ws_id: WorkspaceId, port: int) -> Tunnel:
        await self._ownership.assert_owned(tg_id, ws_id)
        validate_port(port)
        return await self._engine.create_tunnel(ws_id, port)

    async def remove(self, tg_id: int, ws_id: WorkspaceId, tunnel_id: str) -> None:
        await self._ownership.assert_owned(tg_id, ws_id)
        await self._engine.delete_tunnel(ws_id, tunnel_id)


def validate_port(port: int) -> int:
    """A plausible port number, in the documented publishable range."""
    try:
        value = int(port)
    except (TypeError, ValueError):
        raise InvalidInput("port", "invalid")
    if not (MIN_PORT <= value <= MAX_PORT):
        raise InvalidInput("port", "out_of_range")
    return value


def parse_port(text: str) -> int:
    """Parse a typed port or raise ``InvalidInput`` (never a raw ``ValueError``)."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise InvalidInput("port", "empty")
    try:
        value = int(cleaned)
    except ValueError:
        raise InvalidInput("port", "invalid")
    return validate_port(value)
