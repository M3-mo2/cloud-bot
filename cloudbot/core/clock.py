"""Clock port and its production / test implementations.

Never call ``datetime.now()`` directly in domain or services — depend on
``Clock`` so tests are deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The real clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """A clock fixed at a given instant; ``advance`` moves it for tests."""

    def __init__(self, fixed: datetime) -> None:
        self._now = fixed
        if self._now.tzinfo is None:
            self._now = self._now.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(**kwargs)
