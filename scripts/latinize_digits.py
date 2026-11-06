#!/usr/bin/env python3
"""Latinize Arabic-Indic numerals inside string literals.

TASK digits: the product rule is that **every digit in the rendered UI is ASCII
``0-9`` in every locale**, even Arabic. Arabic copy and Arabic word order stay
untouched; only numerals move.

This script rewrites, *inside string literals only*:

* ``٠-٩`` (U+0660–U+0669)      → ``0-9``
* ``۰-۹`` (U+06F0–U+06F9)      → ``0-9``
* ``٪``   (U+066A Arabic percent) → ``%``
* ``٫``   (U+066B Arabic decimal separator) → ``.``

It never touches identifiers, keys, comments or code: Python's own tokenizer
decides what a string literal is. It is **idempotent** — a second run changes
nothing — and it **reports the number of characters changed per file**.

Usage::

    python scripts/latinize_digits.py                 # the catalogs
    python scripts/latinize_digits.py path/to/file.py # specific files/dirs
    python scripts/latinize_digits.py --check         # exit 1 if work is needed
"""

from __future__ import annotations

import argparse
import io
import sys
import tokenize
from pathlib import Path

DIGITS = {chr(0x0660 + i): str(i) for i in range(10)}
DIGITS.update({chr(0x06F0 + i): str(i) for i in range(10)})
PUNCT = {chr(0x066A): "%", chr(0x066B): "."}
TRANSLATION = {**DIGITS, **PUNCT}

#: Tokens whose payload is literal text the user could see.
_TEXT_TOKENS = {
    tokenize.STRING,
    getattr(tokenize, "FSTRING_MIDDLE", -1),
}

DEFAULT_TARGETS = (Path(__file__).resolve().parent.parent / "cloudbot/presentation/i18n/catalogs",)


def _offsets(source: str) -> list[int]:
    """Absolute offset of the start of every 1-based line."""
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _collect(path: Path) -> tuple[str, list[tuple[int, int, str]], dict[str, int]]:
    """Return (source, replacements, per-kind counts) for one file."""
    source = path.read_text(encoding="utf-8")
    starts = _offsets(source)
    replacements: list[tuple[int, int, str]] = []
    counts = {"digits": 0, "punctuation": 0}
    readline = io.StringIO(source).readline
    for token in tokenize.generate_tokens(readline):
        if token.type not in _TEXT_TOKENS:
            continue
        text = token.string
        if not any(ch in TRANSLATION for ch in text):
            continue
        new = "".join(TRANSLATION.get(ch, ch) for ch in text)
        counts["digits"] += sum(1 for ch in text if ch in DIGITS)
        counts["punctuation"] += sum(1 for ch in text if ch in PUNCT)
        start = starts[token.start[0] - 1] + token.start[1]
        end = starts[token.end[0] - 1] + token.end[1]
        replacements.append((start, end, new))
    return source, replacements, counts


def rewrite(path: Path) -> tuple[int, int]:
    """Rewrite one file in place; return (digits changed, punctuation changed)."""
    source, replacements, counts = _collect(path)
    if not replacements:
        return 0, 0
    rebuilt = list(source)
    # Right-to-left so earlier offsets stay valid.
    for start, end, new in reversed(replacements):
        rebuilt[start:end] = new
    new_source = "".join(rebuilt)
    if new_source != source:
        path.write_text(new_source, encoding="utf-8")
    return counts["digits"], counts["punctuation"]


def targets(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="change nothing; exit 1 when any file would change",
    )
    args = parser.parse_args(argv)

    paths = targets(args.paths or list(DEFAULT_TARGETS))
    total_digits = total_punct = 0
    pending = False
    for path in paths:
        if args.check:
            _source, replacements, counts = _collect(path)
            changed = bool(replacements)
            pending = pending or changed
            digits, punct = counts["digits"], counts["punctuation"]
        else:
            digits, punct = rewrite(path)
            changed = bool(digits or punct)
        if changed:
            print(f"{path}: digits={digits} punctuation={punct}")
        total_digits += digits
        total_punct += punct

    print(
        f"total: digits={total_digits} punctuation={total_punct} "
        f"files={len(paths)}"
    )
    if args.check and pending:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
