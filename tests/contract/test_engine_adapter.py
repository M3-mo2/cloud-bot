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
    """The contract's sharpest edge: a 503 must carry the engine's own cause.

    INTEGRATION-GUIDE.md:699 — "503 unavailable ... **`message` names the cause.**
    ... Show the engine's `message`." The adapter must not substitute its own
    generic "unavailable" text; the exact message is preserved on ``cause`` for
    the operator log (the user-facing sentence is chosen by presentation).
    """
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
    # Not merely "contains nsjail": the whole engine message survives untouched.
    assert exc.value.cause == "sandbox unavailable: nsjail could not set up"
    await adapter.aclose()


async def test_error_translation_429_rate_limited():
    body = json.dumps({"error": {"code": "write_rate_exceeded", "message": "", "details": {}}}).encode()
    translated = engine_errors.translate(429, body)
    assert isinstance(translated, RateLimited)


def test_bare_504_is_tunnel_url_unavailable_and_retryable():
    """A 504 without an envelope must not be reported as a permanent failure.

    INTEGRATION-GUIDE.md:696 — "| 504 | `tunnel_url_unavailable` | ... | Yes |".
    A gateway/proxy can answer a 504 with an HTML body (no JSON envelope), so the
    status-only path must yield the contract's only 504 code, classified
    retryable — not the locally invented ``timeout`` code the adapter used to
    map 504 to (which read as a permanent failure to any correct client).
    """
    translated = engine_errors.translate(504, b"<html>504 Gateway Time-out</html>")
    assert isinstance(translated, EngineRefused)
    assert translated.engine_code == "tunnel_url_unavailable"
    assert translated.retryable is True


def test_504_with_modelled_envelope_is_retryable_refusal():
    body = json.dumps(
        {
            "error": {
                "code": "tunnel_url_unavailable",
                "message": "cloudflared produced no URL",
                "details": {"reason": "cloudflare 429"},
            }
        }
    ).encode()
    translated = engine_errors.translate(504, body)
    assert isinstance(translated, EngineRefused)
    assert translated.engine_code == "tunnel_url_unavailable"
    assert translated.retryable is True
    # The contract still says the client shows "Could not publish; try again."
    # for this code, but retryability is a separate, honest property.
    assert translated.details.get("reason") == "cloudflare 429"


def test_modelled_timeout_504_is_parsed_defensively_and_retryable():
    """The engine's retained ``timeout`` code is parsed, never conflated with 504.

    SPEC §2.8 / API.md: ``timeout`` is "defined but never emitted". If an older
    engine does emit it, the adapter still recognises it and marks it retryable;
    a bare ``504`` (no envelope) is ``tunnel_url_unavailable`` instead (above).
    """
    body = json.dumps(
        {"error": {"code": "timeout", "message": "deadline", "details": {}}}
    ).encode()
    translated = engine_errors.translate(504, body)
    assert isinstance(translated, EngineRefused)
    assert translated.engine_code == "timeout"
    assert translated.retryable is True
    # And it is a *known* code, so presentation has a sentence for it.
    assert "timeout" in engine_errors.KNOWN_CODES


def test_permanent_refusal_is_not_marked_retryable():
    """Retryability is the contract's column, not a blanket True on 5xx."""
    body = json.dumps(
        {"error": {"code": "tunnels_disabled", "message": "", "details": {}}}
    ).encode()
    translated = engine_errors.translate(422, body)
    assert isinstance(translated, EngineRefused)
    assert translated.retryable is False


