"""HTTP → domain error translation.

Every non-2xx response becomes a typed ``domain.errors`` value; the raw engine
``message`` is preserved on the error for **logging** (and the DEBUG+admin
collapsed block), never rendered to a normal user.
"""

from __future__ import annotations

import json
from typing import Any

from cloudbot.core.errors import DomainError, EngineRefused, EngineUnavailable, RateLimited

#: Every stable engine error code the adapter recognises (SPEC §2.8 /
#: INTEGRATION-GUIDE §9.3). Used by tests to assert exhaustive coverage.
KNOWN_CODES: frozenset[str] = frozenset(
    {
        "validation",
        "unauthorized",
        "forbidden",
        "subscription_expired",
        "not_found",
        "ports_disabled",
        "conflict",
        "workspace_suspended",
        "too_many_sessions",
        "session_ended",
        "archive_entry_exists",
        "payload_too_large",
        "archive_too_large",
        "archive_too_many_entries",
        "archive_ratio_exceeded",
        "unsupported_archive",
        "unprocessable",
        "unknown_profile",
        "not_directory",
        "session_needs_interpreter",
        "subscription_invalid",
        "max_duration_exceeded",
        "subscription_would_shorten",
        "tunnels_disabled",
        "too_many_tunnels",
        "archive_encrypted",
        "archive_multivolume",
        "archive_unsafe_entry",
        "write_rate_exceeded",
        "internal",
        "unavailable",
        "tunnel_url_unavailable",
        "insufficient_storage",
        "timeout",
    }
)

#: Codes that map to a transient ``EngineUnavailable`` rather than a refusal.
_UNAVAILABLE_CODES = {"internal"}

#: Codes that map to the bot's own ``RateLimited``.
_RATE_LIMITED_CODES = {"write_rate_exceeded"}


def _parse_envelope(body: bytes) -> tuple[str, str, dict[str, Any]]:
    if not body:
        return "", "", {}
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "", "", {}
    if not isinstance(parsed, dict):
        return "", "", {}
    error = parsed.get("error")
    if isinstance(error, dict):
        code = str(error.get("code", "") or "")
        message = str(error.get("message", "") or "")
        details = error.get("details") or {}
        if not isinstance(details, dict):
            details = {"detail": details}
        return code, message, details
    return "", "", {}


def _synthetic_code(status: int) -> str:
    return {
        400: "validation",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "validation",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_archive",
        422: "unprocessable",
        429: "write_rate_exceeded",
        500: "internal",
        502: "unavailable",
        503: "unavailable",
        504: "timeout",
        507: "insufficient_storage",
    }.get(status, "unknown")


def translate(
    status: int,
    body: bytes = b"",
    *,
    path: str = "",
    retry_after: float | None = None,
) -> DomainError:
    """Convert a non-2xx response into a typed domain error."""
    code, message, details = _parse_envelope(body)
    if not code:
        code = _synthetic_code(status)

    if code == "unauthorized":
        return EngineUnavailable(retryable=False, cause="auth")
    if code in _RATE_LIMITED_CODES:
        return RateLimited(retry_after_s=retry_after if retry_after is not None else 5.0)
    if code in _UNAVAILABLE_CODES:
        return EngineUnavailable(retryable=True, cause=message)
    if code == "unavailable":
        return EngineUnavailable(retryable=True, cause=message)
    if status >= 500:
        # Any unmodelled 5xx is transient.
        return EngineUnavailable(retryable=True, cause=message)
    return EngineRefused(code, message=message, details=details)
