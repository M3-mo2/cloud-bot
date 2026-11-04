"""Service use-cases: workspaces, files, execution against the fake engine."""

from __future__ import annotations

import pytest

from cloudbot.core.errors import EngineRefused, InvalidInput
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import Limits, WorkspaceId

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def test_get_or_create_is_idempotent(services):
    u1 = await services.account.get_or_create(7, default_locale=Locale.EN)
    u2 = await services.account.get_or_create(7, default_locale=Locale.AR)
    assert u1.tg_id == u2.tg_id
    assert u2.locale is Locale.EN  # first choice wins


async def test_set_locale(services):
    await services.account.get_or_create(8, default_locale=Locale.EN)
    await services.account.set_locale(8, Locale.AR)
    user = await services.account.profile(8, default_locale=Locale.EN)
    assert user.locale is Locale.AR


async def test_create_registers_ownership(services, users):
    ws = await services.workspaces.create(5, "myws")
    assert await users.owns(5, ws.id)
    assert ws.owner_tg_id == 5


async def test_create_rejects_empty_name(services):
    with pytest.raises(InvalidInput):
        await services.workspaces.create(5, "   ")


async def test_create_rejects_name_too_long(services):
    with pytest.raises(InvalidInput):
        await services.workspaces.create(5, "x" * 129)


async def test_create_rejects_limits_above_maxima(services):
    maxima = (await services.workspaces.meta()).limits_maxima
    over = Limits(cpu_cores=maxima.cpu_cores + 1)
    with pytest.raises(InvalidInput):
        await services.workspaces.create(5, "big", limits=over)


async def test_list_only_owned(services, users):
    ws = await services.workspaces.create(5, "a")
    await users.get_or_create(6, default_locale=Locale.EN)
    mine = await services.workspaces.list(5)
    theirs = await services.workspaces.list(6)
    assert [w.id for w in mine] == [ws.id]
    assert theirs == []


async def test_delete_removes_registration(services, users):
    ws = await services.workspaces.create(5, "gone")
    await services.workspaces.delete(5, ws.id)
    assert not await users.owns(5, ws.id)


async def test_suspended_workspace_refuses_exec(services, users):
    # scraper is seeded suspended.
    import cloudbot.domain.value_objects as vo

    scrapper = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK5")
    await users.add_workspace(9, scrapper)
    with pytest.raises(EngineRefused) as exc:
        await services.execution.exec(9, scrapper, "echo hi")
    assert exc.value.engine_code == "workspace_suspended"


async def test_exec_clamps_timeout(services):
    clamped = services.execution.clamp_exec_timeout(90)
    assert clamped == 60
    assert services.execution.clamp_exec_timeout(None) == 60


async def test_run_and_log_offset_protocol(services, users):
    ws = await services.workspaces.create(5, "runner")
    run = await services.execution.run_command(5, ws.id, ["echo", "hi"])
    assert run.status.value == "queued"
    # Read logs once; then advance the fake run and read again.
    state = await services.execution.read_all_logs(5, ws.id, str(run.id))
    _ = await services.execution.get_run(5, ws.id, str(run.id))  # advance
    state = await services.execution.read_all_logs(5, ws.id, str(run.id), state)
    assert state.offset_stdout > 0
    final = await services.execution.get_run(5, ws.id, str(run.id))
    assert final.status.is_terminal


async def test_file_upload_size_precheck(services):
    meta = await services.files.meta()
    with pytest.raises(InvalidInput):
        services.files.check_upload_size((meta.upload_mb + 1) * 1024 * 1024, meta)
    services.files.check_upload_size(1024, meta)  # no raise
