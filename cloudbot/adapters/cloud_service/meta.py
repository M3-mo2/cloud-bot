"""``/v1/meta`` cache with refresh-on-stale-refusal.

Guarded by an async lock so concurrent refreshes collapse into one.
"""

from __future__ import annotations

import asyncio

from cloudbot.adapters.cloud_service.client import EngineClient
from cloudbot.adapters.cloud_service.mapping import meta_from_dict
from cloudbot.domain.meta import EngineMeta


class MetaCache:
    def __init__(self, client: EngineClient) -> None:
        self._client = client
        self._meta: EngineMeta | None = None
        self._lock = asyncio.Lock()

    @property
    def cached(self) -> EngineMeta | None:
        return self._meta

    async def get(self, refresh: bool = False) -> EngineMeta:
        if self._meta is not None and not refresh:
            return self._meta
        async with self._lock:
            if self._meta is not None and not refresh:
                return self._meta
            response = await self._client.get("/v1/meta")
            self._meta = meta_from_dict(response.json())
            return self._meta

    async def refresh(self) -> EngineMeta:
        return await self.get(refresh=True)
