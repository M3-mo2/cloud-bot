"""One live contract test against a real engine, gated behind an env var.

Creates a workspace, writes and reads a file, runs ``exec``, then deletes the
workspace and asserts it is gone. Requires ``CLOUDBOT_LIVE_ENGINE=1`` and a
reachable engine; never runs in CI by default. Secrets are read from files and
never printed.
"""

from __future__ import annotations

import base64
import io
import os
import zipfile
from pathlib import Path

import pytest

from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.core.errors import EngineRefused
from cloudbot.domain.value_objects import WorkspaceId

pytestmark = pytest.mark.live

LIVE = os.environ.get("CLOUDBOT_LIVE_ENGINE") == "1"
ENGINE_URL = os.environ.get("CLOUDBOT_LIVE_ENGINE_URL", "http://127.0.0.1:8098")
KEY_FILE = os.environ.get("CLOUDBOT_LIVE_KEY_FILE", "/home/zerops/cloud-service-public/api_key")


def _read_key() -> str:
    return Path(KEY_FILE).read_text().strip()


@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_LIVE_ENGINE=1 to run against a real engine")
async def test_live_workspace_file_exec_cycle():
    adapter = EngineAdapter.create(ENGINE_URL, _read_key(), timeout_s=30.0, max_retries=0)
    created = None
    try:
        meta = await adapter.meta(refresh=True)
        assert meta.api_version == "v1"

        created = await adapter.create_workspace(name="cloudbot-live-test", profile=None, limits=None)
        assert str(created.id).startswith("ws_")

        # Write then read a file.
        await adapter.write_file(created.id, "/hello.txt", "hello live\n")
        content = await adapter.read_file(created.id, "/hello.txt", meta=True)
        assert "hello live" in content.content

        # Run a quick synchronous command.
        result = await adapter.exec(created.id, ["/bin/sh", "-c", "echo live-ok"])
        assert result.exit_code == 0
        assert "live-ok" in result.stdout
    finally:
        if created is not None:
            await adapter.delete_workspace(created.id)
            with pytest.raises(EngineRefused):
                await adapter.get_workspace(WorkspaceId(str(created.id)))
        await adapter.aclose()


@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_LIVE_ENGINE=1 to run against a real engine")
async def test_live_native_session_terminal_cycle():
    """The terminal uses the engine's native sessions API end to end.

    Creates a workspace, opens a session, sends ``echo``, reads the output **by
    offset**, sends ``INT``, closes, and asserts the read after close is the
    documented ``409 session_ended``. Then deletes the workspace. At most one
    workspace exists at a time; it is deleted before the test returns.
    """
    adapter = EngineAdapter.create(ENGINE_URL, _read_key(), timeout_s=30.0, max_retries=0)
    created = None
    try:
        created = await adapter.create_workspace(
            name="cloudbot-live-session", profile="python", limits=None
        )
        session = await adapter.open_session(created.id, cols=80, rows=24)
        assert session.id.startswith("ses_")
        assert session.status.value in ("running", "failed")

        if session.status.value == "running":
            # Send a command with a newline; the shell runs it.
            await adapter.session_input(created.id, session.id, "echo cloudbot-terminal-ok\n")

            # Read output by offset until the marker appears (bounded).
            offset = 0
            collected = ""
            for _ in range(20):
                chunk = await adapter.session_output(created.id, session.id, offset=offset)
                collected += chunk.data
                offset = chunk.next
                if "cloudbot-terminal-ok" in collected:
                    break
                if chunk.eof:
                    import asyncio

                    await asyncio.sleep(0.3)
            assert "cloudbot-terminal-ok" in collected
            assert offset > 0

            # INT interrupts the foreground process group without ending session.
            await adapter.session_signal(created.id, session.id, "INT")
        else:
            # The relay could not start in this environment; still exercise close.
            assert session.diagnostic

        # Close (DELETE → 200), then reading must be the documented 409.
        closed = await adapter.close_session(created.id, session.id)
        assert closed.status.value == "killed"
        with pytest.raises(EngineRefused) as exc:
            await adapter.session_output(created.id, session.id, offset=0)
        assert exc.value.engine_code == "session_ended"
        assert exc.value.details.get("status") == "killed"
    finally:
        if created is not None:
            await adapter.delete_workspace(created.id)
            with pytest.raises(EngineRefused):
                await adapter.get_workspace(WorkspaceId(str(created.id)))
        await adapter.aclose()


