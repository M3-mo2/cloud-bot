"""Callback-data codec: compact, content-addressed tokens within 64 bytes.

Telegram caps ``callback_data`` at 64 bytes. ``ws_``+26-char ULID ≈ 29 bytes, and
a naive ``run:logs:<ws>:<run>:stdout:0`` overflows. Callbacks therefore carry a
compact action plus a short **content-addressed token** that a registry maps back
to the full key parts (CB-8). Because the token is a hash of the key parts, the
same screen always produces the same callback — deterministic and snapshot-safe.

The codec asserts the byte length at build time: raises in DEV/DEBUG, logs +
safely truncates in PRODUCTION.
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


class CallbackTooLong(DomainError):
    code = "callback_too_long"


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


class CallbackCodec:
    def __init__(self, *, mode: RunMode = RunMode.PRODUCTION, max_bytes: int = MAX_CALLBACK_BYTES) -> None:
        self._mode = mode
        self._max = max_bytes
        self._to_key: dict[str, tuple[Any, ...]] = {}

    def encode(self, action: str, *key_parts: Any) -> str:
        if not key_parts:
            data = action
        else:
            token = _token_for_key("|", key_parts)
            self._to_key[token] = tuple(key_parts)
            data = f"{action}:{token}"
        size = len(data.encode("utf-8"))
        if size > self._max:
            if self._mode in (RunMode.DEV, RunMode.DEBUG):
                raise CallbackTooLong(f"{data!r} is {size} bytes (> {self._max})")
            safe = data[: self._max]
            log.warning("callback.truncated", original=data, kept=safe)
            return safe
        return data

    def decode(self, data: str) -> DecodedCallback:
        head, _, tail = data.rpartition(":")
        if head and tail in self._to_key:
            return DecodedCallback(action=head, key=self._to_key[tail])
        return DecodedCallback(action=data, key=())

    def register(self, action: str, key_parts: tuple[Any, ...]) -> str:
        """Prime the registry for a token (used when only the token is known)."""
        token = _token_for_key("|", key_parts)
        self._to_key.setdefault(token, tuple(key_parts))
        return f"{action}:{token}"

    def reset(self) -> None:
        self._to_key.clear()


#: Process-wide codec shared by screens and handlers.
codec = CallbackCodec(mode=RunMode.PRODUCTION)


def cb(action: str, *key_parts: Any) -> str:
    return codec.encode(action, *key_parts)


def decode(data: str) -> DecodedCallback:
    return codec.decode(data)
