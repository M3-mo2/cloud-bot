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
#  22. A bite test that puts a self-referential nav entry back (R1), restored.
#  23. A bite test that makes too_many_tunnels silently redraw again, restored.
#  24. A bite test that restores the old state.clear() after a terminal command,
#      so sticky input breaks again, restored.
#  25. A bite test that drops the error guard's Update unwrap so refusals render
#      nothing again (F-01), restored.
#  26. A bite test that hides an unattributed listening port from the picker
#      (F-06), restored.
#  27. A bite test that puts a style Telegram rejects back on a URL button,
#      restored.
#  28. A bite test that drops the `await` on Bot.download, so the document upload
#      raises again and never reaches the engine (Task 22 D1), restored.
#  29. A bite test that makes the terminal callback index a possibly-lost token
#      key again, so the stale button raises IndexError (Task 22 D2), restored.
#  30. A bite test that makes ``decode_required`` hand back the bare decoded key
#      again, so a handler indexes an empty tuple after a restart (the root cause
#      of the whole family, TASK 23), restored.
#  31. A bite test that reintroduces a dead button, so the dev tool's own catalog
#      check reports it (TASK 24), restored.
#
# Runs offline; no token, no network.
set -euo pipefail

# this script's own absolute path — resolved BEFORE the cd below moves the shell,
# so the proof ledger still finds its own file from any working directory.
# shellcheck disable=SC2034
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/zerops/cloud-bot-venv/bin/python}"

# ── never leave the tree bitten ────────────────────────────────────────────────
# Each bite test edits a source file, then restores it in a python ``finally``.
# If this script is killed mid-bite (SIGPIPE from a ``| tail``, a timeout, Ctrl-C)
# that restore never runs, and the product stays silently broken while the tree
# looks like ordinary uncommitted work. This restores ONLY files whose diff still
# carries the BITE marker, so a genuine uncommitted change is never destroyed.
cleanup_bites() {
  local f
  for f in $(git diff --name-only -- cloudbot tests 2>/dev/null); do
    if git diff -- "$f" 2>/dev/null | grep -q 'BITE:'; then
      echo
      echo "WARNING: a bite test was interrupted — restoring $f"
      git checkout -- "$f"
    fi
  done
  return 0
}
trap cleanup_bites EXIT INT TERM

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
assert any(b.url == URL for b in links), "the URL is not a LINK button"
# ...and it carries NO style: "link" is not a value Telegram
# accepts — it refuses the WHOLE message (measured).
assert all(b.style is None for b in links), "a URL button must carry no style"
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

echo
echo "== 24-27. Task 07 — the callback codec and the no-op edit (Bug A + Bug B) =="
echo "-- 24. the adm: parser is total: short/malformed callbacks are invalid, never raising --"
"$PY" -m pytest -q tests/unit/test_admin_callbacks.py
echo "-- 25. contract: every admin screen's callback parses AND has a handler route --"
"$PY" -m pytest -q tests/contract/test_admin_callback_contract.py
echo "-- 26. the edit signature covers the full payload; 'not modified' is success --"
"$PY" -m pytest -q tests/unit/test_sender.py -k "markup or rtl or modified or identical"
echo "-- 27. the exact live path: resume then back to the owner page, no exception --"
"$PY" -m pytest -q tests/unit/test_admin_handlers.py -k "not_modified or stale or short_audit or identical"

echo
echo "== 16. bite test: the full-payload edit signature is load-bearing =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

