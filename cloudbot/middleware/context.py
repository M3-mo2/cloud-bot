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
    #: Set only while an admin is inside another user's workspace: the original
    #: admin id, so panel actions are attributed to the admin while ``tg_id`` is
    #: the owner (so the ordinary ownership gate passes and the subject is right).
    admin_tg_id: int = 0
    entered_ws_id: str = ""
    entered_owner_tg_id: int = 0

    @property
    def in_entered_mode(self) -> bool:
        return bool(self.entered_ws_id)

    @property
    def actor_tg_id(self) -> int:
        """The identity to attribute actions to: the admin when entered."""
        return self.admin_tg_id or self.tg_id
