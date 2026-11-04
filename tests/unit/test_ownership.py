"""The isolation gate: table-driven over every workspace-scoped service method.

A foreign ``tg_id`` must get ``NotOwned`` and the engine must **never** be called
for it (persistence.md §5). This is the client-side half of the 404-not-403 rule.
"""

from __future__ import annotations

import pytest

from cloudbot.core.errors import NotOwned
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.services.account import AccountService
from cloudbot.services.execution import ExecutionService
from cloudbot.services.files import FilesService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.sessions import SessionsService
from cloudbot.services.workspaces import WorkspacesService
from cloudbot.core.locale import Locale

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


class SpyEngine:
    """Wraps a real engine and records every call that reaches it."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def wrapper(*args, **kwargs):
            self.calls.append(name)
            return await attr(*args, **kwargs)

        return wrapper


async def _setup(engine, users, sessions):
    """Owner 1 owns WS; owner 2 does not. Returns (spy, services)."""
    spy = SpyEngine(engine)
    ownership = OwnershipService(users)
    await users.get_or_create(1, default_locale=Locale.EN)
    await users.get_or_create(2, default_locale=Locale.EN)
    await users.add_workspace(1, WS)
    services = type(
        "S",
        (),
        {
            "workspaces": WorkspacesService(spy, users, ownership),
            "files": FilesService(spy, ownership),
            "execution": ExecutionService(spy, ownership),
            "sessions": SessionsService(spy, sessions, ownership),
        },
    )()
    return spy, services


# Every workspace-scoped method, as (label, awaitable factory).
def _scoped_calls(services):
    w = services.workspaces
    f = services.files
    e = services.execution
    s = services.sessions
    return {
        "workspaces.get": lambda: w.get(2, WS),
        "workspaces.patch": lambda: w.patch(2, WS, name="y"),
        "workspaces.suspend": lambda: w.suspend(2, WS),
        "workspaces.resume": lambda: w.resume(2, WS),
        "workspaces.stop": lambda: w.stop(2, WS),
        "workspaces.delete": lambda: w.delete(2, WS),
        "workspaces.usage": lambda: w.usage(2, WS),
        "files.list": lambda: f.list(2, WS, "/"),
        "files.view": lambda: f.view(2, WS, "/main.py"),
        "files.write": lambda: f.write(2, WS, "/x", "hi"),
        "files.mkdir": lambda: f.mkdir(2, WS, "/d"),
        "files.rename": lambda: f.rename(2, WS, "/a", "/b"),
        "files.delete": lambda: f.delete(2, WS, "/a"),
        "execution.exec": lambda: e.exec(2, WS, "echo hi"),
        "execution.run_command": lambda: e.run_command(2, WS, ["echo", "hi"]),
        "execution.run_file": lambda: e.run_file(2, WS, "/main.py"),
        "execution.get_run": lambda: e.get_run(2, WS, "run_x"),
        "execution.list_runs": lambda: e.list_runs(2, WS),
        "execution.tail_logs": lambda: e.tail_logs(2, WS, "run_x", "stdout", 0),
        "execution.stop_run": lambda: e.stop_run(2, WS, "run_x"),
        "execution.purge": lambda: e.purge(2, WS),
        "execution.delete_run": lambda: e.delete_run(2, WS, "run_x"),
        "sessions.open": lambda: s.open(2, WS),
        "sessions.list_for_workspace": lambda: s.list_for_workspace(2, WS),
        "sessions.get": lambda: s.get(2, WS, "ses_x"),
        "sessions.send_input": lambda: s.send_input(2, WS, "ses_x", "hi"),
        "sessions.read_output": lambda: s.read_output(2, WS, "ses_x", 0),
        "sessions.resize": lambda: s.resize(2, WS, "ses_x", cols=100, rows=40),
        "sessions.signal": lambda: s.signal(2, WS, "ses_x", "INT"),
        "sessions.close": lambda: s.close(2, WS, "ses_x"),
    }


async def test_every_scoped_method_refuses_foreign_user(engine, users, sessions):
    spy, services = await _setup(engine, users, sessions)
    for label, factory in _scoped_calls(services).items():
        spy.calls.clear()
        with pytest.raises(NotOwned):
            await factory()
        # The engine is never reached for a workspace the caller does not own.
        assert spy.calls == [], f"{label} reached the engine while not owned: {spy.calls}"


async def test_owner_can_reach(engine, users, sessions):
    spy, services = await _setup(engine, users, sessions)
    ws = await services.workspaces.get(1, WS)
    assert ws.owner_tg_id == 1
    assert "get_workspace" in spy.calls


async def test_foreign_looks_like_missing(engine, users, sessions):
    _spy, services = await _setup(engine, users, sessions)
    with pytest.raises(NotOwned):
        await services.workspaces.get(2, WorkspaceId("ws_00000000000000000000000000"))
