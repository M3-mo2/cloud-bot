"""Short, list-unique labels for ULID-based ids.

A fixed prefix is not enough: ULIDs created in the same millisecond share their
first ~10 characters, so ``ses_01M6Q5WQ…`` can name two different terminals.
The label is extended only until it is distinct in the list being shown, and an
occurrence number keeps the label honest when the same id appears twice. The
callback always carries the full id.
"""

from __future__ import annotations

from typing import Sequence


def unique_short_ids(ids: Sequence[str], *, width: int = 12) -> list[str]:
    """Short labels that are unique among ``ids`` (order preserved)."""
    labels: list[str] = []
    used: dict[str, int] = {}
    for item in ids:
        length = min(width, len(item))
        while length < len(item):
            prefix = item[:length]
            if sum(1 for other in ids if other[:length] == prefix) <= 1:
                break
            length += 1
        base = item if length >= len(item) else item[:length] + "…"
        used[base] = used.get(base, 0) + 1
        labels.append(base if used[base] == 1 else f"{base} #{used[base]}")
    return labels


__all__ = ["unique_short_ids"]