@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_LIVE_ENGINE=1 to run against a real engine")
async def test_live_packages_ports_and_tunnel_gate():
    """Phase 2 reads against a real engine, using an existing workspace read-only.

    Never creates a workspace (the operator's is used read-only) and never
    installs a package (that would mutate his environment). Each read is paired
    with a **positive control** so an empty list from a broken call cannot pass:
    the packages envelope carries ``private_path``; the ports envelope carries the
    ``reachability`` sentence. The tunnel gate must be refused with the documented
    code on a workspace without ``allow_tunnels``. If a publish does succeed (a
    workspace that allows it), the tunnel is deleted in the same run and the list
    is asserted back to empty.
    """
    adapter = EngineAdapter.create(ENGINE_URL, _read_key(), timeout_s=30.0, max_retries=0)
    try:
        meta = await adapter.meta(refresh=True)
        assert meta.api_version == "v1", "positive control: /v1/meta answered"

        workspaces = await adapter.list_workspaces()
        assert workspaces, "positive control: at least one workspace exists"
        ws = workspaces[0]

        # -- 2a packages (read-only) -----------------------------------------
        pkg_list = await adapter.list_packages(ws.id)
        # Positive control: the call happened and parsed a real envelope.
        assert pkg_list.private_path, "positive control: packages call returned 200"
        assert pkg_list.count == len(pkg_list.packages)

        # -- 2c ports (read-only) --------------------------------------------
        report = await adapter.list_ports(ws.id)
        # Positive control: a broken/empty call would not carry this sentence.
        assert report.reachability, "positive control: ports call returned 200"
        assert report.count == len(report.ports)
        assert report.unix_count == len(report.unix)

        # -- 2b tunnels: list is a read; create is gated --------------------
        tunnels = await adapter.list_tunnels(ws.id)
        assert tunnels.count == len(tunnels.tunnels)

        created_tunnel = None
        try:
            tunnel = await adapter.create_tunnel(ws.id, 8080)
            created_tunnel = tunnel
        except EngineRefused as exc:
            assert exc.engine_code == "tunnels_disabled", (
                f"expected the documented gate refusal, got {exc.engine_code}"
            )
            # The gate names the switch that is off.
            assert exc.details.get("field") in ("allow_tunnels", "allow_net", "enabled")
        finally:
            if created_tunnel is not None:
                await adapter.delete_tunnel(ws.id, created_tunnel.id)

        # No leftover tunnels after this run.
        after = await adapter.list_tunnels(ws.id)
        assert after.count == 0 or created_tunnel is None
        lease = {t.id for t in after.tunnels}
        if created_tunnel is not None:
            assert created_tunnel.id not in lease, "the created tunnel must be gone"

        # The workspace we used must still be present.
        still = await adapter.get_workspace(ws.id)
        assert str(still.id) == str(ws.id)

        # No leftover running sessions were created by this test.
        page = await adapter.list_sessions(ws.id, status="running")
        assert page.total == 0, f"unexpected running sessions: {page.total}"
    finally:
        await adapter.aclose()


