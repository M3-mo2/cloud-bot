"""``UserContext`` — resolved once per update and attached to handler data."""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode


@dataclass(frozen=True)
class UserContext:
    tg_id: int
    locale: Locale
    is_admin: bool
    mode: RunMode
    display_name: str = ""
    correlation_id: str = ""
