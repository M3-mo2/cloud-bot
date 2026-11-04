# Cloud Service Client (Engine Adapter)

This document specifies the **only** part of the bot that speaks HTTP: the
`adapters/cloud_service/` package. It implements `domain/ports/engine.py` and is
the single point where Cloud Service (sandboxd, v0.0.20, API prefix `/v1`) wire
formats exist. It is authoritative against
`docs-cloud-service/INTEGRATION-GUIDE.md`; when in doubt, that guide wins.

---

## 1. Responsibilities

1. Authenticate every request with `Authorization: Bearer <service-api-key>`.
2. Map domain calls to HTTP requests and responses to **domain entities**.
3. Translate HTTP failures into typed `domain.errors` (`EngineRefused`,
   `EngineUnavailable`) — never leak raw bodies upward.
4. Cache and refresh `/v1/meta`.
5. Retry transient failures safely (idempotent requests only).
6. Never expose DTOs, JSON, HTTP status codes, or engine messages to
   `presentation` or `handlers`.

---

## 2. Transport and lifecycle

```python
class EngineAdapter(EnginePort):
    def __init__(self, base_url: str, api_key: SecretStr, *,
                 timeout_s: float, max_retries: int, clock: Clock): ...
    async def aclose(self) -> None: ...

    _client: httpx.AsyncClient   # base_url, Authorization, Accept: application/json
```

- One `httpx.AsyncClient` for the process lifetime (connection pooling), created
  in `bootstrap.container` and closed in `lifecycle.shutdown()`.
- Default headers: `Authorization: Bearer <key>`, `Accept: application/json`.
- Bodies are JSON (`Content-Type: application/json`) unless multipart (upload) or
  raw (download).
- File paths in URLs are passed as **query parameters**, never interpolated into
  the path, to avoid escaping mistakes.

### Timeouts

| Operation | Timeout |
| --- | --- |
| Meta, get/list, files, packages | `engine_timeout_s` (default 30) |
| `exec` | `exec_timeout_s + 5` (exec is hard-capped at 60 s) |
| `run_logs`, session `output` | short (10 s); polled in a loop |
| upload / download | streaming; read timeout per chunk |

### Retries

- Retry **idempotent** verbs (`GET`, `PUT`, `PATCH`, `DELETE`) on:
  network errors, `502`, `503`, `504`, and `429`.
- **Never auto-retry** `POST /workspaces`, `POST /runs`, `POST /exec`,
  `POST /tunnels`, `POST /sessions` (creation is not idempotent); surface the
  failure and let the user retry explicitly.
- Backoff: exponential with jitter, `max_retries` attempts, bounded by an overall
  deadline. `Retry-After` honored when present.
- The engine carries **no rate limiting** (SPEC §2.6.1), so the bot's own
  throttle and these retries are the only pacing.

---

## 3. `/v1/meta` — the source of truth

Read at startup and cached. **Re-read whenever a request is refused with `422`
or `409` in a way that suggests a stale cached value** (INTEGRATION-GUIDE §1).

```python
@dataclass(frozen=True)
class EngineMeta:
    version: str
    api_version: str                 # assert == "v1"
    runtimes: list[str]
    interpreters: list[str]
    runnable_extensions: list[str]
    profiles: Profiles
    limits_defaults: Limits
    limits_maxima: Limits
    network: Network
    ports: Ports
    metrics: Metrics
    archives: Archives
    subscriptions: Subscriptions
    tunnels: Tunnels
    workspace_limits: WorkspaceLimits
    write_rate: WriteRate
    egress_decoy: Capability
    identity_masking: Capability
    provider_tools_hidden: Capability
```

What the bot derives from meta (never hardcodes):

- **Profile picker** and default profile (`profiles.default`, `profiles.profiles[]`).
- **Limit sliders/caps** (`limits_defaults`, `limits_maxima`) — a request above a
  maximum is refused `422`, never clamped, so the UI must not offer it.
- **Interpreter/run extensions** (`interpreters`, `runnable_extensions`) for run
  choices and for trusting a file's `runnable` field.
- **Upload cap** (`archives.limits.upload_mb`).
- **Subscription grammar** (`subscriptions.default_duration`, `max_duration`).
- **Feature gates** (`network.enabled`, `ports.enabled`, `metrics.enabled`,
  `tunnels.enabled`) to hide features that are off.
- **version/api_version** for support and an assertion at startup.

The cache object exposes `meta.refresh()` and is guarded by an async lock so
concurrent refreshes collapse into one.

---

## 4. Domain operation → HTTP mapping

All paths are prefixed `/v1`. `{ws}` is a workspace id.