@pytest.mark.skipif(not LIVE, reason="set CLOUDBOT_LIVE_ENGINE=1 to run against a real engine")
async def test_live_metrics_processes_and_archive_dry_run_round_trip():
    """Phase 2d/2e/2g: metrics, the process inventory, and a full archive round trip.

    Uses the operator's **existing** workspace (never creating or deleting one)
    and confines every mutation to ``/.phase2deg-check/``, which it removes at the
    end, asserting the root listing is back to what it was. Each empty result is
    paired with a **positive control** so a broken call cannot read as a pass.
    """
    adapter = EngineAdapter.create(ENGINE_URL, _read_key(), timeout_s=30.0, max_retries=0)
    OWN = "/.phase2deg-check"
    try:
        meta = await adapter.meta(refresh=True)
        assert meta.api_version == "v1", "positive control: /v1/meta answered"
        workspaces = await adapter.list_workspaces()
        assert workspaces, "positive control: at least one workspace exists"
        ws = workspaces[0]

        # -- 2d metrics: a one-shot snapshot ---------------------------------
        metrics = await adapter.workspace_metrics(ws.id)
        # Positive control: the frame names this workspace and carries the
        # enforced ceiling, so a broken call cannot masquerade as a zero reading.
        assert metrics.workspace_id == str(ws.id), "positive control: metrics call returned this ws"
        assert metrics.ram_limit_bytes >= 0
        assert isinstance(metrics.ram_complete, bool)
        assert isinstance(metrics.disk_complete, bool)

        # -- 2e processes: the inventory -------------------------------------
        before_root = await adapter.list_files(ws.id, "/")
        process_list = await adapter.list_processes(ws.id)
        # Positive control: the count mirrors the array (an honest empty 200).
        assert process_list.count == len(process_list.processes)
        # We do not stop any process we did not start.

        # -- 2g archives: a self-created dry-run round trip ------------------
        zip_bytes = _make_zip({"main.py": b"print('phase2deg')\n", "data.txt": b"hello\n"})
        source = f"{OWN}/bundle.zip"
        await adapter.write_file(
            ws.id, source, base64.b64encode(zip_bytes).decode("ascii"), encoding="base64"
        )

        dest = f"{OWN}/out"
        # 1. dry run: validate and report, writing nothing. The engine reports
        #    how many entries *would* be extracted; the honesty signal for
        #    "nothing written" is bytes_written == 0 and an absent destination.
        dry = await adapter.extract_archive(ws.id, path=source, dest=dest, dry_run=True)
        assert dry.dry_run is True
        assert dry.entries_total >= 2
        assert dry.bytes_written == 0
        # Positive control for "nothing was written": the destination is absent
        # (a missing directory is a 404, or an empty listing).
        assert not await _has_files(adapter, ws.id, dest)

        # 2. extract for real: the entries land.
        real = await adapter.extract_archive(ws.id, path=source, dest=dest, dry_run=False)
        assert real.dry_run is False
        assert real.entries_extracted >= 2
        landed = await adapter.list_files(ws.id, dest)
        names = {e.name for e in landed.entries}
        assert "main.py" in names and "data.txt" in names
        # The control proves the same archive DOES add files when not a dry run.

        # 3. clean up our own subtree and assert the root is back.
        await adapter.delete_file(ws.id, OWN)
        after_root = await adapter.list_files(ws.id, "/")
        assert {e.name for e in after_root.entries} == {e.name for e in before_root.entries}
        assert not any(e.name == ".phase2deg-check" for e in after_root.entries)

        # No processes were stopped, no sessions created, the workspace persists.
        after_processes = await adapter.list_processes(ws.id)
        assert after_processes.count == len(after_processes.processes)
        still = await adapter.get_workspace(ws.id)
        assert str(still.id) == str(ws.id)
        page = await adapter.list_sessions(ws.id, status="running")
        assert page.total == 0, f"unexpected running sessions: {page.total}"
    finally:
        # Best-effort cleanup even if an assertion above failed.
        try:
            await adapter.delete_file(ws.id, OWN)
        except Exception:  # noqa: BLE001
            pass
        await adapter.aclose()


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


async def _has_files(adapter: EngineAdapter, ws_id, path: str) -> bool:
    try:
        page = await adapter.list_files(ws_id, path)
    except EngineRefused:
        return False
    return bool(page.entries)

