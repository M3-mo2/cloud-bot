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

import asyncio
from collections import defaultdict

from cloudbot.core.errors import InvalidInput
from cloudbot.domain.meta import EngineMeta
from cloudbot.domain.ports.engine import EnginePort
from cloudbot.domain.value_objects import Tunnel, TunnelList, WorkspaceId
from cloudbot.services.ownership import OwnershipService

#: A publishable port range (documented 1024–65535); anything below is reserved
#: and the engine would refuse it, so we refuse locally instead.
MIN_PORT = 1024
MAX_PORT = 65535


class TunnelAlreadyPublished(Exception):
    """A tunnel already exists for this (user, workspace, port) — do not create a second.

    Not a ``DomainError``: it is the idempotency outcome of a legitimate request,
    not a refusal. It carries the existing tunnel so the handler can surface it and
    say so.
    """

    def __init__(self, tunnel: Tunnel) -> None:
        super().__init__(tunnel.id)
        self.tunnel = tunnel


class TunnelsService:
    def __init__(self, engine: EnginePort, ownership: OwnershipService) -> None:
        self._engine = engine
        self._ownership = ownership
        #: ``/v1/meta`` is the same for every workspace and changes rarely; cache
        #: it so rendering the ceiling (``tunnels.max_per_workspace``) is not a new
        #: engine call on every screen. The adapter's own meta cache backs this up.
        self._meta: EngineMeta | None = None
        #: One lock per (tg_id, ws_id, port): a create is slow (``url_timeout_sec``,
        #: 45 s), so a double-tap would otherwise run two creates and burn the whole
        #: per-workspace allowance on one port (F-03). The lock serialises the two;
        #: the second then sees the tunnel the first created and does not duplicate
        #: it. Keyed by user **and** workspace so no state leaks across either.
        self._locks: "defaultdict[tuple[int, str, int], asyncio.Lock]" = defaultdict(asyncio.Lock)

    async def meta(self, refresh: bool = False) -> EngineMeta:
        if self._meta is None or refresh:
            self._meta = await self._engine.meta(refresh=refresh)
        return self._meta

    async def limit(self) -> int:
        """The engine's per-workspace tunnel cap (never hardcoded)."""
        return (await self.meta()).tunnels.max_per_workspace

    async def list(self, tg_id: int, ws_id: WorkspaceId) -> TunnelList:
        await self._ownership.assert_owned(tg_id, ws_id)
        return await self._engine.list_tunnels(ws_id)

    async def publish(self, tg_id: int, ws_id: WorkspaceId, port: int) -> Tunnel:
        """Create one tunnel for a port, or surface the existing one (idempotent).

        A repeat tap and a create already in flight for the same port both
        short-circuit: the existing tunnel is returned via
        :class:`TunnelAlreadyPublished` rather than a second engine create. A failed
        create releases the lock, so a retry works.
        """
        await self._ownership.assert_owned(tg_id, ws_id)
        validate_port(port)
        key = (tg_id, str(ws_id), port)
        async with self._locks[key]:
            existing = await self._engine.list_tunnels(ws_id)
            for tunnel in existing.tunnels:
                if tunnel.port == port:
                    # A tunnel already exists for this port — never create a second.
                    raise TunnelAlreadyPublished(tunnel)
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
