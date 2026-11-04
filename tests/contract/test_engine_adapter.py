"""Contract tests: the engine adapter against recorded responses.

Replays recorded Cloud Service responses through ``httpx.MockTransport`` and
asserts DTO parsing, error translation, offset following, and meta refresh.
"""

from __future__ import annotations

import json

import httpx
import pytest

from cloudbot.adapters.cloud_service.engine import EngineAdapter
from cloudbot.adapters.cloud_service import errors as engine_errors
from cloudbot.core.errors import EngineRefused, EngineUnavailable, RateLimited
from cloudbot.domain.value_objects import Limits, WorkspaceId

WS = "ws_01M6HJASCWM9Z1PF755GT89NK4"
RUN = "run_01M6HJASCWM9Z1PF755GT89NK4"

WORKSPACE = {
    "workspace": {
        "id": WS,
        "owner_id": "user_dev",
        "name": "demo",
        "profile": "python",
        "created_at": "2026-11-03T20:41:39Z",
        "status": "active",
        "limits": {
            "cpu_cores": 1,
            "memory_mb": 512,
            "disk_mb": 256,
            "max_wall_time_sec": 300,
            "max_runs": 8,
            "allow_net": True,
            "allow_unlimited_runs": True,
            "allow_tunnels": False,
            "write_rate_mb_s": 0,
        },
        "last_activity_at": "2026-11-03T20:41:39Z",
        "summary": {
            "state": "unlimited",
            "age_sec": 0,
            "created_at": "2026-11-03T20:41:39Z",
            "runs": {"total": 3, "active": 1},
            "disk": {"used_bytes": 2411724, "limit_bytes": 268435456},
            "profile": "python",
        },
    }
}

RUN_BODY = {
    "run": {
        "id": RUN,
        "workspace_id": WS,
        "kind": "command",
        "argv": ["/bin/sh", "-c", "echo hi"],
        "status": "queued",
        "exit_code": None,
        "cwd": "/",
        "timeout_sec": 300,
        "unlimited": False,
        "interpreter": None,
        "started_at": "2026-11-03T20:41:39Z",
        "ended_at": "",
        "logs": {"stdout_bytes": 0, "stderr_bytes": 0, "truncated": False},
    }
}

META = {
    "version": "0.0.20",
    "api_version": "v1",
    "runtimes": ["python3"],
    "interpreters": ["bash", "node", "perl", "python3"],
    "runnable_extensions": [".py", ".js"],
    "profiles": {"default": "python", "profiles": [{"name": "python", "description": "py"}]},
    "limits_defaults": {"cpu_cores": 1, "memory_mb": 512, "disk_mb": 1024, "max_wall_time_sec": 300, "max_runs": 3, "allow_net": False, "allow_unlimited_runs": False, "allow_tunnels": False, "write_rate_mb_s": 0},
    "limits_maxima": {"cpu_cores": 8, "memory_mb": 8192, "disk_mb": 51200, "max_wall_time_sec": 3600, "max_runs": 64, "allow_net": True, "allow_unlimited_runs": False, "allow_tunnels": False, "write_rate_mb_s": 0},
    "archives": {"enabled": True, "limits": {"upload_mb": 50}},
}


def _handler(routes: dict[tuple[str, str], httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "", "details": {}}})

    return handler


def make_adapter(routes):
    transport = httpx.MockTransport(_handler(routes))
    return EngineAdapter.create("http://engine.test", "test-key", transport=transport)


async def test_create_workspace_parses_201():
    adapter = make_adapter(
        {("POST", "/v1/workspaces"): httpx.Response(201, json=WORKSPACE)}
    )
    ws = await adapter.create_workspace(name="demo", profile="python", limits=None)
    assert str(ws.id) == WS
    assert ws.name == "demo"
    assert ws.status.value == "active"
    assert ws.summary.disk_used_bytes == 2411724
    await adapter.aclose()


async def test_list_workspaces():
    adapter = make_adapter(
        {("GET", "/v1/workspaces"): httpx.Response(200, json={"workspaces": [WORKSPACE["workspace"]]})}
    )
    ws_list = await adapter.list_workspaces()
    assert len(ws_list) == 1
    await adapter.aclose()


