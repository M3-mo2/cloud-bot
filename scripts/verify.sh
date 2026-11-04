#!/usr/bin/env bash
# verify.sh — prove the three claims that matter most.
#
#   1. The rich-only rule is enforced (no send_message(parse_mode=...)).
#   2. Raw engine output can never reach a user.
#   3. Ownership isolation is real (a foreign user is refused like a missing id).
#
# Runs offline; no token, no network.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/zerops/cloud-bot-venv/bin/python}"

echo "== 1. rich-only rule =="
"$PY" - <<'PY'
import pathlib, sys
root = pathlib.Path("cloudbot")
bad = []
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8")
    if "send_message(" in t and "parse_mode" in t:
        bad.append(str(p))
if bad:
    print("FAIL: send_message(parse_mode=...) found in:", *bad, sep="\n  ")
    sys.exit(1)
print("OK: no module calls send_message(parse_mode=...)")
PY

echo "== 2. raw engine output cannot reach a user =="
"$PY" -m pytest -q tests/unit/test_vocabulary.py tests/unit/test_escaping.py

echo "== 3. ownership isolation is real =="
"$PY" -m pytest -q tests/unit/test_ownership.py

echo
echo "all three proofs passed"
