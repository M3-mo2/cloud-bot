"""Telegram Stars billing — the pack catalogue and the invoice payload.

Everything here is pure data and pure functions; the store persists it and the
handlers speak Telegram. Two rules are load-bearing:

* a payload names **the user and the pack**, never an amount. The amount is
  always read from :data:`STAR_PACKS`, so a forged payload cannot change the
  price of what is delivered.
* a Stars pack is a 1:1 top-up: its Telegram price equals the Stars it credits.
  The catalogue is still the only place that number is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Telegram's currency tag for digital goods paid in Stars.
STARS_CURRENCY = "XTR"

@dataclass(frozen=True)
class StarPack:
    code: str
    stars: int

    @property
    def title_key(self) -> str:
        return "wallet.pack.title"

#: Display order. Prices are in Stars (``XTR``) and equal the credited amount.
STAR_PACKS: dict[str, StarPack] = {
    "stars_50": StarPack(code="stars_50", stars=50),
    "stars_150": StarPack(code="stars_150", stars=150),
    "stars_500": StarPack(code="stars_500", stars=500),
    "stars_1200": StarPack(code="stars_1200", stars=1200),
}

PAYLOAD_PREFIX = "stars"

def pack(code: str) -> StarPack | None:
    return STAR_PACKS.get(code)

def packs() -> tuple[StarPack, ...]:
    return tuple(STAR_PACKS.values())

def encode_payload(tg_id: int, pack_code: str) -> str:
    """The invoice payload: the buyer and the pack, never a price."""
    return f"{PAYLOAD_PREFIX}:{int(tg_id)}:{pack_code}"

def decode_payload(payload: str) -> tuple[int, StarPack] | None:
    """Parse a payload we produced, or ``None`` for anything else.

    Total: a forged string, a missing part, a non-integer id or an unknown pack
    is ``None`` — never an exception and never a guessed price.
    """
    parts = (payload or "").split(":")
    if len(parts) != 3 or parts[0] != PAYLOAD_PREFIX:
        return None
    try:
        tg_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    found = pack(parts[2])
    if found is None:
        return None
    return tg_id, found

@dataclass(frozen=True)
class StarsPayment:
    """A delivered Stars payment. ``charge_id`` is what Telegram refunds need."""

    tg_id: int
    pack_code: str
    stars: int
    charge_id: str
    provider_charge_id: str
    created_at: datetime
    refunded_at: datetime | None = None

    @property
    def refundable(self) -> bool:
        return self.refunded_at is None

__all__ = [
    "PAYLOAD_PREFIX",
    "STARS_CURRENCY",
    "STAR_PACKS",
    "StarPack",
    "StarsPayment",
    "decode_payload",
    "encode_payload",
    "pack",
    "packs",
]