target = pathlib.Path("cloudbot/presentation/rich/sender.py")
original = target.read_text(encoding="utf-8")
guard = "        signature = (built.html, bool(built.is_rtl), _markup_signature(markup))\n"
bitten = original.replace(
    guard,
    '        signature = (built.html, bool(built.is_rtl), "")  # BITE: ignore the keyboard\n',
    1,
)
assert bitten != original, "bite substitution did not apply (signature moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_sender.py",
       "-k", "different_markup", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: the bite did not make the test red (it is not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the signature did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 17. bite test: the total parser prevents the live IndexError =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

target = pathlib.Path("cloudbot/presentation/screens/admin/callbacks.py")
original = target.read_text(encoding="utf-8")
guard = (
    "        if len(rest) != 2:\n"
    '            return _invalid(data, "bad_arity", action)\n'
    "        tg_id = _require_int(data, rest[0], action)\n"
)
bitten = original.replace(
    guard,
    "        tg_id = _require_int(data, rest[0], action)\n",
    1,
)
assert bitten != original, "bite substitution did not apply (audit_user guard moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_admin_callbacks.py",
       "-k", "malformed", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the malformed-callback test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: removing the arity guard did not make the parser raise (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the guard did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 18. bite test: the reply-keyboard guard is load-bearing =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce a reply keyboard (a ReplyKeyboardMarkup tallied by the guard) into a
# nav screen: the guard test must go red; restore, and it is green again.
target = pathlib.Path("cloudbot/presentation/screens/nav.py")
original = target.read_text(encoding="utf-8")
bitten = original + (
    "\n\n# BITE: a reply keyboard in disguise (must be refused)\n"
    "def _bite_reply_keyboard():\n"
    "    from aiogram.types import ReplyKeyboardMarkup\n"
    "    return ReplyKeyboardMarkup(keyboard=[])\n"
)
assert bitten != original, "bite append did not apply"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/architecture/test_no_reply_keyboard.py",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the reply-keyboard guard was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: reintroducing a reply keyboard did not make the guard red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: removing the bite did not return the guard to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 19. bite test: the persisted session resume is load-bearing =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Make /start ignore the session (never resume): the resume test must go red;
# restore the guard, and it is green again. This proves the column-not-FSM
# session is what puts the user back inside.
target = pathlib.Path("cloudbot/handlers/common.py")
original = target.read_text(encoding="utf-8")
guard = "    if inside is not None:\n"
bitten = original.replace(
    guard,
    "    if inside is not None and False:  # BITE: /start ignores the session\n",
    1,
)
assert bitten != original, "bite substitution did not apply (resume guard moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_session.py",
       "-k", "start_while_inside_renders", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the resume test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: ignoring the session did not make /start lose the resume (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the resume did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 20. bite test: the removed workspace-home Back row stays removed =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Restore the redundant bottom "Back" row on the workspace home (it lands on the
# same list as the log-out): the dead-end/duplication test must go red; restore,
# and it is green again. This proves D2's assertion is load-bearing.
target = pathlib.Path("cloudbot/presentation/screens/workspaces.py")
original = target.read_text(encoding="utf-8")
guard = '    if session:\n        rows += nav.nav_rows(locale, "workspace_home", inside=ws)\n'
back_row = (
    '    rows.append([inline(catalog.t("common.back", locale), '
    '_cb("ws:list", 0), intent=Intent.PRIMARY)])\n'
)
bitten = original.replace(guard, back_row + guard, 1)
assert bitten != original, "bite substitution did not apply (workspace-home exit moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_workspace_home_exit.py",
       "-k", "redundant", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the no-redundant-back-row test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: restoring the Back row did not make the test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: removing the bite did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 21. bite test: the inside nav block is the session's, not the account's =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Put the four account destinations (Settings/Language/Help + Menu) back into the
# inside branch of the ONE helper: the task-13 test must go red; restore, and it
# is green again. This proves the two-entry inside block is load-bearing.
target = pathlib.Path("cloudbot/presentation/screens/nav.py")
original = target.read_text(encoding="utf-8")
guard = "        return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]\n"
bite = (
    "        # BITE: the account destinations are back in the inside block\n"
    "        for _key, _label_key, _action in DESTINATIONS:\n"
    "            buttons.append(inline(catalog.t(_label_key, locale), _action, intent=Intent.PRIMARY))\n"
    "        if current != \"menu\":\n"
    "            buttons.append(inline(catalog.t(\"common.menu\", locale), \"nav:menu\", intent=Intent.PRIMARY))\n"
    + guard
)
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (inside branch moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_inside_nav_session.py",
       "-k", "exactly_home_and_workspaces or one_row_of_two",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the inside-block test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: restoring the account destinations did not make the test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: removing the bite did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 22. bite test: a nav entry never points at the screen you are on =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Put the self-referential `ws:list` back onto the workspaces list screen: the
# task-14 test must go red; restore, and it is green again. This proves R1/R2's
# assertion is load-bearing.
target = pathlib.Path("cloudbot/presentation/screens/workspaces.py")
original = target.read_text(encoding="utf-8")
guard = '    kb_rows += nav.nav_rows(locale, "workspaces", inside=inside)\n'
bite = guard + (
    '    kb_rows.append([inline(catalog.t("menu.workspaces", locale), "ws:list", intent=Intent.PRIMARY)])  # BITE\n'
)
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (ws_list nav call moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_top_level_nav.py",
       "-k", "drops_the_workspaces_and_menu_entries", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the no-self-nav-entry test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: restoring the self-referential ws:list did not make the test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: removing the bite did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 23. bite test: too_many_tunnels is not a silent redraw =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Map the refusal back onto the gate kind, so ``too_many_tunnels`` silently
# redraws the list again (the exact live bug): the task-15 test must go red;
# restore, and it is green again. This proves the dedicated refusal screen is
# load-bearing, not decoration.
target = pathlib.Path("cloudbot/handlers/features.py")
original = target.read_text(encoding="utf-8")
guard = '    "too_many_tunnels": "limit",\n'
bite = '    "too_many_tunnels": "gate",  # BITE: silently redraw the list\n'
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (refusal map moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_tunnel_handlers.py",
       "-k", "silent_redraw", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the no-silent-redraw test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: making too_many_tunnels silent did not make the test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the refusal screen did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 24. bite test: sticky terminal input is load-bearing =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Restore the OLD behaviour — `await state.clear()` after a command — so the very
# next plain message no longer reaches the session (the reported bug, verbatim:
# *«مش كل مره ادوس ارسال امر»*). The sticky test must go red; restore the re-set,
# and it is green again. This proves the sticky rule is the fix, not decoration.
target = pathlib.Path("cloudbot/handlers/terminal.py")
original = target.read_text(encoding="utf-8")
guard = (
    "    # Sticky (R1): keep the same state so the very next plain message is another\n"
    "    # command — no re-tap per command.\n"
    "    await state.set_state(TerminalInput.awaiting_command)\n"
    "    await state.update_data(ws_id=str(ws_id), ses_id=ses_id)\n"
)
bite = "    # BITE: the old behaviour — clear the sticky state after one command\n    await state.clear()\n"
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (sticky re-set moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_terminal_sticky.py",
       "-k", "one_tap_then_three or state_is_not_cleared",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the sticky test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: restoring the old clear did not make the sticky test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the sticky re-set did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 25. bite test: the error guard's Update unwrap is load-bearing (F-01) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce F-01: drop the Update unwrap so ``_render`` receives the Update,
# falls through both isinstance branches, and renders nothing. The F-01 callback
# test must go red; restore the unwrap, and it is green again. This proves the
# one-line unwrap is the fix for the critical silent-death defect, not decoration.
target = pathlib.Path("cloudbot/middleware/error_guard.py")
original = target.read_text(encoding="utf-8")
guard = "        if isinstance(event, Update):\n"
bite = "        if False:  # BITE: the un-wrap is gone (an Update reaches _render)\n"
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (unwrap guard moved?)"
cmd = [sys.executable, "-m", "pytest", "-q",
       "tests/unit/test_port_publishing_fixes.py",
       "-k", "f01_domain_error_on_a_callback", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the F-01 test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: dropping the unwrap did not make the F-01 test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the unwrap did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 26. bite test: an unattributed listening port is offered, not hidden (F-06) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce the F-06 filter — drop a listening port whose owner is unknown
# (``attributed`` false) from the picker. The task-20 test must go red; restore
# the offer-every-port rule, and it is green again. This proves the F-06 change
# ("never hide an unattributed socket") is load-bearing, not decoration.
target = pathlib.Path("cloudbot/handlers/features.py")
original = target.read_text(encoding="utf-8")
guard = '        if p.protocol in ("tcp", "tcp6")\n'
bite = '        if p.protocol in ("tcp", "tcp6") and p.attributed  # BITE: hide unknown owners\n'
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (detected-ports filter moved?)"
cmd = [sys.executable, "-m", "pytest", "-q",
       "tests/unit/test_tunnel_port_fixes.py",
       "-k", "f06_unattributed_listening_port_is_offered",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the F-06 test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: hiding the unattributed port did not make the F-06 test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the offer-every-port rule did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo "== 27. bite test: a URL button never carries a style Telegram rejects (F-01 live) =="
"$PY" - <<'PYEOF'
import pathlib, subprocess, sys

# The live defect: every tunnel LINK button went out with style="link". Telegram
# refuses the WHOLE message for it ("can't parse InlineKeyboardButton: Invalid button
# style specified"), so a published tunnel was created and never shown — and every
# local test passed, because the repo's own proof asserted style == "link".
# Reintroduce it, show the guard goes red, restore, show green.
target = pathlib.Path("cloudbot/presentation/keyboards/buttons.py")
original = target.read_text(encoding="utf-8")
guard = 'return InlineKeyboardButton(text=text, url=url, style=style)'
bite = 'return InlineKeyboardButton(text=text, url=url, style=style or "link")'
assert guard in original, "the url-button factory moved; update this proof"
bitten = original.replace(guard, bite, 1)
assert bitten != original, "the bite substitution did not apply"

cmd = [sys.executable, "-m", "pytest", "-q",
       "tests/unit/test_keyboards.py", "tests/unit/test_screen_keyboards.py",
       "-k", "url_button",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)

if before.returncode != 0:
    print("FAIL: the URL-button guard was not green before the bite")
    print(before.stdout[-600:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: putting style=\"link\" back did not make the guard red (not evidence)")
    print(red.stdout[-600:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the style-free URL button did not return it to green")
    print(green.stdout[-600:]); sys.exit(1)

def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")

print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PYEOF

echo

echo
echo "== 28. bite test: the upload awaits Bot.download and reads a BinaryIO (D1) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce the live defect: drop the ``await`` so the handler holds a coroutine,
# then feeds it to ``services.files.upload`` — the engine never receives the bytes
# and the user sees nothing (the exact production traceback). The upload test must
# go red; restore the await, and it is green again.
target = pathlib.Path("cloudbot/handlers/files.py")
original = target.read_text(encoding="utf-8")
guard = "    buffer = await message.bot.download(document)\n"
bite = "    buffer = message.bot.download(document)  # BITE: no await\n"
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (the download call moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_file_upload.py",
       "-k", "reaches_the_engine", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the upload test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: dropping the await did not make the upload test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the await did not return the upload test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 29. bite test: a terminal button never indexes a lost callback key (D2) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce the live IndexError: make the stale-button path index the decoded
# key unconditionally (as the original ``decode(cb.data).key[0]`` did), so a button
# drawn before a restart (empty token registry → key=()) raises instead of
# rendering the stale screen. The stale-button test must go red; restore the codec
# parse, and it is green again.
target = pathlib.Path("cloudbot/handlers/terminal.py")
original = target.read_text(encoding="utf-8")
guard = (
    "    parsed = _term.parse(cb.data)\n"
    "    if not parsed.valid:\n"
    "        await show_screen(cb, term_screens.stale_screen(ctx.locale), ctx=ctx, sender=sender)\n"
    "        return None\n"
    "    return parsed\n"
)

bite = (
    "    from cloudbot.presentation.keyboards.callbacks import decode  # BITE\n"
    "    _ = decode(cb.data).key[0]  # BITE: the old IndexError on a lost token\n"
    "    return _term.parse(cb.data)\n"
)
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (the codec parse moved?)"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_terminal_callbacks.py",
       "-k", "live_indexerror_callback_now_renders_a_screen",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the stale-button test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: indexing a lost key did not make the stale-button test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the total parse did not return the test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo

echo
echo "== 30. bite test: the restart rule is load-bearing (TASK 23) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# Reintroduce the ROOT CAUSE: make ``decode_required`` hand back the bare decoded
# callback again, so a handler's ``.key[0]`` indexes an empty tuple when the token
# registry died with the previous process (the old ``decode(cb.data).key[0]``). The
# every-screen restart contract test must go red; restore the guard, and it is
# green again. This proves the inline/token rule and the total read are what make
# the whole family impossible, not just one prefix.
target = pathlib.Path("cloudbot/presentation/keyboards/callbacks.py")
original = target.read_text(encoding="utf-8")
guard = (
    "    decoded = codec.decode(data)\n"
    "    if len(decoded.key) < arity:\n"
    "        raise StaleCallback(data)\n"
    "    return decoded\n"
)
bite = (
    "    decoded = codec.decode(data)\n"
    "    return decoded  # BITE: the unguarded read (old decode(...).key[n])\n"
)
bitten = original.replace(guard, bite, 1)
assert bitten != original, "bite substitution did not apply (the decode_required guard moved?)"
cmd = [sys.executable, "-m", "pytest", "-q",
       "tests/contract/test_callback_restart_contract.py",
       "-k", "survives_a_restart",
       "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the restart contract test was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: the unguarded read did not make the restart contract test red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: restoring the total read did not return the contract test to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

echo
echo "== 31. bite test: the dev tool's catalog check bites (TASK 24) =="
"$PY" - <<'PY'
import pathlib, subprocess, sys

# The dev tool walks the one product catalog and classifies every button. Prove
# the walk is load-bearing by reintroducing a DEAD BUTTON: point the "New
# workspace" button at a callback no handler can route. The tool's own catalog
# check (shared with the contract test) reports it — the walk finds a no-handler
# verdict — and the check goes red. Restore, and it is green again.
target = pathlib.Path("cloudbot/presentation/screens/workspaces.py")
original = target.read_text(encoding="utf-8")
guard = 'inline(catalog.t("menu.new_workspace", locale), "ws:new", intent=Intent.SUCCESS)])\n'
bite = 'inline(catalog.t("menu.new_workspace", locale), "zzz:dead", intent=Intent.SUCCESS)])  # BITE: a dead button\n'
assert guard in original, "the New-workspace button moved; update this proof"
bitten = original.replace(guard, bite, 1)
assert bitten != original, "the bite substitution did not apply"
cmd = [sys.executable, "-m", "pytest", "-q", "tests/unit/test_dev_tools.py",
       "-k", "catalog_walk", "--no-header", "-p", "no:cacheprovider"]
before = subprocess.run(cmd, capture_output=True, text=True)
try:
    target.write_text(bitten, encoding="utf-8")
    red = subprocess.run(cmd, capture_output=True, text=True)
finally:
    target.write_text(original, encoding="utf-8")
green = subprocess.run(cmd, capture_output=True, text=True)
if before.returncode != 0:
    print("FAIL: the dev tool's catalog check was not green before the bite")
    print(before.stdout[-800:]); sys.exit(1)
if red.returncode == 0:
    print("FAIL: a dead button did not make the tool's own check red (not evidence)")
    print(red.stdout[-800:]); sys.exit(1)
if green.returncode != 0:
    print("FAIL: removing the dead button did not return the check to green")
    print(green.stdout[-800:]); sys.exit(1)
def line(r):
    ls = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    return ls[-1].strip() if ls else ("red" if r.returncode else "green")
print("OK: before ->", line(before), "| bite ->", line(red), "| restored ->", line(green))
PY

# ── the proof ledger counts itself ─────────────────────────────────────────────
# The declared total used to be hand-written, and it drifted: it once claimed
# "thirty-eight proofs passed" while only 26 existed (the ids jumped 15 → 28).
# The count is now derived from this same file, so the claim and what actually
# ran cannot disagree.
N_TOTAL=$(grep -oE '== ?[0-9]+\. ' "$SELF" | grep -oE '[0-9]+' | sort -un | wc -l)
N_LAST=$(grep -oE '== ?[0-9]+\. ' "$SELF" | grep -oE '[0-9]+' | sort -un | tail -1)
N_LINES=$(grep -cE '^echo "== [0-9]+\.' "$SELF")
echo
echo "proof ledger: declared=$N_TOTAL highest_id=$N_LAST title_lines=$N_LINES"
if [ "$N_TOTAL" != "$N_LAST" ]; then
  echo "FAIL: proof ids are not contiguous 1..$N_TOTAL (highest is $N_LAST) — one was skipped"
  exit 1
fi
if [ "$N_LINES" != "$N_TOTAL" ]; then
  echo "FAIL: $N_LINES proof titles but $N_TOTAL ids — one is duplicated or missing"
  exit 1
fi
echo "all $N_TOTAL proofs passed (counted from this file, not hand-written)"
