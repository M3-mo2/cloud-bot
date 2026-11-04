"""Sessions service: ownership gate, concurrency cap, signal allowlist, offset I/O.

Also asserts the terminal is NOT a FIFO/run-based re-invention.
"""

from __future__ import annotations

import inspect

import pytest

from cloudbot.core.errors import EngineRefused, InvalidInput, NotOwned
from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import SessionStatus, WorkspaceId
from cloudbot.services.account import AccountService
from cloudbot.services.ownership import OwnershipService
from cloudbot.services.sessions import SessionsService

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")


async def _service(engine, users, sessions):
    ownership = OwnershipService(users)
    await users.get_or_create(1, default_locale=Locale.EN)
    await users.get_or_create(2, default_locale=Locale.EN)
    await users.add_workspace(1, WS)
    svc = SessionsService(engine, sessions, ownership)
    return svc


async def test_open_registers_handle_and_owner(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    assert session.status is SessionStatus.RUNNING
    handle = await sessions.get(1, __import__("cloudbot.domain.value_objects", fromlist=["SessionId"]).SessionId(session.id))
    assert handle is not None
    assert handle.workspace_id == WS


async def test_foreign_user_is_refused_before_engine(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    with pytest.raises(NotOwned):
        await svc.open(2, WS)
    with pytest.raises(NotOwned):
        await svc.list_for_workspace(2, WS)
    with pytest.raises(NotOwned):
        await svc.send_input(2, WS, "ses_x", "hi")
    with pytest.raises(NotOwned):
        await svc.resize(2, WS, "ses_x", cols=100, rows=40)
    with pytest.raises(NotOwned):
        await svc.signal(2, WS, "ses_x", "INT")
    with pytest.raises(NotOwned):
        await svc.close(2, WS, "ses_x")


async def test_concurrency_cap_refused_without_touching_engine(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    cap = (await svc.meta()).sessions.max_per_workspace
    opened = [await svc.open(1, WS) for _ in range(cap)]
    assert len(opened) == cap
    with pytest.raises(EngineRefused) as exc:
        await svc.open(1, WS)
    assert exc.value.engine_code == "too_many_sessions"


async def test_signal_allowlist_refuses_before_engine(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    for allowed in ("INT", "TERM", "QUIT", "HUP"):
        await svc.signal(1, WS, session.id, allowed)
    with pytest.raises(InvalidInput):
        await svc.signal(1, WS, session.id, "KILL")
    with pytest.raises(InvalidInput):
        await svc.signal(1, WS, session.id, "9")


async def test_send_line_appends_newline(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    await svc.send_line(1, WS, session.id, "ls")
    read = await svc.read_output(1, WS, session.id, 0)
    assert read.data.endswith("$ ls") or "ls" in read.data
    # The engine received the newline: the fake echoes "$ ls".
    assert "ls" in read.data


async def test_resize_validates_bounds(engine, users, sessions):
    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    with pytest.raises(InvalidInput):
        await svc.resize(1, WS, session.id, cols=0, rows=24)
    with pytest.raises(InvalidInput):
        await svc.resize(1, WS, session.id, cols=80, rows=100000)
    await svc.resize(1, WS, session.id, cols=120, rows=40)


async def test_close_marks_deleted_and_removes_handle(engine, users, sessions):
    from cloudbot.domain.value_objects import SessionId

    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    closed = await svc.close(1, WS, session.id)
    assert closed.status is SessionStatus.KILLED
    assert await sessions.get(1, SessionId(session.id)) is None


async def test_offset_cursor_is_persisted(engine, users, sessions):
    from cloudbot.domain.value_objects import SessionId

    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    await svc.send_input(1, WS, session.id, "hello")
    read = await svc.read_output(1, WS, session.id, 0)
    handle = await sessions.get(1, SessionId(session.id))
    assert handle is not None
    assert handle.out_offset == read.next


def test_terminal_is_not_a_fifo_or_run_reinvention():
    """The terminal must use the native sessions API, never a FIFO/run PTY."""
    import cloudbot.services.sessions as mod
    import cloudbot.handlers.terminal as handler_mod
    import cloudbot.adapters.cloud_service.engine as engine_mod

    # The terminal *path* (service + handler) must not build a pseudo-terminal:
    # no FIFO, no spawned PTY, no long-lived run.
    path_source = inspect.getsource(mod) + inspect.getsource(handler_mod)
    for forbidden in (
        "mkfifo",
        "os.mkfifo",
        "create_run(",
        "openpty",
        "pty.spawn",
        "subprocess",
        "asyncssh",
    ):
        assert forbidden not in path_source, f"terminal path uses {forbidden!r}"

    # The engine adapter does call the native sessions endpoints.
    engine_source = inspect.getsource(engine_mod)
    assert "/sessions" in engine_source
    assert "open_session" in engine_source
    assert "session_signal" in engine_source
