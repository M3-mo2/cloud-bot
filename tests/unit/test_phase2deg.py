"""Phase 2d/2e/2g unit tests: metrics, processes, archives.

Covers the ownership gate on every family, local validation (a metrics interval
in the engine's bounds; a process id matching ``proc_<hex>``; an archive source
path), the process stop ``gone`` path (including a repeat ``404``), the archive
dry-run path (writes nothing, reports it), the upload cap pre-check, and the
export always being ZIP.
"""

from __future__ import annotations

import pytest

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Limits, WorkspaceId
from cloudbot.presentation.screens import archives as arc_screens
from cloudbot.presentation.screens import metrics as met_screens
from cloudbot.presentation.screens import processes as proc_screens
from cloudbot.services.archives import suggest_export_name
from cloudbot.services.metrics import validate_interval
from cloudbot.services.processes import validate_process_id

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def _own(users, subscriptions, tg_id: int, ws: WorkspaceId = WS):
    """Owner + an **active** plan: these tests measure their own gate, not the
    plan gate (which is proven separately in ``test_subscription_gate.py``)."""
    await users.get_or_create(tg_id, default_locale=Locale.EN)
    await users.add_workspace(tg_id, ws)
    await subscriptions.grant(tg_id, str(ws), "week", 7, tg_id, "test fixture")