async def test_client_side_socket_timeout_is_not_the_server_504():
    """A client socket timeout is a different event from the server's 504.

    INTEGRATION-GUIDE.md:696 models ``504 tunnel_url_unavailable``; a transport
    timeout (``httpx.TimeoutException``) is not an HTTP response at all and must
    stay ``EngineUnavailable`` — never be conflated with the server 504.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("client socket timed out", request=request)

    adapter = EngineAdapter.create(
        "http://engine.test", "k", transport=httpx.MockTransport(handler), max_retries=0
    )
    try:
        with pytest.raises(EngineUnavailable) as exc:
            await adapter.meta(refresh=True)
        assert exc.value.retryable is True
        # It is a transport failure, not a 504 refusal.
        assert "tunnel_url_unavailable" not in str(exc.value.cause)
    finally:
        await adapter.aclose()


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
    calls["meta"] = 0
    calls["create"] = 0
    # A creation POST is not idempotent, so a stale-number 422 is surfaced (it
    # could create twice if retried) — but the adapter still re-reads ``/v1/meta``
    # so the next attempt's caps are right (INTEGRATION-GUIDE.md:29-32).
    with pytest.raises(EngineRefused):
        await adapter.create_workspace(name="x", profile=None, limits=Limits())
    assert calls["create"] == 1  # creations are never auto-retried
    assert calls["meta"] == 1  # but meta was refreshed, not left stale
    await adapter.aclose()


async def test_patch_workspace_refreshes_meta_and_retries_once_on_422():
    """The meta-refresh requirement is wired, and this fails without the wiring.

    INTEGRATION-GUIDE.md:29-32 requires re-reading ``/v1/meta`` after a ``422``
    that names a stale cached number, then retrying. ``PATCH`` is idempotent, so
    the adapter does exactly that: refresh meta, retry once, and return the
    successful result. Removing ``_with_meta_refresh`` from ``patch_workspace``
    makes the first ``422`` surface, so this test goes red.
    """
    calls = {"meta": 0, "patch": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/meta":
            calls["meta"] += 1
            return httpx.Response(200, json=META)
        if request.url.path == f"/v1/workspaces/{WS}" and request.method == "PATCH":
            calls["patch"] += 1
            if calls["patch"] == 1:
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "unprocessable",
                            "message": "disk_mb above maximum",
                            "details": {"field": "disk_mb"},
                        }
                    },
                )
            return httpx.Response(200, json=WORKSPACE)
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    adapter = EngineAdapter.create(
        "http://engine.test", "k", transport=httpx.MockTransport(handler)
    )
    await adapter.meta(refresh=True)  # prime the cache: one meta call
    calls["meta"] = 0
    ws = await adapter.patch_workspace(WorkspaceId(WS), limits=Limits(disk_mb=2048))
    assert str(ws.id) == WS
    # The stale refusal was resolved by refreshing meta exactly once and retrying.
    assert calls["meta"] == 1
    assert calls["patch"] == 2
    await adapter.aclose()


async def test_patch_workspace_surfaces_a_persistent_422_after_one_retry():
    """One retry only: a genuinely bad request is still a visible refusal."""
    calls = {"patch": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/meta":
            return httpx.Response(200, json=META)
        calls["patch"] += 1
        return httpx.Response(
            422,
            json={"error": {"code": "unprocessable", "message": "still bad", "details": {"field": "disk_mb"}}},
        )

    adapter = EngineAdapter.create(
        "http://engine.test", "k", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.patch_workspace(WorkspaceId(WS), limits=Limits(disk_mb=2048))
    assert exc.value.engine_code == "unprocessable"
    assert calls["patch"] == 2  # original + exactly one retry
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


# -- files: stat carries the integrity/type fields ---------------------------
STAT_FILE = {
    "name": "main.py",
    "path": "/main.py",
    "type": "file",
    "size": 12,
    "mode": "0644",
    "mod_time": "2026-01-02T15:05:05Z",
    "content_type": "text/x-python; charset=utf-8",
    "sha256": "0ca9" + "a" * 60,
    "binary": False,
    "runnable": "python3",
}


async def test_stat_file_surfaces_sha256_content_type_and_binary():
    """A file's integrity must be verifiable from ``stat``, not only a read.

    API.md L1316-1328: ``files/stat`` returns ``content_type``, ``sha256`` and
    ``binary`` for a regular file. The adapter used to rebuild a five-field DTO
    and throw all three away, so nothing could confirm a file's hash without
    reading its bytes. They must reach the domain entry.
    """
    adapter = make_adapter(
        {("GET", f"/v1/workspaces/{WS}/files/stat"): httpx.Response(200, json=STAT_FILE)}
    )
    entry = await adapter.stat_file(WorkspaceId(WS), "/main.py")
    assert entry.path == "/main.py"
    assert entry.name == "main.py"
    assert entry.type == "file"
    assert entry.size == 12
    assert entry.sha256 == STAT_FILE["sha256"]
    assert entry.content_type == "text/x-python; charset=utf-8"
    assert entry.binary is False
    await adapter.aclose()


async def test_stat_file_binary_flag_is_not_dropped():
    binary = {**STAT_FILE, "name": "logo.png", "path": "/logo.png",
              "content_type": "image/png", "binary": True}
    adapter = make_adapter(
        {("GET", f"/v1/workspaces/{WS}/files/stat"): httpx.Response(200, json=binary)}
    )
    entry = await adapter.stat_file(WorkspaceId(WS), "/logo.png")
    assert entry.binary is True
    assert entry.content_type == "image/png"
    await adapter.aclose()


async def test_file_listing_dto_defaults_stay_empty():
    """A listing omits the stat-only fields; they must not fabricate values."""
    listing = {
        "path": "/",
        "entries": [
            {"name": "main.py", "path": "/main.py", "type": "file", "size": 12,
             "mode": "0644", "mod_time": "2026-01-02T15:05:05Z", "runnable": "python3"}
        ],
        "count": 1,
        "total": 1,
        "truncated": False,
    }
    adapter = make_adapter(
        {("GET", f"/v1/workspaces/{WS}/files"): httpx.Response(200, json=listing)}
    )
    page = await adapter.list_files(WorkspaceId(WS), "/")
    entry = page.entries[0]
    assert entry.sha256 == ""
    assert entry.content_type == ""
    assert entry.binary is False
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


# -- packages (Phase 2a) -----------------------------------------------------
PACKAGES = {
    "packages": [{"name": "six", "version": "1.16.0"}],
    "count": 1,
    "truncated": False,
    "shared_enabled": True,
    "shared_available": False,
    "shared_path": "",
    "private_path": "/opt/python/private",
}

INSTALL_OK = {
    "duration_ms": 10000,
    "exit_code": 0,
    "output": "Successfully installed six\n",
    "stdout": "Successfully installed six\n",
    "timed_out": False,
    "truncated": False,
    "target": "/opt/python/private",
}

INSTALL_FAILED = {
    "duration_ms": 1200,
    "exit_code": 1,
    "output": "ERROR: Could not find a version that satisfies the requirement six\n",
    "stdout": "",
    "timed_out": False,
    "truncated": False,
    "target": "/opt/python/private",
}


async def test_list_packages_parses_envelope():
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/packages"): httpx.Response(200, json=PACKAGES)})
    pkg_list = await adapter.list_packages(WS)
    assert pkg_list.count == 1
    assert pkg_list.packages[0].name == "six"
    assert pkg_list.shared_enabled is True
    assert pkg_list.private_path == "/opt/python/private"
    await adapter.aclose()


async def test_install_packages_parses_200_and_reports_exit_code():
    adapter = make_adapter(
        {("POST", f"/v1/workspaces/{WS}/packages/install"): httpx.Response(200, json=INSTALL_FAILED)}
    )
    result = await adapter.install_packages(WS, ["six"])
    # A 200 with exit_code 1 is a FAILED install; the adapter keeps the code.
    assert result.exit_code == 1
    assert result.ok is False
    assert "Could not find a version" in result.output
    await adapter.aclose()


async def test_install_packages_sends_packages_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=INSTALL_OK)

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    result = await adapter.install_packages(WS, ["six"])
    assert captured["body"] == {"packages": ["six"]}
    assert result.exit_code == 0
    await adapter.aclose()


async def test_remove_package_and_all_use_query_params():
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, request.url.query.decode()))
        return httpx.Response(204)

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    await adapter.remove_package(WS, "six")
    await adapter.remove_all_packages(WS)
    assert captured == [
        (f"/v1/workspaces/{WS}/packages", "name=six"),
        (f"/v1/workspaces/{WS}/packages", "all=true"),
    ]
    await adapter.aclose()


# -- tunnels (Phase 2b) ------------------------------------------------------
TUNNEL = {
    "id": "tnl_01M6HJASCWM9Z1PF755GT89NK4",
    "workspace_id": WS,
    "port": 8080,
    "url": "https://example-abc.trycloudflare.com",
    "status": "active",
    "created_at": "2026-11-03T20:41:39Z",
}


async def test_create_tunnel_parses_201():
    adapter = make_adapter(
        {("POST", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(201, json={"tunnel": TUNNEL})}
    )
    tunnel = await adapter.create_tunnel(WS, 8080)
    assert tunnel.port == 8080
    assert tunnel.url.startswith("https://")
    await adapter.aclose()


async def test_list_tunnels_parses_envelope():
    adapter = make_adapter(
        {("GET", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(200, json={"tunnels": [TUNNEL]})}
    )
    tunnels = await adapter.list_tunnels(WS)
    assert tunnels.count == 1
    assert tunnels.tunnels[0].id == TUNNEL["id"]
    await adapter.aclose()


async def test_tunnels_disabled_422_carries_details():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(
                422,
                json={
                    "error": {
                        "code": "tunnels_disabled",
                        "message": "tunnels are not permitted for this workspace",
                        "details": {"config": "limits.allow_tunnels", "field": "allow_tunnels"},
                    }
                },
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.create_tunnel(WS, 8080)
    assert exc.value.engine_code == "tunnels_disabled"
    assert exc.value.details["config"] == "limits.allow_tunnels"
    assert exc.value.details["field"] == "allow_tunnels"
    await adapter.aclose()


async def test_too_many_tunnels_422():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(
                422, json={"error": {"code": "too_many_tunnels", "message": "", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.create_tunnel(WS, 8080)
    assert exc.value.engine_code == "too_many_tunnels"
    await adapter.aclose()


async def test_tunnel_url_unavailable_504_is_refusal_not_retry():
    """A 504 with a modelled code is a ``top-level`` refusal, never a blind retry.

    The adapter retries idempotent verbs on 504, but a creation POST is never
    retried, so the caller sees the documented ``tunnel_url_unavailable``. The
    refusal is still classified **retryable** per INTEGRATION-GUIDE.md:696 ("Retry?
    Yes"): the client must not call it permanent, and the tunnel screen offers
    Retry on the same port.
    """
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(
                504,
                json={
                    "error": {
                        "code": "tunnel_url_unavailable",
                        "message": "cloudflared produced no URL",
                        "details": {"reason": "cloudflare 429"},
                    }
                },
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.create_tunnel(WS, 8080)
    assert exc.value.engine_code == "tunnel_url_unavailable"
    assert exc.value.details.get("reason") == "cloudflare 429"
    assert exc.value.retryable is True
    await adapter.aclose()


async def test_create_tunnel_bare_gateway_504_is_retryable_tunnel_code():
    """A proxy 504 with an HTML body reaches the tunnel screen, not a dead end.

    The real regression path: ``POST /tunnels`` gets a bare ``504`` (no JSON
    envelope), the adapter must still name the contract's ``tunnel_url_unavailable``
    so the tunnel handler renders its dedicated retry-on-the-same-port screen.
    Classifying it as a locally invented ``timeout`` code bypassed that path.
    """
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/tunnels"): httpx.Response(
                504, content=b"<html>504 Gateway Time-out</html>"
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.create_tunnel(WS, 8080)
    assert exc.value.engine_code == "tunnel_url_unavailable"
    assert exc.value.retryable is True
    await adapter.aclose()


async def test_delete_tunnel_204():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(204)

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    await adapter.delete_tunnel(WS, TUNNEL["id"])
    assert calls == ["DELETE"]
    await adapter.aclose()


# -- ports (Phase 2c) --------------------------------------------------------
PORTS = {
    "workspace_id": WS,
    "ports": [
        {
            "port": 18090,
            "protocol": "tcp",
            "address": "0.0.0.0",
            "scope": "wildcard",
            "state": "LISTEN",
            "attributed": True,
            "pid": 890648,
            "kind": "run",
            "label": "python3 -c ...",
            "argv": ["/bin/python3", "-c", "..."],
            "owners": [890648],
            "inode": 2726317911,
        },
        {
            "port": 5353,
            "protocol": "udp",
            "address": "0.0.0.0",
            "scope": "wildcard",
            "state": "",
            "attributed": False,
        },
    ],
    "unix": [
        {
            "path": "/tmp/demo.sock",
            "type": "stream",
            "state": "unconnected",
            "attributed": True,
            "pid": 890654,
            "kind": "run",
        }
    ],
    "count": 2,
    "unix_count": 1,
    "complete": True,
    "notes": [],
    "pids_scanned": 2,
    "fds_scanned": 8,
    "reachability": "ports are listening inside the workspace's private network namespace; ...",
}


async def test_list_ports_parses_report():
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/ports"): httpx.Response(200, json=PORTS)})
    report = await adapter.list_ports(WS)
    assert report.count == 2
    assert report.ports[0].port == 18090
    assert report.ports[0].state == "LISTEN"
    # UDP state is the empty string, not invented.
    assert report.ports[1].state == ""
    assert report.ports[1].attributed is False
    assert report.unix_count == 1
    assert report.reachability.startswith("ports are listening")
    await adapter.aclose()


async def test_list_ports_partial_flags_complete_false():
    body = {**PORTS, "complete": False, "notes": ["pids_scanned reached max_pids=64"]}
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/ports"): httpx.Response(200, json=body)})
    report = await adapter.list_ports(WS)
    assert report.complete is False
    assert report.notes
    await adapter.aclose()


async def test_ports_disabled_404():
    adapter = make_adapter(
        {
            ("GET", f"/v1/workspaces/{WS}/ports"): httpx.Response(
                404, json={"error": {"code": "ports_disabled", "message": "", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.list_ports(WS)
    assert exc.value.engine_code == "ports_disabled"
    await adapter.aclose()


# -- metrics (Phase 2d) ------------------------------------------------------
METRICS = {
    "timestamp": "2026-11-04T16:06:07Z",
    "interval_seconds": 3,
    "workspaces": [
        {
            "workspace_id": WS,
            "ram_used_bytes": 0,
            "ram_limit_bytes": 536870912,
            "ram_percent": 0,
            "ram_complete": True,
            "disk_used_bytes": 6089,
            "disk_limit_bytes": 1073741824,
            "disk_percent": 0.0006,
            "disk_complete": True,
            "write_rate_limit_mb_s": 0,
            "live_runs": 0,
            "live_cgroups": 0,
        }
    ],
    "sampler": {"ticks": 4, "cgroup_reads": 0, "statfs_reads": 6,
                "tree_walk_reads": 0, "subscribers": 0, "running": 0,
                "dropped_frames": 0, "candidates": 1},
}


async def test_workspace_metrics_parses_frame():
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/metrics"): httpx.Response(200, json=METRICS)})
    metrics = await adapter.workspace_metrics(WS)
    assert metrics.workspace_id == WS
    assert metrics.interval_seconds == 3
    assert metrics.ram_used_bytes == 0
    assert metrics.ram_complete is True
    assert metrics.disk_used_bytes == 6089
    assert metrics.disk_complete is True
    await adapter.aclose()


async def test_workspace_metrics_honours_incomplete_flag():
    body = {
        **METRICS,
        "workspaces": [{**METRICS["workspaces"][0], "disk_used_bytes": 0, "disk_complete": False}],
    }
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/metrics"): httpx.Response(200, json=body)})
    metrics = await adapter.workspace_metrics(WS)
    # The honesty flag is carried through untouched; a false figure is not a zero.
    assert metrics.disk_complete is False
    assert metrics.ram_complete is True
    await adapter.aclose()


# -- processes (Phase 2e) ----------------------------------------------------
PROCESSES = {
    "processes": [
        {
            "id": "proc_0123456789abcdef",
            "pid": 179173,
            "ppid": 179172,
            "argv": ["python3", "bot.py"],
            "kind": "session",
            "source_id": "ses_01M6HJATZTFXTWXWBG8A5KWYS8",
            "started_at": "2026-11-04T16:06:07Z",
        },
        {
            "id": "proc_fedcba9876543210",
            "pid": 179200,
            "ppid": 1,
            "argv": ["sleep", "600"],
            "kind": "detached",
            "source_id": "",
            "started_at": "2026-11-04T16:06:08Z",
        },
    ],
    "count": 2,
}


async def test_list_processes_parses_inventory():
    adapter = make_adapter({("GET", f"/v1/workspaces/{WS}/processes"): httpx.Response(200, json=PROCESSES)})
    pl = await adapter.list_processes(WS)
    assert pl.count == 2
    assert pl.processes[0].id == "proc_0123456789abcdef"
    assert pl.processes[0].kind == "session"
    # A detached process is reported, never hidden.
    assert pl.processes[1].kind == "detached"
    assert pl.processes[1].source_id == ""
    await adapter.aclose()


async def test_list_processes_empty_is_200_with_positive_control():
    """An empty inventory is an honest 200 — proven, not inferred from a blank."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"processes": [], "count": 0})

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    pl = await adapter.list_processes(WS)
    # Positive control: the call actually happened and answered 200.
    assert calls == [f"/v1/workspaces/{WS}/processes"]
    assert pl.count == 0
    assert pl.processes == ()
    await adapter.aclose()


async def test_stop_process_parses_stopped():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/processes/proc_0123456789abcdef/stop"): httpx.Response(
                200,
                json={"process_id": "proc_0123456789abcdef", "status": "stopped",
                      "stopped": True, "stopped_at": "2026-11-04T16:06:09Z"},
            )
        }
    )
    result = await adapter.stop_process(WS, "proc_0123456789abcdef")
    assert result.status == "stopped"
    assert result.stopped is True
    assert result.gone is False
    await adapter.aclose()


