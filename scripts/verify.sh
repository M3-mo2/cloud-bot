#!/usr/bin/env bash
# verify.sh — prove the claims that matter most.
#
#   1. The rich-only rule is enforced (no send_message(parse_mode=...)).
#   2. Raw engine output can never reach a user.
#   3. Ownership isolation is real (a foreign user is refused like a missing id).
#   4. The terminal uses the engine's native sessions API — never a FIFO,
#      mkfifo or a run-based pseudo-terminal (INTEGRATION-GUIDE §5.4).
#   5. The offset protocol is followed: output is read by offset/next and a read
#      after close surfaces `session_ended`.
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
"$PY" -m pytest -q tests/unit/test_vocabulary.py tests/unit/test_escaping.py tests/unit/test_terminal_mapping.py

echo "== 3. ownership isolation is real =="
"$PY" -m pytest -q tests/unit/test_ownership.py

echo "== 4. terminal uses the engine's native sessions API (no FIFO / run PTY) =="
"$PY" - <<'PY'
import pathlib, sys
# The terminal path (service + handler) must not build a pseudo-terminal.
path_files = [
    pathlib.Path("cloudbot/services/sessions.py"),
    pathlib.Path("cloudbot/handlers/terminal.py"),
]
forbidden = ("mkfifo", "os.mkfifo", "openpty", "pty.spawn", "subprocess", "create_run(")
bad = []
for p in path_files:
    t = p.read_text(encoding="utf-8")
    for token in forbidden:
        if token in t:
            bad.append(f"{p}: {token}")
engine = pathlib.Path("cloudbot/adapters/cloud_service/engine.py").read_text(encoding="utf-8")
for required in ("/sessions", "open_session", "session_output", "session_signal", "session_resize"):
    if required not in engine:
        bad.append(f"engine.py missing {required!r}")
if bad:
    print("FAIL: terminal is not using the native sessions API:", *bad, sep="\n  ")
    sys.exit(1)
print("OK: terminal path calls the engine sessions endpoints; no FIFO / run PTY")
PY

echo "== 5. offset protocol + session_ended after close =="
"$PY" -m pytest -q tests/unit/test_sessions.py tests/contract/test_engine_adapter.py -k "session or offset or fifo or terminal"

echo
echo "all five proofs passed"
