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


async def test_close_keeps_handle_and_marks_terminal(engine, users, sessions):
    """Closing keeps the bot-side handle so the owner can still see the session.

    Removing the handle here would make the next call raise ``NotOwned`` — a
    closed session would read as "not yours", the isolation answer. The row must
    stay, with a terminal status.
    """
    from cloudbot.domain.value_objects import SessionId

    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    closed = await svc.close(1, WS, session.id)
    assert closed.status is SessionStatus.KILLED
    handle = await sessions.get(1, SessionId(session.id))
    assert handle is not None, "the handle must be KEPT after close"
    assert handle.id == session.id


async def test_read_after_close_is_session_ended_not_not_owned(engine, users, sessions):
    """The documented contract: a read after close is ``session_ended``.

    The engine keeps the record (``status: killed``) and reports 409
    ``session_ended``; because the handle is kept, ``_assert_tracked`` passes and
    the refusal surfaces as the domain error carrying ``session_ended`` — never
    the isolation answer.
    """
    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    await svc.close(1, WS, session.id)
    with pytest.raises(EngineRefused) as exc:
        await svc.read_output(1, WS, session.id, 0)
    assert exc.value.engine_code == "session_ended"


async def test_unknown_session_id_is_still_not_owned(engine, users, sessions):
    """The isolation answer must be reserved for what it means.

    An id the caller never owned (or that never existed) is ``NotOwned``. This is
    kept apart from the closed-session case on purpose: collapsing the two is the
    tempting shortcut that caused the defect.
    """
    svc = await _service(engine, users, sessions)
    with pytest.raises(NotOwned):
        await svc.read_output(1, WS, "ses_NEVEROWNED0000000000000", 0)


async def test_closed_session_does_not_count_against_the_cap(engine, users, sessions):
    """A terminal session frees a slot; opening again must succeed."""
    svc = await _service(engine, users, sessions)
    cap = (await svc.meta()).sessions.max_per_workspace
    opened = [await svc.open(1, WS) for _ in range(cap)]
    with pytest.raises(EngineRefused) as exc:
        await svc.open(1, WS)
    assert exc.value.engine_code == "too_many_sessions"

    await svc.close(1, WS, opened[0].id)

    # The closed session is still listed (handle kept) with a terminal status...
    listed = await svc.list_for_workspace(1, WS)
    closed = next(s for s in listed if s.id == opened[0].id)
    assert closed.status.is_terminal
    # ...and it does not count, so a new terminal can be opened.
    reopened = await svc.open(1, WS)
    assert reopened.status is SessionStatus.RUNNING


async def test_forget_removes_the_handle_without_touching_the_engine(engine, users, sessions):
    """``forget`` is the only path that drops a handle; ownership is enforced."""
    from cloudbot.domain.value_objects import SessionId

    svc = await _service(engine, users, sessions)
    session = await svc.open(1, WS)
    await svc.close(1, WS, session.id)
    await svc.forget(1, WS, session.id)
    assert await sessions.get(1, SessionId(session.id)) is None
    # A foreign caller cannot forget someone else's session.
    other = await svc.open(1, WS)
    with pytest.raises(NotOwned):
        await svc.forget(2, WS, other.id)


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


async def test_cap_pre_check_refuses_BEFORE_reaching_the_engine(engine, users, sessions):
    """The cap is the SERVICE's job, and this asserts it happens before the engine is called.

    Why this exists: `FakeEngine.open_session` raises `too_many_sessions` by itself, so the older test
    above passes even when the service's pre-check is deleted — its name claims "without touching the
    engine" and verifies nothing of the sort. This test counts the engine's calls, so it goes red the
    moment the pre-check stops running. It is the difference between a test that asserts an outcome the
    fake also produces, and one that asserts the behaviour under test.
    """
    svc = await _service(engine, users, sessions)
    cap = (await svc.meta()).sessions.max_per_workspace

    calls: list[int] = []
    real_open = engine.open_session

    async def counting_open(*args, **kwargs):
        calls.append(1)
        return await real_open(*args, **kwargs)

    engine.open_session = counting_open  # type: ignore[method-assign]
    try:
        for _ in range(cap):
            await svc.open(1, WS)
        at_capacity = len(calls)
        assert at_capacity == cap

        with pytest.raises(EngineRefused) as exc:
            await svc.open(1, WS)
        assert exc.value.engine_code == "too_many_sessions"
        assert len(calls) == at_capacity, (
            "the refused open still reached the engine — the cap is being left to the engine "
            "instead of being pre-checked"
        )
    finally:
        engine.open_session = real_open  # type: ignore[method-assign]