### 4.1 Meta / health

| Domain | HTTP |
| --- | --- |
| `meta(refresh)` | `GET /v1/meta` |
| `health()` | `GET /healthz` |
| `ready()` | `GET /readyz` |

### 4.2 Workspaces

| Domain | HTTP | Notes |
| --- | --- | --- |
| `create_workspace` | `POST /v1/workspaces` | body `{name, profile?, limits?, subscription?}`; `201 {workspace}` |
| `list_workspaces` | `GET /v1/workspaces` | `{workspaces:[...]}`; each item carries `summary` |
| `get_workspace` | `GET /v1/workspaces/{ws}` | `{workspace}` |
| `patch_workspace` | `PATCH /v1/workspaces/{ws}` | name/limits; absent or `null` keeps current |
| `suspend_workspace` | `POST /v1/workspaces/{ws}/suspend` | `{workspace, changed, stopped_run_ids, ...}` |
| `resume_workspace` | `POST /v1/workspaces/{ws}/resume` | `{workspace, changed}` |
| `stop_workspace` | `POST /v1/workspaces/{ws}/stop` | `{workspace, stopped_run_*, stopped_session_*, stopped_process_*}` |
| `delete_workspace` | `DELETE /v1/workspaces/{ws}` | `204` |
| `workspace_usage` | `GET /v1/workspaces/{ws}/usage` | `ResourceUsage` |

`profile` is **top-level**, not inside `limits` (INTEGRATION-GUIDE §2.1). `limits`
must contain only fields the engine accepts; `allow_unlimited_runs`/`allow_tunnels`
are boolean opt-ins.

### 4.3 Files

| Domain | HTTP |
| --- | --- |
| `list_files` | `GET /v1/workspaces/{ws}/files?path=&offset=&limit=` |
| `stat_file` | `GET /v1/workspaces/{ws}/files/stat?path=` |
| `read_file` | `GET /v1/workspaces/{ws}/files/content?path=&meta=&encoding=&offset=&limit=&start_line=&end_line=` |
| `download_file` | `GET /v1/workspaces/{ws}/files/download?path=` |
| `write_file` | `PUT /v1/workspaces/{ws}/files/content?path=` body `{content, encoding}` |
| `delete_file` | `DELETE /v1/workspaces/{ws}/files?path=` |
| `mkdir` | `POST /v1/workspaces/{ws}/files/mkdir` body `{path}` |
| `rename` | `POST /v1/workspaces/{ws}/files/rename` body `{from, to}` |
| `upload_file` | `POST /v1/workspaces/{ws}/files/upload` multipart `path`+`file` |

Reads use `?meta=true` when the UI wants `size/sha256/binary/content_type`. Raw
downloads are never parsed as JSON.

### 4.4 Exec / Runs

| Domain | HTTP |
| --- | --- |
| `exec` | `POST /v1/workspaces/{ws}/exec` body `{cmd, cwd, env, stdin, timeout_sec}` |
| `create_run` | `POST /v1/workspaces/{ws}/runs` body `{kind, file\|argv, args, cwd, env, interpreter, timeout_sec, no_timeout}` |
| `list_runs` | `GET /v1/workspaces/{ws}/runs?status=&kind=&limit=&offset=` |
| `get_run` | `GET /v1/workspaces/{ws}/runs/{run}` |
| `run_logs` | `GET /v1/workspaces/{ws}/runs/{run}/logs?stream=&offset=&limit=` |
| `stop_run` | `POST /v1/workspaces/{ws}/runs/{run}/stop` |
| `purge_runs` | `DELETE /v1/workspaces/{ws}/runs` |
| `delete_run` | `DELETE /v1/workspaces/{ws}/runs/{run}` |

**Critical (INTEGRATION-GUIDE §4.2):** the run object contains **no**
`stdout`/`stderr`. Output is always read from the logs endpoint by byte offset:
`{data, offset, next, eof, truncated}`. `next` is the offset to request next;
`eof` means "read everything so far" (the run may still be running); `truncated`
means the engine's log cap dropped older bytes. `kind=file` and `argv` are
mutually exclusive (`422` if both).

### 4.5 Sessions (Phase 1.5)

| Domain | HTTP |
| --- | --- |
| `open_session` | `POST /v1/workspaces/{ws}/sessions` |
| `list_sessions` | `GET /v1/workspaces/{ws}/sessions?status=&limit=&offset=` |
| `get_session` | `GET /v1/workspaces/{ws}/sessions/{ses}` |
| `session_input` | `POST /v1/workspaces/{ws}/sessions/{ses}/input` body `{data}` |
| `session_output` | `GET /v1/workspaces/{ws}/sessions/{ses}/output?offset=&limit=` |
| `session_resize` | `POST /v1/workspaces/{ws}/sessions/{ses}/resize` body `{cols, rows}` |
| `session_signal` | `POST /v1/workspaces/{ws}/sessions/{ses}/signal` body `{signal}` |
| `close_session` | `DELETE /v1/workspaces/{ws}/sessions/{ses}` |

