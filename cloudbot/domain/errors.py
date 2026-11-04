"""Domain errors, re-exported from ``core.errors`` for domain-layer use.

The canonical definitions live in ``core.errors`` so that both ``core`` and
``domain`` may reference them without an inward dependency inversion.
"""

from cloudbot.core.errors import (
    DomainError,
    EngineRefused,
    EngineUnavailable,
    InvalidInput,
    NotOwned,
    RateLimited,
    WorkspaceMissing,
)

__all__ = [
    "DomainError",
    "EngineRefused",
    "EngineUnavailable",
    "InvalidInput",
    "NotOwned",
    "RateLimited",
    "WorkspaceMissing",
]
