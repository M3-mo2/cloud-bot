"""Mode behaviour table."""

from __future__ import annotations

from cloudbot.core import modes
from cloudbot.core.modes import RunMode


def test_admin_commands_only_dev_debug():
    assert not modes.allows_admin_commands(RunMode.PRODUCTION)
    assert modes.allows_admin_commands(RunMode.DEV)
    assert modes.allows_admin_commands(RunMode.DEBUG)


def test_raw_payloads_debug_only():
    assert not modes.shows_raw_payloads(RunMode.PRODUCTION)
    assert not modes.shows_raw_payloads(RunMode.DEV)
    assert modes.shows_raw_payloads(RunMode.DEBUG)


def test_mock_engine_allowed_only_dev():
    assert not modes.mock_engine_allowed(RunMode.PRODUCTION)
    assert modes.mock_engine_allowed(RunMode.DEV)
    assert not modes.mock_engine_allowed(RunMode.DEBUG)


def test_throttle_states():
    assert modes.throttle_enabled(RunMode.PRODUCTION)
    assert not modes.throttle_relaxed(RunMode.PRODUCTION)
    assert modes.throttle_relaxed(RunMode.DEV)
    assert not modes.throttle_enabled(RunMode.DEBUG)


def test_no_component_branches_in_raw_enum():
    # The table is the single source; PRODUCTION is less verbose than DEV.
    assert modes.log_level(RunMode.PRODUCTION) > modes.log_level(RunMode.DEV)
