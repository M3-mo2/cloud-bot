"""TASK backup-repair: a failure message must name the problem and never a secret.

``scrub`` is the guard both the stored ``last_error`` and every backup log line go
through. These tests pin its contract with the exact shapes the incident data
carries: a Postgres DSN with a password, a bot token, and a bearer key.
"""

from __future__ import annotations

import pytest

from cloudbot.core.scrub import MAX_ERROR_CHARS, cap, scrub, scrub_error

DSN = "postgresql://user:npg_FAKE-test-only@ep-x-pooler.eu.aws.neon.tech/cloudbot?sslmode=require"
TOKEN = "7981234567:AAFakeTokenValueForTheScrubTestOnly_do_not_use"
ENGINE_KEY = "engine-key-fake-value-0123456789abcdef"
SECRETS = (DSN, TOKEN, ENGINE_KEY, "npg_FAKE-test-only")

def test_a_dsn_shaped_string_never_survives():
    out = scrub(f"could not connect to {DSN}")
    assert "npg_FAKE-test-only" not in out
    assert "user" not in out
    assert "[redacted]" in out
    # The reason is still readable.
    assert "could not connect" in out

def test_a_bot_token_and_bearer_key_never_survive():
    out = scrub(f"HTTP 401 for https://api.telegram.org/bot{TOKEN}/sendDocument")
    assert TOKEN not in out
    assert "AAFakeTokenValue" not in out
    assert scrub(f"Authorization: Bearer {ENGINE_KEY}") == "Authorization: Bearer [redacted]"

def test_exact_secret_values_are_removed_even_without_a_recognisable_shape():
    # A value the regexes would not otherwise catch (the live engine key).
    assert ENGINE_KEY not in scrub(f"upstream rejected key {ENGINE_KEY}", secrets=SECRETS)

def test_the_real_exception_is_kept_and_named():
    try:
        raise ConnectionError(f"connection to {DSN} failed: timeout expired")
    except ConnectionError as exc:
        message = scrub_error(exc, secrets=SECRETS)
    assert message.startswith("ConnectionError: ")
    assert "timeout expired" in message
    assert "npg_FAKE-test-only" not in message
    assert len(message) <= MAX_ERROR_CHARS

def test_a_huge_message_is_capped():
    message = scrub_error(RuntimeError("x" * 5000))
    assert len(message) == MAX_ERROR_CHARS
    assert message.endswith("…")

def test_scrub_is_total():
    assert scrub(None) == ""
    assert scrub("") == ""
    assert cap("abcdef", 4) == "abc…"
    assert cap("abcdef", 0) == ""

@pytest.mark.parametrize(
    "text",
    [
        "postgres://u:p@h/db",
        "postgresql+asyncpg://u:p@h/db",
        "password=hunter2 host=x",
    ],
)
def test_other_dsn_spellings_are_covered(text):
    assert "p@h" not in scrub(text) or "[redacted]" in scrub(text)
    assert "hunter2" not in scrub(text)
