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
# Phase 2d/2e/2g proofs:
#  10. A figure whose *_complete is false is never rendered as an exact number.
#  11. The process stop outcome is honest (stopped vs gone; a repeat 404 → gone).
#  12. A dry run writes nothing, with a real-extract positive control.
#  13. RAR is never offered as a create/export target.
#  14. Every archive refusal code has its own sentence in both catalogs.
#  15. A bite test that changes behaviour (metrics honesty guard), restored.
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

echo "== 10. a figure whose *_complete is false is never an exact number =="
"$PY" - <<'PY'
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceMetrics
from cloudbot.presentation.screens import metrics as met

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


def frame(*, disk_complete, disk_percent):
    return WorkspaceMetrics(
        workspace_id=str(WS), timestamp="t", interval_seconds=3,
        ram_used_bytes=0, ram_limit_bytes=512 * 1024 * 1024, ram_percent=0.0,
        ram_complete=True, disk_used_bytes=0, disk_limit_bytes=1024 * 1024 * 1024,
        disk_percent=disk_percent, disk_complete=disk_complete,
        write_rate_limit_mb_s=0, live_runs=0, live_cgroups=0,
    )


incomplete = met.metrics_screen(frame(disk_complete=False, disk_percent=99.0), WS, Locale.EN).doc.html
assert "99" not in incomplete, "an incomplete figure rendered as an exact percentage"
assert "provisional" in incomplete.lower(), "the incomplete figure was not labelled"
complete = met.metrics_screen(frame(disk_complete=True, disk_percent=99.0), WS, Locale.EN).doc.html
assert "99%" in complete, "a complete figure did not render its percentage"
print("OK: disk_complete:false renders provisional (no exact %); complete renders the number")
PY

echo "== 11. the stop outcome is honest (stopped vs gone; repeat 404 → gone) =="
"$PY" - <<'PY'
import asyncio
from cloudbot.core.errors import EngineRefused
from cloudbot.core.locale import Locale
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.domain.value_objects import ProcessStopResult, WorkspaceId
from cloudbot.presentation.screens import processes as proc
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.processes import ProcessesService

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")

stopped_html = proc.stop_result_screen(
    ProcessStopResult(process_id="proc_x", status="stopped", stopped=True), WS, Locale.EN).doc.html
gone_html = proc.stop_result_screen(
    ProcessStopResult(process_id="proc_x", status="gone", stopped=False), WS, Locale.EN).doc.html
assert stopped_html != gone_html, "stopped and gone render identically"
assert "already" in gone_html.lower(), "gone is not explained as already gone"

# A repeat stop is a 404 in the engine; the service folds it to 'gone'.
class Users:
    async def owns(self, tg_id, ws_id):
        return True

engine = FakeEngine()

async def _not_found(ws_id, process_id):
    raise EngineRefused("not_found")

engine.stop_process = _not_found
svc = ProcessesService(engine, OwnershipService(Users()))
result = asyncio.run(svc.stop(1, WS, "proc_0123456789abcdef"))
assert result.status == "gone" and result.gone, "a repeat 404 did not read as already gone"
print("OK: stopped/gone are distinct; a repeat stop's 404 reads as already gone")
PY

echo "== 12. the dry run writes nothing (with a real-extract positive control) =="
"$PY" - <<'PY'
import asyncio
from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.domain.value_objects import WorkspaceId

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def main():
    engine = FakeEngine()
    before = (await engine.list_files(WS, "/out")).entries
    dry = await engine.extract_archive(WS, path="/archive.zip", dest="/out", dry_run=True)
    assert dry.bytes_written == 0, "a dry run reported bytes written"
    mid = (await engine.list_files(WS, "/out")).entries
    # Positive control: the same archive extracted for real DOES add files.
    real = await engine.extract_archive(WS, path="/archive.zip", dest="/out", dry_run=False)
    after = (await engine.list_files(WS, "/out")).entries
    assert not before and not mid, "a dry run wrote files"
    assert after, "the real extract wrote nothing (the control failed)"
    assert real.bytes_written > 0
    print("OK: dry run wrote nothing; the real extract added files (control)")


asyncio.run(main())
PY

echo "== 13. RAR is never offered as a create/export target =="
"$PY" - <<'PY'
import pathlib, sys
bad = []
for p in pathlib.Path("cloudbot").rglob("*.py"):
    text = p.read_text(encoding="utf-8").lower()
    for token in ("format=rar", 'format="rar"', "rar_encoder", "create_rar", "encode_rar", "writestr.*rar"):
        if token in text:
            bad.append(f"{p}: {token}")
from cloudbot.services.archives import ArchivesService
if hasattr(ArchivesService, "create") or hasattr(ArchivesService, "encode"):
    bad.append("ArchivesService exposes a create/encode method")
