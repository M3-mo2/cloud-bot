"""Settings validation: modes, admin gate, secret handling."""

from __future__ import annotations

import pytest

from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode


def test_defaults():
    s = Settings(bot_token="x", engine_api_key="k", _env_file=None)
    assert s.mode is RunMode.PRODUCTION
    assert s.default_locale is Locale.EN
    assert s.engine_timeout_s == 30.0
    assert isinstance(s.bot_token.get_secret_value(), str)


def test_unknown_mode_is_fatal():
    with pytest.raises(Exception):
        Settings(mode="staging", _env_file=None)


def test_debug_requires_admin_ids():
    with pytest.raises(Exception):
        Settings(mode="debug", admin_ids="", _env_file=None)
    s = Settings(mode="debug", admin_ids="111,222", _env_file=None)
    assert s.admin_ids == frozenset({111, 222})
    assert s.is_admin(111)
    assert not s.is_admin(333)


def test_admin_ids_parsing():
    s = Settings(admin_ids="1, 2 ,3", _env_file=None)
    assert s.admin_ids == frozenset({1, 2, 3})


def test_secret_not_in_repr():
    s = Settings(bot_token="supersecret", engine_api_key="anothersecret", _env_file=None)
    assert "supersecret" not in repr(s.bot_token)
    assert "anothersecret" not in repr(s.engine_api_key)