Signals limited to `INT|TERM|QUIT|HUP`. Output uses the same offset protocol as
run logs. `close_session` returns `200` (not `204`) with `{session, changed}`;
after a close, a read reports `409 session_ended`. These calls are implemented in
`adapters/cloud_service/engine.py`; the terminal itself lives in
`services/sessions.py` + `presentation/screens/terminal.py`.

### 4.6 Packages, tunnels, ports (Phase 2a/2b/2c)

| Domain | HTTP |
| --- | --- |
| `list_packages` | `GET /v1/workspaces/{ws}/packages` |
| `install_packages` | `POST /v1/workspaces/{ws}/packages/install` body `{packages:[...]}` |
| `remove_package` | `DELETE /v1/workspaces/{ws}/packages?name=` |
| `remove_all_packages` | `DELETE /v1/workspaces/{ws}/packages?all=true` |
| `list_tunnels` | `GET /v1/workspaces/{ws}/tunnels` |
| `create_tunnel` | `POST /v1/workspaces/{ws}/tunnels` body `{port}` |
| `delete_tunnel` | `DELETE /v1/workspaces/{ws}/tunnels/{id}` |
| `list_ports` | `GET /v1/workspaces/{ws}/ports` |

An install returns `200` with `{exit_code, output, stdout, timed_out, truncated,
duration_ms, target}`; **a non-zero `exit_code` is a failed install** and the
`InstallResult` keeps the code so the screen can render it honestly. A tunnel
create returns `201` with `{tunnel:{id, workspace_id, port, url, status,
created_at}}`; the `url` is **ephemeral** and is never persisted or rendered as
text (a `LINK` button only). Ports are a read-only report carrying a verbatim
`reachability` sentence and a `complete` flag. Implemented in
`adapters/cloud_service/engine.py`; the use-cases live in `services/packages.py`,
`services/tunnels.py`, `services/ports.py` and the screens in
`presentation/screens/{packages,tunnels,ports}.py`.

Metrics (`/metrics`, `/metrics/stream`) and processes (`/processes`) remain for
later phases; same adapter pattern, sequenced in [`../ROADMAP.md`](../ROADMAP.md).

---

## 5. DTO → domain mapping

`dto.py` mirrors the API with pydantic models (`extra="ignore"` so new engine
fields never crash the bot). `mapping.py` converts DTOs to frozen domain entities
and centralizes nil-handling (engine uses `""` and `null` in places).

Examples:

```
DTO WorkspaceDTO          → Workspace(id, owner_tg_id=??, name, profile, status,
                                      limits, summary, created_at, last_activity_at)
```

Note: `owner_tg_id` is **not** the engine's `owner_id` (that is the bot's single
principal). The workspace entity gets its owner from the `UserRepository`,
attached in the service layer after the ownership check.

```
DTO RunDTO.status str     → RunStatus(str)   # validated against the enum
DTO summary.state         → WorkspaceStatus / summary.state
```

Enum members are validated: an unknown `status` from a newer engine version maps
to a safe `Unknown` member rather than crashing (logged), and the UI renders a
generic "in progress" label. This is the future-proofing rule.

---

## 6. Error translation

`adapters/cloud_service/errors.py` converts any non-2xx response into a typed
domain error, using the envelope `{"error":{"code","message","details"}}`:

```python
def translate(status: int, body: bytes, *, path: str) -> DomainError:
    # parse envelope if present; fall back to a synthetic code by status
    ...
```

Mapping (from INTEGRATION-GUIDE §9.3 / SPEC §2.8):

