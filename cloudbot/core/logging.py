"""structlog setup: level per mode, JSON vs pretty, and secret redaction.

The rule that never bends: never print, log, or echo a secret. This module is
the single place logging is configured, and it redacts any value that looks like
a bearer token or that is passed under a known secret key.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from cloudbot.core.modes import RunMode, log_json

_SECRET_KEYS = {
    "bot_token",
    "engine_api_key",
    "authorization",
    "bearer",
    "api_key",
    "token",
    "secret",
    "password",
}


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in _SECRET_KEYS:
            event_dict[key] = "***"
        elif isinstance(event_dict[key], str) and "Bearer " in event_dict[key]:
            event_dict[key] = "***"
    return event_dict


def configure_logging(mode: RunMode, level: int) -> None:
    """Configure stdlib + structlog once, for the process lifetime."""
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if log_json(mode):
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
        force=True,
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def startup_banner(mode: RunMode) -> None:
    """Log the mode at startup (the 'seven-day rule' nudge for DEBUG)."""
    log = structlog.get_logger("cloudbot")
    log.info("cloudbot.startup", mode=mode.value)
    if mode is RunMode.DEBUG:
        log.warning(
            "cloudbot.debug_mode",
            note="DEBUG is for incidents only; it is not for production traffic.",
        )
