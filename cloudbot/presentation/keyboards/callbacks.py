"""Callback-data codec: inline keys when they fit, a token only when they cannot.

Telegram caps ``callback_data`` at 64 bytes (``validate_callback`` enforces it).
The earlier design routed **every** key-bearing callback through a compact
content-addressed token held in a **process-global, in-memory** registry (CB-8).
That made a restart fatal: a button drawn by the previous process tapped after a
restart decoded to ``key=()`` and the handler's ``decode(cb.data).key[0]`` raised
``IndexError`` (F-23.1). The registry died with the process, so ``ws:``, ``file:``,
``run:``, ``port:``, ``nav:`` — every prefix — could die with it.

**The restart rule.** A callback must survive a process restart. Key parts are
therefore encoded **inline** whenever they fit the 64-byte cap (that is the whole
point of having a cap — most callbacks are a few dozen bytes):

    action           ->  ``action``                       (no key parts)
    action, (a, b)   ->  ``action:a:b``                   (inline, restart-proof)

Every action is exactly two colon-separated segments (``ws:view``, ``file:ls``,
``term:signal`` …), so ``decode`` splits the action off the first two segments and
reads whatever key parts follow. It needs no registry and no memory of the
process that drew the button.

Only a callback whose inline form genuinely exceeds the cap (a terminal callback
carrying a workspace **and** a session id, ``run:logs:<ws>:<run>``, a file path)
falls back to the **token registry**: ``action:~token``. The ``~`` marker makes a
token unmistakably different from an inline key part, so ``decode`` can tell them
apart. When the token is unknown — which is exactly what a restart produces —
``decode`` returns ``key=()``, and ``decode_required`` raises :class:`StaleCallback`
so the handler renders a correctable screen instead of indexing an empty tuple.

**Totality.** ``decode`` never raises: for any input it returns an ``action`` and
a ``key`` tuple, empty when the key is unknown. A handler that needs key parts
calls :func:`decode_required`, which fails loudly (as a screen) rather than
silently.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import structlog

from cloudbot.core.errors import DomainError
from cloudbot.core.modes import RunMode

log = structlog.get_logger("cloudbot.callbacks")

MAX_CALLBACK_BYTES = 64
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

#: Marks a registry token in a callback, distinguishing it from an inline key part.
#: A key part never begins with this character (ids are ``ws_…``/``ses_…``/``run_…``,
#: paths begin with ``/``, numbers/profiles are alphanumeric).
TOKEN_MARKER = "~"

#: The number of colon-separated segments that make up an action (``prefix:name``).
_ACTION_SEGMENTS = 2


class CallbackTooLong(DomainError):
    code = "callback_too_long"


class StaleCallback(DomainError):
    """A key-bearing callback whose key parts cannot be resolved in this process.

    Raised by :func:`decode_required` when an inline key is missing (malformed) or
    a registry token is unknown — the signature of a button drawn before a restart.
    A handler never sees a bare ``key=()``; the error guard renders the stale
    screen (``err.stale.*``).
    """

    code = "stale_callback"

    def __init__(self, data: str = "") -> None:
        super().__init__(data)
        self.data = data


@dataclass(frozen=True)
class DecodedCallback:
    action: str
    key: tuple[Any, ...] = ()


def _token_for_key(separator: str, parts: tuple[Any, ...]) -> str:
    raw = separator.join(str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).digest()
    number = int.from_bytes(digest[:5], "big")  # 40 bits
    out = ""
    while number:
        number, rem = divmod(number, len(_ALPHABET))
        out = _ALPHABET[rem] + out
    return out or "0"


def _action_of(data: str) -> str:
    """The action of ``data`` (``prefix:name``), or ``data`` when it has no colon."""
    head, sep, tail = data.partition(":")
    if not sep:
        return data
    name = tail.split(":", 1)[0]
    return f"{head}:{name}"


class CallbackCodec:
    def __init__(self, *, mode: RunMode = RunMode.PRODUCTION, max_bytes: int = MAX_CALLBACK_BYTES) -> None:
        self._mode = mode
        self._max = max_bytes
        self._to_key: dict[str, tuple[Any, ...]] = {}

    def encode(self, action: str, *key_parts: Any) -> str:
        if not key_parts:
            data = action
        else:
            parts = [str(p) for p in key_parts]
            # Inline is safe only when no part contains the segment separator or the
            # token marker: a path like ``/a:b`` or an id starting with ``~`` would
            # otherwise split back into the wrong key parts.
            inline_safe = all(":" not in p and not p.startswith(TOKEN_MARKER) for p in parts)
            inline = action + ":" + ":".join(parts)
            if inline_safe and len(inline.encode("utf-8")) <= self._max:
                # The common case: the key parts travel inline and survive a restart.
                data = inline
            else:
                # Genuinely too long (or an unsafe-to-split part): fall back to the
                # in-memory registry. The token is short and the handler is written
                # to survive its loss.
                token = _token_for_key("|", key_parts)
                self._to_key[token] = tuple(key_parts)
                data = f"{action}:{TOKEN_MARKER}{token}"
        size = len(data.encode("utf-8"))
        if size > self._max:
            if self._mode in (RunMode.DEV, RunMode.DEBUG):
                raise CallbackTooLong(f"{data!r} is {size} bytes (> {self._max})")
            safe = data[: self._max]
            log.warning("callback.truncated", original=data, kept=safe)
            return safe
        return data

    def decode(self, data: str) -> DecodedCallback:
        """Total: never raises, always returns an action (and a key tuple or ``()``)."""
        if not isinstance(data, str) or not data:
            return DecodedCallback(action=str(data), key=())
        action = _action_of(data)
        segments = data.split(":")
        rest = segments[_ACTION_SEGMENTS:] if len(segments) > _ACTION_SEGMENTS else []
        if len(rest) == 1 and rest[0].startswith(TOKEN_MARKER):
            token = rest[0][len(TOKEN_MARKER) :]
            if token in self._to_key:
                return DecodedCallback(action=action, key=self._to_key[token])
            # The registry died with the previous process (or the token is wrong):
            # the key parts are gone. The handler will render the stale screen.
            return DecodedCallback(action=action, key=())
        return DecodedCallback(action=action, key=tuple(rest))

    def register(self, action: str, key_parts: tuple[Any, ...]) -> str:
        """Prime the registry for a token (used when only the token is known)."""
        token = _token_for_key("|", key_parts)
        self._to_key.setdefault(token, tuple(key_parts))
        return f"{action}:{TOKEN_MARKER}{token}"

    def reset(self) -> None:
        self._to_key.clear()


#: Process-wide codec shared by screens and handlers.
codec = CallbackCodec(mode=RunMode.PRODUCTION)


def cb(action: str, *key_parts: Any) -> str:
    return codec.encode(action, *key_parts)


def decode(data: str) -> DecodedCallback:
    return codec.decode(data)


def decode_required(data: str, arity: int = 1) -> DecodedCallback:
    """Decode a callback that the caller **needs** key parts from.

    Raises :class:`StaleCallback` when fewer than ``arity`` key parts are present
    (a stale token after a restart, or a malformed callback). The error guard maps
    that to the correctable stale screen — a handler never indexes an empty tuple.
    """
    decoded = codec.decode(data)
    if len(decoded.key) < arity:
        raise StaleCallback(data)
    return decoded
