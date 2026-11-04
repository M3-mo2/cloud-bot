"""Runtime modes: a single frozen behaviour table.

No component branches on the raw ``RunMode`` enum; components ask this table
(``allows_admin_commands(mode)``, ``mock_engine(mode)``, ``verbose_errors(mode)``),
so every behavioural difference between modes is reviewable in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum


class RunMode(str, Enum):
    PRODUCTION = "production"
    DEV = "dev"
    DEBUG = "debug"


@dataclass(frozen=True)
class ModeProfile:
    """What each mode changes (edge behaviour only — never domain logic)."""

    name: RunMode
    log_level: int
    log_json: bool
    log_trace_payloads: bool
    verbose_errors: bool
    show_raw_payloads: bool
    admin_commands: bool
    mock_engine_allowed: bool
    throttle_enabled: bool
    throttle_relaxed: bool
    persistence_fsm: bool


_PROFILES: dict[RunMode, ModeProfile] = {
    RunMode.PRODUCTION: ModeProfile(
        name=RunMode.PRODUCTION,
        log_level=logging.INFO,
        log_json=True,
        log_trace_payloads=False,
        verbose_errors=False,
        show_raw_payloads=False,
        admin_commands=False,
        mock_engine_allowed=False,
        throttle_enabled=True,
        throttle_relaxed=False,
        persistence_fsm=True,
    ),
    RunMode.DEV: ModeProfile(
        name=RunMode.DEV,
        log_level=logging.DEBUG,
        log_json=False,
        log_trace_payloads=False,
        verbose_errors=True,
        show_raw_payloads=False,
        admin_commands=True,
        mock_engine_allowed=True,
        throttle_enabled=True,
        throttle_relaxed=True,
        persistence_fsm=False,
    ),
    RunMode.DEBUG: ModeProfile(
        name=RunMode.DEBUG,
        log_level=logging.DEBUG,
        log_json=False,
        log_trace_payloads=True,
        verbose_errors=True,
        show_raw_payloads=True,
        admin_commands=True,
        mock_engine_allowed=False,
        throttle_enabled=False,
        throttle_relaxed=False,
        persistence_fsm=False,
    ),
}


def profile(mode: RunMode) -> ModeProfile:
    return _PROFILES[mode]


def log_level(mode: RunMode) -> int:
    return _PROFILES[mode].log_level


def log_json(mode: RunMode) -> bool:
    return _PROFILES[mode].log_json


def allows_admin_commands(mode: RunMode) -> bool:
    return _PROFILES[mode].admin_commands


def mock_engine_allowed(mode: RunMode) -> bool:
    return _PROFILES[mode].mock_engine_allowed


def verbose_errors(mode: RunMode) -> bool:
    return _PROFILES[mode].verbose_errors


def shows_raw_payloads(mode: RunMode) -> bool:
    """Whether raw engine payloads may be shown at all (DEBUG only)."""
    return _PROFILES[mode].show_raw_payloads


def throttle_enabled(mode: RunMode) -> bool:
    return _PROFILES[mode].throttle_enabled


def throttle_relaxed(mode: RunMode) -> bool:
    return _PROFILES[mode].throttle_relaxed


def persistence_fsm(mode: RunMode) -> bool:
    return _PROFILES[mode].persistence_fsm
