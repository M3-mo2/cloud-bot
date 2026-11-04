"""Admin-panel screens and the section registry.

Importing this package registers every built-in section. Adding a section is one
module here (which calls ``section.register(...)``) plus importing it below — the
menu builder in ``overview.py`` reads the registry, so no menu code changes.
"""

from __future__ import annotations

from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, SectionRegistry, register, registry

# Import for the side effect of registering each section.
from . import overview as _overview  # noqa: E402,F401
from . import users as _users  # noqa: E402,F401
from . import user_page as _user_page  # noqa: E402,F401
from . import workspace as _workspace  # noqa: E402,F401
from . import notifications as _notifications  # noqa: E402,F401
from . import audit as _audit  # noqa: E402,F401

__all__ = [
    "AdminSection",
    "SectionRegistry",
    "registry",
    "register",
    "Screen",
]
