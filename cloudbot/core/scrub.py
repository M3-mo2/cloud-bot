"""``scrub`` — the one function that removes secrets from a message.

A failed backup stores its error in ``backup_config.last_error``, which the
Telegram admin screen and the panel both render. That string must never carry a
credential, so every error goes through this helper before it is stored, logged
or returned.

This mirrors the panel's ``safeMessage`` / ``redact`` pair
(``cloudbot-admin/lib/backup/service.ts`` + ``lib/backup/pg.ts``):

* the DSN exactly, its password, and any ``postgres://…`` / ``postgresql://…``
  URL shape become ``[redacted]``;
* a Telegram bot token (``<digits>:<token>``) becomes ``bot[redacted]``;
* a ``Bearer`` credential becomes ``Bearer [redacted]``;
* anything passed in ``secrets`` (the live token/key values) is replaced too.

It is deliberately total: a ``None`` or an empty message yields ``""`` and it
never raises, so a failure path cannot fail while it is trying to explain
itself.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

#: ``postgres://`` and ``postgresql://`` URLs, up to whitespace or a quote.
_DSN = re.compile(r"postgres(?:ql)?(?:\+asyncpg)?://[^\s\"']+", re.IGNORECASE)
#: ``password=…`` inside a libpq keyword string.
_PASSWORD_KV = re.compile(r"(password=)[^\s&\"']+", re.IGNORECASE)
#: A Telegram bot token: ``<bot id>:<secret>``. The bot id is digits; the secret
#: is the URL-safe token alphabet Telegram issues.
_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)
#: A bare ``<digits>:<token>`` without the ``bot`` prefix (what ``str(exc)`` may
#: hold when an HTTP client echoes the URL).
_BARE_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
#: A bearer credential.
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

def scrub(text: object, *, secrets: "tuple[str, ...] | list[str]" = ()) -> str:
    """Return ``text`` with every known secret shape replaced by a placeholder.

    ``secrets`` is the authoritative list: a caller passes the live values it
    holds (the bot token, the engine key, the DSN) so even a shape the regexes
    do not recognise is removed by exact match.
    """
    if text is None:
        return ""
    out = text if isinstance(text, str) else str(text)
    if not out:
        return ""
    for secret in secrets:
        if not secret:
            continue
        out = out.replace(str(secret), REDACTED)
        # A URL component may have been percent-encoded before it reached here.
        encoded = re.sub(r"[^A-Za-z0-9._~-]", lambda m: f"%{ord(m.group(0)):02X}", str(secret))
        if encoded != secret:
            out = out.replace(encoded, REDACTED)
    out = _DSN.sub(REDACTED, out)
    out = _PASSWORD_KV.sub(rf"\1{REDACTED}", out)
    out = _BOT_TOKEN.sub(f"bot{REDACTED}", out)
    out = _BARE_TOKEN.sub(REDACTED, out)
    out = _BEARER.sub(rf"\1{REDACTED}", out)
    return out

def scrub_error(exc: BaseException, *, secrets: "tuple[str, ...] | list[str]" = ()) -> str:
    """``"{type}: {message}"`` scrubbed and capped — what ``last_error`` holds."""
    return cap(scrub(f"{type(exc).__name__}: {exc}", secrets=secrets), MAX_ERROR_CHARS)

#: A stored ``last_error`` is capped so a giant message cannot bloat the row.
MAX_ERROR_CHARS = 300

def cap(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Truncate to ``limit`` characters, with an explicit ellipsis."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"
