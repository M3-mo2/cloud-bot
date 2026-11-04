"""The bot's own runtime facts (uptime). Kept separate from the request path."""

from __future__ import annotations

from datetime import datetime, timezone


class BotRuntime:
    """A tiny, injectable record of when this process started.

    Used only by the admin overview for an honest uptime reading; tests create
    their own with a frozen start.
    """

    def __init__(self, started_at: datetime | None = None) -> None:
        self.started_at = started_at or datetime.now(timezone.utc)

    def uptime_seconds(self, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - self.started_at).total_seconds())
