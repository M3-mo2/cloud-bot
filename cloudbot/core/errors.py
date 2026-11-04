"""Domain error base classes.

Errors carry **codes and structured fields, never user text**. The presentation
layer maps them to localized catalog keys. Nothing ever prints ``str(exc)`` to a
user.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every error the domain/services may raise."""

    code: str = "domain_error"


class EngineUnavailable(DomainError):
    """The engine could not be reached or answered with a transient failure."""

    code = "engine_unavailable"

    def __init__(self, *, retryable: bool, cause: str = "") -> None:
        super().__init__(cause or self.code)
        self.retryable = retryable
        self.cause = cause


class EngineRefused(DomainError):
    """A valid request the engine refused (422/409/403/404/413/415/507)."""

    code = "engine_refused"

    def __init__(
        self,
        code: str,
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.engine_code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


class NotOwned(DomainError):
    """The isolation gate: the caller does not own the workspace."""

    code = "not_owned"

    def __init__(self, ws_id: str) -> None:
        super().__init__(ws_id)
        self.ws_id = ws_id


class WorkspaceMissing(DomainError):
    code = "workspace_missing"

    def __init__(self, ws_id: str) -> None:
        super().__init__(ws_id)
        self.ws_id = ws_id


class InvalidInput(DomainError):
    code = "invalid_input"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class RateLimited(DomainError):
    code = "rate_limited"

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"retry after {retry_after_s}s")
        self.retry_after_s = retry_after_s
