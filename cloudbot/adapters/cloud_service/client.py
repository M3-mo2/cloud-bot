"""httpx transport: auth, retries (idempotent verbs only), timeouts, meta cache.

This is the only module in the codebase that imports ``httpx`` for real traffic.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from cloudbot.adapters.cloud_service.errors import translate
from cloudbot.core.errors import EngineUnavailable

_IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "PATCH", "DELETE"})
_RETRY_STATUSES = frozenset({429, 502, 503, 504})


class EngineClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _is_retryable(self, method: str, path: str) -> bool:
        if method not in _IDEMPOTENT_METHODS:
            return False
        # Creation POSTs are never retried; idempotent verbs always may be.
        return True

    async def _retry_after(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        files: Any = None,
        data: Any = None,
        timeout: float | None = None,
        raw: bool = False,
    ) -> httpx.Response:
        attempt = 0
        last_exc: Exception | None = None
        while True:
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    content=content,
                    files=files,
                    data=data,
                    timeout=timeout,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < self._max_retries and self._is_retryable(method, path):
                    await self._backoff(attempt)
                    attempt += 1
                    continue
                raise EngineUnavailable(retryable=True, cause=str(exc)) from exc

            if response.status_code < 400:
                return response

            if (
                response.status_code in _RETRY_STATUSES
                and self._is_retryable(method, path)
                and attempt < self._max_retries
            ):
                retry_after = await self._retry_after(response)
                await self._backoff(attempt, retry_after)
                attempt += 1
                continue

            raise translate(
                response.status_code,
                response.content,
                path=path,
                retry_after=await self._retry_after(response),
            )

    async def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        if retry_after is not None:
            await asyncio.sleep(min(retry_after, 10.0))
            return
        base = min(0.5 * (2**attempt), 5.0)
        await asyncio.sleep(base + random.uniform(0, base / 2))

    # -- convenience wrappers ------------------------------------------------
    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)