async def test_run_create_202_and_logs_offset():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/runs"): httpx.Response(202, json=RUN_BODY),
            ("GET", f"/v1/workspaces/{WS}/runs/{RUN}/logs"): httpx.Response(
                200,
                json={"data": "hello\n", "offset": 0, "next": 6, "eof": True, "truncated": False},
            ),
        }
    )
    run = await adapter.create_run(WS, kind="command", argv=["/bin/sh", "-c", "echo hi"])
    assert run.status.value == "queued"
    chunk = await adapter.run_logs(WS, str(run.id), offset=0)
    assert chunk.data == "hello\n"
    assert chunk.next == 6
    assert chunk.eof is True
    await adapter.aclose()


async def test_exec_body_uses_cmd_and_clamps():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 5, "truncated": False},
        )

    transport = httpx.MockTransport(handler)
    adapter = EngineAdapter.create("http://engine.test", "k", transport=transport)
    result = await adapter.exec(WS, ["python3", "main.py"], timeout_sec=90)
    assert captured["body"]["cmd"] == ["python3", "main.py"]
    assert captured["body"]["timeout_sec"] == 60  # clamped
    assert result.exit_code == 0
    await adapter.aclose()


async def test_error_translation_403_forbidden():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}"): httpx.Response(
                403, json={"error": {"code": "forbidden", "message": "nope", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.get_workspace(WorkspaceId(WS))
    assert exc.value.engine_code == "forbidden"
    await adapter.aclose()


async def test_error_translation_404_not_found_distinct_from_403():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}"): httpx.Response(
                404, json={"error": {"code": "not_found", "message": "", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.get_workspace(WorkspaceId(WS))
    assert exc.value.engine_code == "not_found"
    await adapter.aclose()


async def test_error_translation_503_unavailable_preserves_message():
    adapter = make_adapter(
        {
            ("GET", "/v1/meta"): httpx.Response(
                503,
                json={
                    "error": {
                        "code": "unavailable",
                        "message": "sandbox unavailable: nsjail could not set up",
                        "details": {"nsjail": "x"},
                    }
                },
            )
        }
    )
    with pytest.raises(EngineUnavailable) as exc:
        await adapter.meta(refresh=True)
    assert exc.value.retryable is True
    assert "nsjail" in exc.value.cause
    await adapter.aclose()


async def test_error_translation_429_rate_limited():
    body = json.dumps({"error": {"code": "write_rate_exceeded", "message": "", "details": {}}}).encode()
    translated = engine_errors.translate(429, body)
    assert isinstance(translated, RateLimited)


async def test_401_becomes_config_error():
    body = json.dumps({"error": {"code": "unauthorized", "message": "", "details": {}}}).encode()
    translated = engine_errors.translate(401, body)
    assert isinstance(translated, EngineUnavailable)
    assert translated.retryable is False


async def test_meta_refresh_on_unprocessable():
    calls = {"meta": 0, "create": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/meta":
            calls["meta"] += 1
            return httpx.Response(200, json=META)
        if request.url.path == "/v1/workspaces":
            calls["create"] += 1
            if calls["create"] == 1:
                return httpx.Response(
                    422, json={"error": {"code": "unprocessable", "message": "stale", "details": {"field": "disk_mb"}}}
                )
            return httpx.Response(201, json=WORKSPACE)
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    transport = httpx.MockTransport(handler)
    adapter = EngineAdapter.create("http://engine.test", "k", transport=transport)
    await adapter.meta(refresh=True)  # prime
    calls["create"] = 0
    # The adapter's _with_meta_refresh is exercised through a retrying call.
    with pytest.raises(EngineRefused):
        await adapter.create_workspace(name="x", profile=None, limits=Limits())
    assert calls["create"] == 1  # creations are never auto-retried
    await adapter.aclose()


async def test_every_known_code_translates():
    for code in engine_errors.KNOWN_CODES:
        body = json.dumps({"error": {"code": code, "message": "m", "details": {}}}).encode()
        translated = engine_errors.translate(422, body)
        assert translated is not None


async def test_unknown_field_body_does_not_crash():
    adapter = make_adapter(
        {
            ("GET", "/v1/meta"): httpx.Response(200, json={**META, "brand_new_key": {"x": 1}})
        }
    )
    meta = await adapter.meta(refresh=True)
    assert meta.version == "0.0.20"
    await adapter.aclose()


# -- sessions (Phase 1.5, native engine PTY) ---------------------------------
SES = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"

SESSION = {
    "session": {
        "id": SES,
        "workspace_id": WS,
        "shell": "/bin/sh",
        "cwd": "",
        "cols": 80,
        "rows": 24,
        "status": "running",
        "created_at": "2026-11-03T20:41:39Z",
        "last_activity_at": "2026-11-03T20:41:39Z",
        "started_at": "2026-11-03T20:41:39Z",
        "ended_at": "",
        "exit_code": None,
        "output_bytes": 0,
        "output_truncated": False,
    }
}

FAILED_SESSION = {
    "session": {
        "id": SES,
        "workspace_id": WS,
        "status": "failed",
        "reason": "sandbox_unavailable",
        "diagnostic": "sandbox unavailable: PTY allocation failed",
        "cols": 80,
        "rows": 24,
        "output_bytes": 0,
        "output_truncated": False,
    }
}


async def test_open_session_parses_201():
    adapter = make_adapter(
        {("POST", f"/v1/workspaces/{WS}/sessions"): httpx.Response(201, json=SESSION)}
    )
    session = await adapter.open_session(WS, cols=80, rows=24)
    assert session.id == SES
    assert session.status.value == "running"
    assert session.cols == 80
    await adapter.aclose()


async def test_open_session_201_failed_reports_failed_with_diagnostic():
    adapter = make_adapter(
        {("POST", f"/v1/workspaces/{WS}/sessions"): httpx.Response(201, json=FAILED_SESSION)}
    )
    session = await adapter.open_session(WS)
    assert session.status.value == "failed"
    assert session.reason == "sandbox_unavailable"
    # The diagnostic is carried on the entity for DEBUG+admin only.
    assert session.diagnostic.startswith("sandbox unavailable")
    await adapter.aclose()


async def test_open_session_input_resize_signal_bodies():
    captured: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    adapter = EngineAdapter.create("http://engine.test", "k", transport=transport)
    await adapter.session_input(WS, SES, "ls\n")
    await adapter.session_resize(WS, SES, cols=120, rows=40)
    await adapter.session_signal(WS, SES, "INT")
    assert captured == [
        (f"/v1/workspaces/{WS}/sessions/{SES}/input", {"data": "ls\n"}),
        (f"/v1/workspaces/{WS}/sessions/{SES}/resize", {"cols": 120, "rows": 40}),
        (f"/v1/workspaces/{WS}/sessions/{SES}/signal", {"signal": "INT"}),
    ]
    await adapter.aclose()


async def test_session_output_offset_protocol():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}/sessions/{SES}/output"): httpx.Response(
                200,
                json={"data": "$ echo hi\r\nhi\r\n$ ", "offset": 0, "next": 18, "eof": True, "truncated": False},
            )
        }
    )
    chunk = await adapter.session_output(WS, SES, offset=0)
    assert chunk.data.startswith("$ echo hi")
    assert chunk.next == 18
    assert chunk.eof is True
    await adapter.aclose()