if bad:
    print("FAIL: rar is offered as a create target:", *bad, sep="\n  ")
    sys.exit(1)
print("OK: no code path offers rar as a create/export format; export is ZIP only")
PY

echo "== 14. every archive refusal code has its own sentence in both catalogs =="
"$PY" - <<'PY'
from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog

CODES = [
    "archive_entry_exists", "archive_too_large", "archive_too_many_entries",
    "archive_ratio_exceeded", "payload_too_large", "unsupported_archive",
    "archive_encrypted", "archive_multivolume", "archive_unsafe_entry",
    "unprocessable", "insufficient_storage",
]
missing = []
for code in CODES:
    for loc in (Locale.EN, Locale.AR):
        if not catalog.has(f"err.{code}", loc):
            missing.append(f"{code}/{loc.value}")
if missing:
    print("FAIL: archive refusal codes without a sentence:", *missing, sep="\n  ")
    import sys; sys.exit(1)
# And the per-entry refusal mapping resolves every code to a real sentence.
from cloudbot.presentation.screens.archives import _reason_sentence
for code in CODES:
    for loc in (Locale.EN, Locale.AR):
        sentence = _reason_sentence(code, loc)
        assert sentence and sentence != f"err.{code}", f"{code}/{loc.value}"
print("OK: all 11 archive refusal codes have distinct sentences in ar and en")
PY

echo "== 15. bite test: the incomplete-metric guard is load-bearing =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

target = pathlib.Path("cloudbot/presentation/screens/metrics.py")
original = target.read_text(encoding="utf-8")
guard = "    if not complete:\n"
bitten = original.replace(guard, "    if not complete and False:  # BITE\n", 1)
assert bitten != original, "bite substitution did not apply (guard moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_phase2deg.py",
       "-k", "incomplete", "--no-header", "-p", "no:cacheprovider"]
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if red.returncode == 0:
    print("FAIL: the bite did not make the test red (it is not evidence)")
    print(red.stdout[-800:])
    sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the guard did not return the test to green")
    print(green.stdout[-800:])
    sys.exit(1)
red_line = [l for l in red.stdout.splitlines() if "passed" in l or "failed" in l][-1:]
green_line = [l for l in green.stdout.splitlines() if "passed" in l or "failed" in l][-1:]
print("OK: bite ->", (red_line or ["red"])[0].strip(), "| restored ->", (green_line or ["green"])[0].strip())
PY

echo
echo "all fifteen proofs passed"
echo
echo "== 16-23. the admin panel (Phase 3) =="
echo "-- 16. the gate is identity-based, not mode-based --"
"$PY" -m pytest -q tests/unit/test_admin_gate.py -k "production or reaches"
echo "-- 17. a non-admin cannot detect the panel --"
"$PY" -m pytest -q tests/unit/test_admin_gate.py -k "swallow or indistinguish"
echo "-- 18. the menu is registry-driven --"
"$PY" -m pytest -q tests/unit/test_admin_registry.py
echo "-- 19. the migration applies to an existing database --"
"$PY" -m pytest -q tests/contract/test_admin_migration.py
echo "-- 20. the notifier never loses or duplicates an event across a restart --"
"$PY" -m pytest -q tests/unit/test_admin_notifier.py -k "restart or coalesce or cursor or clock or gap or paused or unsubscribed"
echo "-- 21. the volume cap is honest (one message, real count) --"
"$PY" -m pytest -q tests/unit/test_admin_notifier.py -k "cap or capped or dispatcher"
echo "-- 22. an entered-mode action is attributed to the admin, not the user --"
"$PY" -m pytest -q tests/unit/test_admin_events.py -k "entered"
echo "-- 23. bite test: the gate is load-bearing (make it mode-based, red; restore, green) --"
"$PY" - <<'PY'
import pathlib, subprocess, sys

target = pathlib.Path("cloudbot/middleware/admin_gate.py")
original = target.read_text(encoding="utf-8")
guard = "        if ctx is None or not ctx.is_admin:\n"
bitten = original.replace(guard, "        if False:  # BITE: gate disabled (mode-based identity removed)\n", 1)
assert bitten != original, "bite substitution did not apply (gate moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_admin_gate.py",
       "-k", "swallow", "--no-header", "-p", "no:cacheprovider"]
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if red.returncode == 0:
    print("FAIL: the bite did not make the gate test red (it is not evidence)")
    print(red.stdout[-800:])
    sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the gate did not return the test to green")
    print(green.stdout[-800:])
    sys.exit(1)
red_line = [l for l in red.stdout.splitlines() if "passed" in l or "failed" in l][-1:]
green_line = [l for l in green.stdout.splitlines() if "passed" in l or "failed" in l][-1:]
print("OK: bite ->", (red_line or ["red"])[0].strip(), "| restored ->", (green_line or ["green"])[0].strip())
PY

echo
echo "all twenty-three proofs passed"