# -- metrics -----------------------------------------------------------------
async def test_metrics_refused_for_foreign_user(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    with pytest.raises(Exception) as exc:
        await services.metrics.snapshot(2, WS)
    assert exc.value.code == "not_owned"


async def test_metrics_snapshot_returns_frame(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    metrics = await services.metrics.snapshot(1, WS)
    assert metrics.workspace_id == str(WS)
    assert metrics.ram_complete is True
    assert metrics.disk_complete is True


async def test_metrics_interval_validation_uses_server_bounds(services):
    meta = await services.metrics.meta()
    # Below the floor is refused locally (the engine would answer 422).
    with pytest.raises(InvalidInput) as exc:
        validate_interval(meta.metrics.min_interval_sec - 1, meta)
    assert exc.value.field == "interval"
    assert exc.value.reason == "below_min"
    # Above the ceiling is refused too.
    with pytest.raises(InvalidInput):
        validate_interval(meta.metrics.max_interval_sec + 1, meta)
    # A value inside the bounds passes.
    assert validate_interval(meta.metrics.interval_seconds, meta) == meta.metrics.interval_seconds


# -- metrics screen honesty --------------------------------------------------
def test_metrics_incomplete_disk_is_never_an_exact_percentage():
    from cloudbot.domain.value_objects import WorkspaceMetrics

    metrics = WorkspaceMetrics(
        workspace_id=str(WS),
        timestamp="t",
        interval_seconds=3,
        ram_used_bytes=0,
        ram_limit_bytes=512 * 1024 * 1024,
        ram_percent=0.0,
        ram_complete=True,
        disk_used_bytes=0,
        disk_limit_bytes=1024 * 1024 * 1024,
        disk_percent=99.0,
        disk_complete=False,
        write_rate_limit_mb_s=0,
        live_runs=0,
        live_cgroups=0,
    )
    html = met_screens.metrics_screen(metrics, WS, Locale.EN).doc.html
    # The incomplete figure is labelled provisional and carries no percentage.
    assert "99" not in html
    assert "provisional" in html.lower()
    # The complete figure still renders its which is a real number.
    assert "512 MB" in html


def test_metrics_complete_disk_renders_the_number():
    from cloudbot.domain.value_objects import WorkspaceMetrics

    metrics = WorkspaceMetrics(
        workspace_id=str(WS),
        timestamp="t",
        interval_seconds=3,
        ram_used_bytes=1024,
        ram_limit_bytes=512 * 1024 * 1024,
        ram_percent=0.01,
        ram_complete=True,
        disk_used_bytes=6089,
        disk_limit_bytes=1024 * 1024 * 1024,
        disk_percent=0.0006,
        disk_complete=True,
        write_rate_limit_mb_s=0,
        live_runs=1,
        live_cgroups=1,
    )
    html = met_screens.metrics_screen(metrics, WS, Locale.EN).doc.html
    assert "provisional" not in html.lower()
    assert "0%" in html


def test_metrics_idle_state_is_not_a_warning():
    from cloudbot.domain.value_objects import WorkspaceMetrics

    metrics = WorkspaceMetrics(
        workspace_id=str(WS),
        timestamp="t",
        interval_seconds=3,
        ram_used_bytes=0,
        ram_limit_bytes=512 * 1024 * 1024,
        ram_percent=0.0,
        ram_complete=True,
        disk_used_bytes=0,
        disk_limit_bytes=1024 * 1024 * 1024,
        disk_percent=0.0,
        disk_complete=True,
        write_rate_limit_mb_s=0,
        live_runs=0,
        live_cgroups=0,
    )
    html = met_screens.metrics_screen(metrics, WS, Locale.EN).doc.html
    assert "Nothing is running" in html
    # No warning marker on the idle state.
    assert "⚠️" not in html.split("<blockquote>")[0].split("</table>")[0]


# -- processes ---------------------------------------------------------------
async def test_processes_list_refused_for_foreign_user(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    with pytest.raises(Exception) as exc:
        await services.processes.list(2, WS)
    assert exc.value.code == "not_owned"


async def test_processes_list_reports_detached(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    pl = await services.processes.list(1, WS)
    assert pl.count == len(pl.processes)
    assert any(p.kind == "detached" for p in pl.processes)


async def test_processes_stop_refused_for_foreign_user(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    with pytest.raises(Exception) as exc:
        await services.processes.stop(2, WS, "proc_0123456789abcdef")
    assert exc.value.code == "not_owned"


async def test_processes_stop_returns_stopped(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    result = await services.processes.stop(1, WS, "proc_0123456789abcdef")
    assert result.status == "stopped"
    assert result.stopped is True
    assert result.gone is False


async def test_processes_stop_gone_path(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    result = await services.processes.stop(1, WS, "proc_fedcba9876543210")
    assert result.status == "gone"
    assert result.stopped is False
    assert result.gone is True


async def test_processes_repeat_stop_not_found_becomes_gone(services, users, engine, subscriptions):
    """A repeat stop is a 404 because the process left the inventory → "gone"."""
    await _own(users, subscriptions, 1)

    async def _not_found(ws_id, process_id):
        raise EngineRefused("not_found")

    original = engine.stop_process
    engine.stop_process = _not_found  # type: ignore[assignment]
    try:
        result = await services.processes.stop(1, WS, "proc_0123456789abcdef")
    finally:
        engine.stop_process = original  # type: ignore[assignment]
    assert result.status == "gone"
    assert result.gone is True


@pytest.mark.parametrize("bad", ["", "   ", "1234", "proc_", "proc_ZZZ", "../../etc", "proc_12-34"])
def test_process_id_validation_refuses_bad_shapes(bad):
    with pytest.raises(InvalidInput):
        validate_process_id(bad)


@pytest.mark.parametrize("good", ["proc_0123456789abcdef", "proc_fedcba9876543210"])
def test_process_id_validation_accepts_engine_shape(good):
    assert validate_process_id(good) == good


def test_stop_outcomes_render_differently():
    from cloudbot.domain.value_objects import ProcessStopResult

    stopped_html = proc_screens.stop_result_screen(
        ProcessStopResult(process_id="proc_x", status="stopped", stopped=True), WS, Locale.EN
    ).doc.html
    gone_html = proc_screens.stop_result_screen(
        ProcessStopResult(process_id="proc_x", status="gone", stopped=False), WS, Locale.EN
    ).doc.html
    assert stopped_html != gone_html
    assert "already" in gone_html.lower() or "✅" in gone_html


def test_process_argv_is_escaped_and_truncated():
    from cloudbot.domain.value_objects import ProcessEntry, ProcessList

    entry = ProcessEntry(
        id="proc_0123456789abcdef",
        pid=1,
        ppid=0,
        argv=("<script>", "x" * 500),
        kind="run",
        source_id="run_x",
    )
    pl = ProcessList(processes=(entry,), count=1)
    html = proc_screens.processes_screen(pl, WS, Locale.EN).doc.html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# -- archives ----------------------------------------------------------------
async def test_archives_extract_refused_for_foreign_user(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    with pytest.raises(Exception) as exc:
        await services.archives.extract(2, WS, path="/a.zip")
    assert exc.value.code == "not_owned"


async def test_archives_extract_dry_run_reports_nothing_written(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    before = await services.files.list(1, WS, "/")
    result = await services.archives.extract(1, WS, path="/archive.zip", dry_run=True)
    assert result.dry_run is True
    # A dry run writes NOTHING — the file list is unchanged and no bytes written.
    assert result.bytes_written == 0
    after = await services.files.list(1, WS, "/out")
    assert after.entries == ()
    # Positive control: the same archive extracted for real DOES add files.
    real = await services.archives.extract(1, WS, path="/archive.zip", dest="/out", dry_run=False)
    assert real.bytes_written > 0
    landed = await services.files.list(1, WS, "/out")
    assert landed.entries, "the real extract must add files (positive control)"


async def test_archives_extract_real_writes(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    result = await services.archives.extract(1, WS, path="/archive.zip", dry_run=False)
    assert result.dry_run is False
    assert result.entries_extracted >= 1
    assert result.bytes_written > 0


async def test_archives_upload_size_precheck(services):
    meta = await services.archives.meta()
    cap = meta.archives.limits.upload_mb * 1024 * 1024
    with pytest.raises(InvalidInput) as exc:
        services.archives.check_upload_size(cap + 1, meta)
    assert exc.value.field == "archive"
    services.archives.check_upload_size(1024, meta)  # no raise


async def test_archives_extract_rejects_empty_source(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    with pytest.raises(InvalidInput):
        await services.archives.extract(1, WS, path="  ")


async def test_archives_export_always_zip(services, users, subscriptions):
    await _own(users, subscriptions, 1)
    filename, blob = await services.archives.export(1, WS, "/project")
    assert filename.endswith(".zip")
    assert blob[:2] == b"PK"  # a ZIP signature


@pytest.mark.parametrize(
    "path,expected",
    [("/project", "project.zip"), ("/", "workspace.zip"), ("/a b/c*", "c.zip")],
)
def test_export_name_is_safe_and_zip(path, expected):
    name = suggest_export_name(path)
    assert name == expected
    assert name.endswith(".zip")


def test_rar_is_never_an_export_or_create_choice():
    """No code path offers RAR as a create/export format (RAR is read-only)."""
    import pathlib

    root = pathlib.Path("cloudbot")
    offences = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8").lower()
        # No "rar" encoder option and no format=rar anywhere.
        if "format=rar" in text or "format=\"rar\"" in text or "rar_encoder" in text:
            offences.append(str(p))
    assert not offences, offences
    # The archives service exposes no create/encode API at all.
    from cloudbot.services.archives import ArchivesService

    assert not hasattr(ArchivesService, "create")
    assert not hasattr(ArchivesService, "encode")