async def test_stop_process_gone_and_repeat_404():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/processes/proc_1111111111111111/stop"): httpx.Response(
                200,
                json={"process_id": "proc_1111111111111111", "status": "gone",
                      "stopped": False, "stopped_at": "2026-11-04T16:06:09Z"},
            ),
            ("POST", f"/v1/workspaces/{WS}/processes/proc_2222222222222222/stop"): httpx.Response(
                404, json={"error": {"code": "not_found", "message": "", "details": {}}}
            ),
        }
    )
    gone = await adapter.stop_process(WS, "proc_1111111111111111")
    assert gone.status == "gone"
    assert gone.stopped is False
    assert gone.gone is True
    # A second stop is a 404 because the process left the inventory.
    with pytest.raises(EngineRefused) as exc:
        await adapter.stop_process(WS, "proc_2222222222222222")
    assert exc.value.engine_code == "not_found"
    await adapter.aclose()


# -- archives (Phase 2g) -----------------------------------------------------
ARCHIVE_RESULT = {
    "format": "zip",
    "destination": "/",
    "entries_total": 5,
    "entries_extracted": 4,
    "entries_skipped": 1,
    "entries_rejected": 0,
    "bytes_written": 51,
    "dry_run": False,
    "entries": [
        {"name": "src/main.py", "path": "/src/main.py", "type": "file", "size": 15,
         "mode": "-rw-r--r--", "rejected": False, "skipped": False},
    ],
}