| Engine code(s) | HTTP | Domain error | UI meaning |
| --- | --- | --- | --- |
| `validation` | 400/405 | `EngineRefused("validation")` | "Something in the request was invalid." |
| `unauthorized` | 401 | `EngineUnavailable(retryable=False, cause="auth")` | Internal config error; operator alert. |
| `forbidden` | 403 | `EngineRefused("forbidden")` | "That resource is not yours." |
| `subscription_expired` | 403 | `EngineRefused("subscription_expired")` | "Your plan expired; renew to continue." |
| `not_found` | 404 | `EngineRefused("not_found")` | "That item no longer exists." |
| `ports_disabled` | 404 | `EngineRefused("ports_disabled")` | Hide feature. |
| `conflict` | 409 | `EngineRefused("conflict")` | "That is not possible right now." |
| `workspace_suspended` | 409 | `EngineRefused("workspace_suspended")` | "This workspace is paused." |
| `too_many_sessions` | 409 | `EngineRefused("too_many_sessions")` | "Close a terminal first." |
| `session_ended` | 409 | `EngineRefused("session_ended")` | "That terminal has closed." |
| `archive_entry_exists` | 409 | `EngineRefused("archive_entry_exists")` | Offered overwrite flow. |
| `payload_too_large` | 413 | `EngineRefused("payload_too_large")` | "That file is too large." |
| `archive_too_large` / `_too_many_entries` / `_ratio_exceeded` | 413 | `EngineRefused(code)` | Archive too large. |
| `unsupported_archive` | 415 | `EngineRefused("unsupported_archive")` | "Not a zip or rar." |
| `unprocessable` | 422 | `EngineRefused("unprocessable", details)` | Read message/details; usually a stale cap → refresh meta. |
| `unknown_profile` | 422 | `EngineRefused("unknown_profile")` | "Pick one of the offered profiles." |
| `not_directory` | 422 | `EngineRefused("not_directory")` | Read message. |
| `session_needs_interpreter` | 422 | `EngineRefused(code)` | "Terminals need the python profile." |
| `subscription_invalid` / `max_duration_exceeded` / `subscription_would_shorten` | 422 | `EngineRefused(code)` | Term issues. |
| `tunnels_disabled` / `too_many_tunnels` | 422 | `EngineRefused(code)` | Tunnel issues. |
| `archive_encrypted` / `archive_multivolume` / `archive_unsafe_entry` | 422 | `EngineRefused(code)` | Archive issues. |
| `write_rate_exceeded` | 429 | `RateLimited(retry_after)` | "Slow down; retry shortly." |
| `internal` | 500 | `EngineUnavailable(retryable=True)` | "Something went wrong; try again." |
| `unavailable` | 503 | `EngineUnavailable(retryable=True, cause=message)` | **Preserve the engine message for the operator log**, show generic retry text. |
| `tunnel_url_unavailable` | 504 | `EngineRefused("tunnel_url_unavailable")` | "Could not publish; try again." |
| `insufficient_storage` | 507 | `EngineRefused("insufficient_storage")` | "The workspace is out of disk." |

The engine's `message` is attached to the error object as `cause` for **logging**,
and (DEBUG + admin only) for the collapsed raw block. It is **never** shown to a
normal user; the mapping layer supplies the human sentence.

A test enumerates every code in SPEC §2.8 / INTEGRATION-GUIDE §9.3 and asserts
the adapter classifies it and the presentation layer has a sentence for it.

---

## 7. Every documented error code the adapter must recognize

Authoritative list (SPEC §2.8 and INTEGRATION-GUIDE §9.3), by status:

```
400 validation
401 unauthorized
403 forbidden, subscription_expired
404 not_found, ports_disabled
405 validation
409 conflict, workspace_suspended, too_many_sessions, session_ended, archive_entry_exists
413 payload_too_large, archive_too_large, archive_too_many_entries, archive_ratio_exceeded
415 unsupported_archive
422 unprocessable, unknown_profile, not_directory, session_needs_interpreter,
    subscription_invalid, max_duration_exceeded, subscription_would_shorten,
    tunnels_disabled, too_many_tunnels, archive_encrypted, archive_multivolume,
    archive_unsafe_entry
429 write_rate_exceeded
500 internal
503 unavailable
504 tunnel_url_unavailable
507 insufficient_storage
```

(Also: `timeout` is defined but never emitted; the adapter still parses it
defensively.)

These are the exact codes that [`../ui/05-errors-and-status.md`](../ui/05-errors-and-status.md)
maps to AR/EN sentences.

---

## 8. Testing the adapter

- **Contract fixtures.** Recorded request/response pairs (mirroring
  `docs-cloud-service/INTEGRATION-VERIFICATION.md`) are replayed against
  `httpx.MockTransport`; assertions cover DTO parsing, error translation, offset
  following, and enum validation.
- **Fake engine.** `FakeEngine(EnginePort)` in `adapters/cloud_service/fake.py`
  for DEV and unit tests — deterministic workspaces, a tree, a run that advances
  through states with synthetic logs.
- **Meta refresh.** A test that a simulated `422 unprocessable` triggers a
  `meta.refresh()` and one retry.
- **No leak.** A test that no `EngineRefused`/`EngineUnavailable` object carries a
  field the presentation layer would render verbatim.