async def test_close_session_returns_200_not_204():
    adapter = make_adapter(
        {
            ("DELETE", f"/v1/workspaces/{WS}/sessions/{SES}"): httpx.Response(
                200,
                json={
                    "session": {**SESSION["session"], "status": "killed", "reason": "user_closed"},
                    "changed": True,
                },
            )
        }
    )
    session = await adapter.close_session(WS, SES)
    assert session.status.value == "killed"
    assert session.reason == "user_closed"
    await adapter.aclose()


async def test_read_after_close_is_409_session_ended():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}/sessions/{SES}/output"): httpx.Response(
                409,
                json={
                    "error": {
                        "code": "session_ended",
                        "message": "session has ended",
                        "details": {"status": "killed"},
                    }
                },
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.session_output(WS, SES, offset=0)
    assert exc.value.engine_code == "session_ended"
    assert exc.value.details.get("status") == "killed"
    await adapter.aclose()


async def test_too_many_sessions_is_409_refusal():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/sessions"): httpx.Response(
                409, json={"error": {"code": "too_many_sessions", "message": "", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.open_session(WS)
    assert exc.value.engine_code == "too_many_sessions"
    await adapter.aclose()


async def test_session_needs_interpreter_is_422_refusal():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/sessions"): httpx.Response(
                422,
                json={"error": {"code": "session_needs_interpreter", "message": "", "details": {}}},
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.open_session(WS)
    assert exc.value.engine_code == "session_needs_interpreter"
    await adapter.aclose()


async def test_list_sessions_parses_page():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}/sessions"): httpx.Response(
                200,
                json={"sessions": [SESSION["session"]], "total": 1, "limit": 20, "offset": 0},
            )
        }
    )
    page = await adapter.list_sessions(WS)
    assert page.total == 1
    assert page.sessions[0].id == SES
    assert str(page.sessions[0].workspace_id) == WS
    await adapter.aclose()