async def test_upload_and_extract_is_multipart_and_parses_result():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json=ARCHIVE_RESULT)

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    result = await adapter.upload_and_extract(
        WS, filename="project.zip", data=b"PK\x03\x04payload", path="/dst", dry_run=True
    )
    # The upload is multipart — never the JSON helper.
    assert captured["content_type"].startswith("multipart/form-data")
    assert b'name="archive"' in captured["body"]
    assert b'name="path"' in captured["body"]
    assert b"project.zip" in captured["body"]
    assert b'name="dry_run"' in captured["body"]
    assert result.format == "zip"
    assert result.entries_total == 5
    assert result.entries_extracted == 4
    assert result.entries_skipped == 1
    await adapter.aclose()


async def test_extract_archive_sends_json_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=ARCHIVE_RESULT)

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    await adapter.extract_archive(WS, path="/bundle.zip", dest="/out", dry_run=True)
    assert captured["body"] == {"path": "/bundle.zip", "dest": "/out",
                                "overwrite": False, "dry_run": True}
    await adapter.aclose()


async def test_export_archive_reads_raw_bytes_not_json():
    zip_bytes = b"PK\x03\x04\x14\x00\x00\x00binaryzipcontent"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, content=zip_bytes, headers={"Content-Type": "application/zip"}
        )

    adapter = EngineAdapter.create("http://engine.test", "k", transport=httpx.MockTransport(handler))
    data = await adapter.export_archive(WS, "/project")
    assert calls == [f"/v1/workspaces/{WS}/files/archive"]
    # The bytes survive untouched — never parsed as JSON.
    assert data == zip_bytes
    await adapter.aclose()


@pytest.mark.parametrize(
    "status,code",
    [
        (409, "archive_entry_exists"),
        (413, "archive_too_large"),
        (413, "archive_too_many_entries"),
        (413, "archive_ratio_exceeded"),
        (413, "payload_too_large"),
        (415, "unsupported_archive"),
        (422, "archive_encrypted"),
        (422, "archive_multivolume"),
        (422, "archive_unsafe_entry"),
        (422, "unprocessable"),
        (507, "insufficient_storage"),
    ],
)
async def test_every_archive_refusal_code_translates(status, code):
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/files/extract"): httpx.Response(
                status, json={"error": {"code": code, "message": "m", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.extract_archive(WS, path="/x.zip")
    assert exc.value.engine_code == code
    await adapter.aclose()


async def test_extract_missing_source_is_404_not_found():
    adapter = make_adapter(
        {
            ("POST", f"/v1/workspaces/{WS}/files/extract"): httpx.Response(
                404, json={"error": {"code": "not_found", "message": "file not found", "details": {}}}
            )
        }
    )
    with pytest.raises(EngineRefused) as exc:
        await adapter.extract_archive(WS, path="/nope.txt")
    assert exc.value.engine_code == "not_found"
    await adapter.aclose()

