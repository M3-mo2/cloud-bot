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

echo "== 6. the install result is honest (non-zero exit_code ⇒ failure screen) =="
"$PY" - <<'PY'
import sys
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import InstallResult
from cloudbot.services.packages import InstallOutcome
from cloudbot.presentation.screens import packages as pkg

# A 200 response whose payload carries exit_code 1 must render as a FAILURE.
failed = InstallOutcome(
    result=InstallResult(exit_code=1, output="pip: error", stdout="", duration_ms=100,
                         timed_out=False, truncated=False, target="/opt/python/private"),
    allow_net=False,
)
screen = pkg.install_result_screen(failed, "six", "ws_x", Locale.EN)
html = screen.doc.html
assert "failed" in html.lower() or "❌" in html, "failed install did not render as failure"
assert "no internet" in html.lower(), "allow_net explanation missing for the no-net failure"
# And a zero exit_code renders as success.
ok = InstallOutcome(
    result=InstallResult(exit_code=0, output="ok", stdout="ok", duration_ms=100,
                         timed_out=False, truncated=False, target="/opt/python/private"),
    allow_net=True,
)
assert "installed" in pkg.install_result_screen(ok, "six", "ws_x", Locale.EN).doc.html.lower()
print("OK: exit_code drives the result screen; the no-net failure is explained in words")
PY

echo "== 7. the tunnel URL is never cached (LINK button on the live screen only) =="
"$PY" - <<'PY'
import pathlib, sys
# (a) Nothing in persistence stores a tunnel or a url.
persistence = list(pathlib.Path("cloudbot/adapters/persistence").rglob("*.py"))
persistence += list(pathlib.Path("cloudbot/adapters/persistence/migrations").glob("*.sql"))
joined = "\n".join(p.read_text(encoding="utf-8") for p in persistence)
assert "tunnel" not in joined.lower(), "persistence layer mentions tunnels (it must not store them)"
# (b) The tunnel screen carries the URL only as a LINK button, never as text.
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Tunnel, TunnelList, WorkspaceId
from cloudbot.presentation.screens import tunnels as tnl

URL = "https://secret-abc.trycloudflare.com"
tl = TunnelList(tunnels=(Tunnel(id="tnl_x", workspace_id="ws_x", port=8080, url=URL,
                               status="active"),))
screen = tnl.tunnels_screen(tl, WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4"), Locale.EN)
assert URL not in screen.doc.html, "the ephemeral URL leaked into the stored screen text"
links = [b for row in screen.markup.inline_keyboard for b in row if getattr(b, "url", None)]
assert any(b.url == URL and b.style == "link" for b in links), "the URL is not a LINK button"
print("OK: URL only travels as a LINK button; never persisted, never rendered as text")
PY

echo "== 8. the three tunnel gates are distinguished =="
"$PY" -m pytest -q tests/unit/test_features.py -k "gate or three"

echo "== 9. the reachability sentence is present verbatim as a blockquote =="
"$PY" - <<'PY'
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import PortReport, WorkspaceId
from cloudbot.presentation.screens import ports as port_screens

SENTENCE = ("ports are listening inside the private network namespace; "
            "this does not mean they are reachable from outside.")
report = PortReport(workspace_id="ws_x", ports=(), unix=(), count=0, unix_count=0,
                    complete=True, notes=(), pids_scanned=0, fds_scanned=0,
                    reachability=SENTENCE)
html = port_screens.ports_screen(report, WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4"), Locale.EN).doc.html
assert f"<blockquote>{SENTENCE}</blockquote>" in html, "reachability is not a verbatim blockquote"
print("OK: the reachability sentence is surfaced verbatim inside <blockquote>")
PY

echo
echo "all nine proofs passed"
