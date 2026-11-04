# 03 — Components

This document defines the responsibility and public shape of each component. It
is the contract between layers. Signatures are illustrative Python (`typing`), not
final code.

---

## 1. `core`

### 1.1 `core/config.py`

`Settings` is loaded from environment / `.env` via `pydantic-settings`. It is
immutable, validated at startup, and injected everywhere.

| Field | Type | Notes |
| --- | --- | --- |
| `mode` | `RunMode` | `production` \| `dev` \| `debug`. |
| `bot_token` | `SecretStr` | aiogram token. |
| `engine_base_url` | `AnyHttpUrl` | Cloud Service origin. |
| `engine_api_key` | `SecretStr` | The single bearer service key. |
| `engine_timeout_s` | `float` | Per-request timeout (default 30; exec uses longer). |
| `engine_max_retries` | `int` | Transient retries for 5xx/network. |
| `database_url` | `str` | `sqlite+aiosqlite:///./data/bot.db`. |
| `admin_ids` | `frozenset[int]` | DEBUG/admin gate. |
| `default_locale` | `Locale` | Fallback before the user chooses. |
| `throttle_rate` | `float` | Messages/second per user. |
| `log_level` | `str` | Derived from mode unless overridden. |
| `debug_show_raw` | `bool` | DEBUG only; show collapsed raw payloads to admins. |

### 1.2 `core/modes.py`

A frozen table describing what each mode changes (see
[`05-runtime-modes.md`](05-runtime-modes.md)). Exposes predicates like
`allows_admin_commands(mode)`, `mock_engine(mode)`, `verbose_errors(mode)`. No
component branches on the raw enum; they ask the table.

### 1.3 `core/clock.py`

`Clock` is a port: `now() -> datetime`. `SystemClock` in production, `FrozenClock`
in tests. Never call `datetime.now()` directly in domain/services.

### 1.4 `core/locale.py`

`Locale(Enum): AR = "ar"; EN = "en"`; `is_rtl(locale) -> bool`;
`LOCALE_LABELS: dict[Locale, str]` for the picker (each label shown in its own
script).

### 1.5 `core/errors.py` and `domain/errors.py`

`DomainError` is the base. Subclasses carry **codes and structured fields**, never
user text:

- `EngineUnavailable(retryable: bool, cause: str)`
- `EngineRefused(code: str, message: str, details: dict)` — a valid request the
  engine refused (422/409/403/404/413/415/507).
- `NotOwned(ws_id: str)` — the isolation gate.
- `WorkspaceMissing(ws_id: str)`
- `InvalidInput(field: str, reason: str)`
- `RateLimited(retry_after_s: float)`

The presentation layer maps these to catalog keys. Nothing prints `str(exc)` to a
user.

---

## 2. `domain`

### 2.1 Entities (`domain/entities.py`)

Frozen dataclasses (or pydantic models with `frozen=True`), framework-free:

```python
@dataclass(frozen=True)
class User:
    tg_id: int
    locale: Locale
    created_at: datetime
    plan: str = "free"

@dataclass(frozen=True)
class Workspace:
    id: WorkspaceId
    owner_tg_id: int
    name: str
    profile: str
    status: WorkspaceStatus           # active | suspended | locked
    limits: Limits
    summary: WorkspaceSummary
    created_at: datetime
    last_activity_at: datetime

@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    type: Literal["file", "dir", "symlink"]
    size: int | None
    mode: str | None
    mod_time: datetime | None
    runnable: str | None              # interpreter, when the engine says so
    target: str | None = None

@dataclass(frozen=True)
class Run:
    id: RunId
    workspace_id: WorkspaceId
    kind: Literal["file", "command"]
    argv: list[str]
    cwd: str
    status: RunStatus                 # see ui/05
    exit_code: int | None
    interpreter: str | None
    timeout_sec: int | None
    started_at: datetime | None
    ended_at: datetime | None

@dataclass(frozen=True)
class ResourceUsage:                  # from /usage and /metrics
    disk_bytes: int
    disk_limit_bytes: int
    disk_percent: float
    runs_total: int
    active_runs: int
    max_runs: int
    disk_complete: bool = True
```

### 2.2 Value objects (`domain/value_objects.py`)

`WorkspaceId(str)`, `RunId(str)`, `SessionId(str)` with prefix validation
(`ws_`, `run_`, `ses_`). `Limits` mirrors the engine's limit block.
`WorkspaceStatus`, `RunStatus`, `SessionStatus` enums mirror the engine exactly.

### 2.3 Ports (`domain/ports/`)

**`EnginePort`** — the complete Cloud Service surface the bot uses. Grouped by
resource; every method returns domain entities or raises a `domain.error`:

```python
class EnginePort(Protocol):
    async def meta(self, refresh: bool = False) -> EngineMeta: ...

    async def create_workspace(self, *, name, profile, limits, subscription) -> Workspace: ...
    async def list_workspaces(self) -> list[Workspace]: ...
    async def get_workspace(self, ws_id) -> Workspace: ...
    async def patch_workspace(self, ws_id, *, name=None, limits=None) -> Workspace: ...
    async def suspend_workspace(self, ws_id) -> LifecycleResult: ...
    async def resume_workspace(self, ws_id) -> LifecycleResult: ...
    async def stop_workspace(self, ws_id) -> LifecycleResult: ...
    async def delete_workspace(self, ws_id) -> None: ...
    async def workspace_usage(self, ws_id) -> ResourceUsage: ...

    async def list_files(self, ws_id, path, *, offset=0, limit=...) -> FilePage: ...
    async def stat_file(self, ws_id, path) -> FileEntry: ...
    async def read_file(self, ws_id, path, *, encoding=None, line_range=None, meta=False) -> FileContent: ...
    async def write_file(self, ws_id, path, content, *, encoding="text") -> None: ...
    async def delete_file(self, ws_id, path) -> None: ...
    async def mkdir(self, ws_id, path) -> None: ...
    async def rename(self, ws_id, src, dst) -> None: ...
    async def upload_file(self, ws_id, path, filename, stream) -> None: ...

    async def exec(self, ws_id, cmd, *, cwd, env, stdin, timeout_sec) -> ExecResult: ...
    async def create_run(self, ws_id, *, kind, file=None, argv=None, args=None,
                         cwd=None, env=None, interpreter=None,
                         timeout_sec=None, no_timeout=False) -> Run: ...
    async def list_runs(self, ws_id, *, status=None, kind=None, limit, offset) -> RunPage: ...
    async def get_run(self, ws_id, run_id) -> Run: ...
    async def run_logs(self, ws_id, run_id, *, stream, offset, limit) -> LogChunk: ...
    async def stop_run(self, ws_id, run_id) -> None: ...
    async def purge_runs(self, ws_id) -> int: ...
    async def delete_run(self, ws_id, run_id) -> None: ...
```

**`UserRepository`**:

```python
class UserRepository(Protocol):
    async def get_or_create(self, tg_id: int, *, default_locale: Locale) -> User: ...
    async def get(self, tg_id: int) -> User | None: ...
    async def set_locale(self, tg_id: int, locale: Locale) -> None: ...
    async def add_workspace(self, tg_id: int, ws_id: WorkspaceId) -> None: ...
    async def remove_workspace(self, tg_id: int, ws_id: WorkspaceId) -> None: ...
    async def owned_workspaces(self, tg_id: int) -> list[WorkspaceId]: ...
    async def owns(self, tg_id: int, ws_id: WorkspaceId) -> bool: ...
```

**`SessionStore`** (terminal, Phase 1.5): maps Telegram chat/message ↔ engine
`ses_...`, plus the output offset cursor.

---

## 3. `services`

Each service takes ports via constructor injection and exposes async methods that
return **domain entities** (or raise domain errors). No service imports aiogram,
httpx, or the DB driver.

### 3.1 `services/ownership.py`

The isolation gate. `assert_owned(tg_id, ws_id)` raises `NotOwned` unless the repo
confirms it. **Every** workspace-scoped service method calls this first. This is
the single choke point that makes a single-principal engine safe for many users.

### 3.2 `services/workspaces.py`

`list`, `create` (pre-validates name/limits against `EngineMeta.limits_maxima`
*and* the user's plan allowance before calling the engine), `get`, `patch`,
`suspend`, `resume`, `stop`, `delete`, `usage`. `create` registers the new
workspace with the `UserRepository` *after* the engine returns the id.

### 3.3 `services/files.py`

`list` (paged), `view` (JSON-wrapped read with metadata), `write`, `delete`,
`mkdir`, `rename`, `upload`. Encodes binary safely; enforces per-plan file-size
caps before upload to avoid a wasted round trip.

### 3.4 `services/execution.py`

`exec` (sync, hard 60 s cap awareness), `run_file`, `run_command`, `list_runs`,
`get_run`, `tail_logs` (offset-follow helper), `stop_run`, `purge`, `delete_run`.

### 3.5 `services/account.py`

`onboard`, `set_locale`, `profile`.

### 3.6 `services/terminal.py` (Phase 1.5)

`open`, `input`, `output` (offset), `resize`, `signal`, `close`, `list`.

---

## 4. `presentation`

### 4.1 `presentation/rich/document.py` — `RichDoc`

A small builder that composes an `InputRichMessage` from typed elements and
guarantees escaping.

```python
class RichDoc:
    def __init__(self, locale: Locale): ...
    def h(self, level: int, text: str) -> "RichDoc": ...
    def p(self, text: str) -> "RichDoc": ...
    def bold(self, text: str) -> "RichDoc": ...
    def code(self, text: str) -> "RichDoc": ...
    def pre(self, text: str, language: str | None = None) -> "RichDoc": ...
    def div(self) -> "RichDoc": ...
    def table(self, headers: Sequence[str], rows: Sequence[Sequence[str]],
              *, bordered=True, striped=True) -> "RichDoc": ...
    def kv(self, pairs: Mapping[str, str]) -> "RichDoc": ...
    def ul(self, items: Sequence[str]) -> "RichDoc": ...
    def details(self, summary: str, blocks: Sequence[Element], *, open=False) -> "RichDoc": ...
    def blockquote(self, text: str) -> "RichDoc": ...
    def alert(self, kind: AlertKind, title: str, body: str) -> "RichDoc": ...
    def build(self) -> InputRichMessage: ...   # is_rtl from locale
```

`build()` returns `InputRichMessage(html=..., is_rtl=core.locale.is_rtl(locale))`.
Exactly one of html/markdown/blocks is ever set (html).

### 4.2 `presentation/rich/tags.py`

Escaping is centralized: `esc(text)` escapes `& < >` (and quotes inside
attributes); every user-derived value (filenames, workspace names, command
output) passes through it. This is the **only** module allowed to concatenate raw
HTML.

### 4.3 `presentation/rich/components.py`

Higher-level, reusable structures: `StatusTable`, `UsageBar`, `LogBlock`,
`PathBreadcrumb`, `ConfirmPrompt`, `RawPayloadDetails` (DEBUG/admin only,
collapsible).

### 4.4 `presentation/rich/sender.py` — `RichSender`

The single outbound gateway. Wraps aiogram:

```python
class RichSender:
    async def send(self, chat_id: int, doc: RichDoc, *, markup: ReplyMarkup | None = None,
                   reply_to: int | None = None) -> Message: ...
    async def edit(self, chat_id: int, message_id: int, doc: RichDoc,
                   *, markup: ReplyMarkup | None = None) -> Message: ...
        # aiogram 3.31.0: edit_message_text(rich_message=doc.build(), reply_markup=markup)
    async def draft(self, chat_id: int, draft_id: int, doc: RichDoc,
                    *, can_stop: bool = True, keep_on_stop: bool = False) -> bool: ...
        # sends an ephemeral streamed preview (send_rich_message_draft)
    async def answer_callback(self, cb, *, text: str | None = None, alert: bool = False) -> None: ...
```

`send` → `bot.send_rich_message(chat_id, rich_message=doc.build(), reply_markup=markup)`.
`edit` → `bot.edit_message_text(chat_id=..., message_id=..., rich_message=doc.build(), reply_markup=markup)`
because **aiogram 3.31.0 has no `edit_rich_message`**. The sender is the only
place these calls exist, so a future aiogram release that adds
`edit_rich_message` changes exactly one method.

### 4.5 `presentation/keyboards/styles.py`

The intent→style convention (full table in
[`../rendering/keyboards.md`](../rendering/keyboards.md)). Screens declare
**intent**, not color: `btn("Run", intent=PRIMARY)`. This keeps every screen
consistent by construction.

```python
class Intent(Enum):
    PRIMARY = ButtonStyle.PRIMARY   # navigate, neutral, default
    SUCCESS = ButtonStyle.SUCCESS   # confirm, start, positive
    DANGER  = ButtonStyle.DANGER    # delete, stop, destructive
    LINK    = ButtonStyle.LINK      # external URL
    DISABLED = "disabled"           # shown, inert
```

### 4.6 `presentation/keyboards/buttons.py`

Factories that combine text (from the catalog), style, and flags:

```python
def inline(text: str, action: str, *, intent=Intent.PRIMARY,
           disabled: bool = False, url: str | None = None) -> InlineKeyboardButton: ...
def reply(text: str, *, intent=Intent.PRIMARY) -> KeyboardButton: ...
```

`inline(..., disabled=True)` sets `disabled=DisabledButton()`.
`reply` colorizes persistent menu items. `force_reply=True` is set on the markup,
not the button (see keyboards doc).

### 4.7 `presentation/screens/*` — `Screen`

A screen is a pure function of domain state + locale:

```python
@dataclass
class Screen:
    doc: RichDoc
    markup: ReplyMarkup | None = None
```

Screens are snapshot-testable: `build_workspace_detail(ws, locale) -> Screen`, no
I/O. The handler calls the service, gets entities, calls the screen, and hands the
`Screen` to `RichSender`. Screens for both locales are golden-tested.

### 4.8 `presentation/i18n/catalog.py` + `catalogs/`

`Catalog.t(key, locale, **args) -> str`. Keys are stable identifiers
(`ws.status.active`). Catalogs are plain dict-like modules, one per locale, with a
test asserting **key parity** between `en.py` and `ar.py`.

### 4.9 `presentation/i18n/formatters.py`

`format_bytes(n, locale)`, `format_duration(seconds, locale)`,
`format_relative(dt, now, locale)`, `format_count(n, locale)` with plural rules
(Arabic has six plural forms; `babel` supplies them), `format_percent`. Numbers
use locale-appropriate digits in Arabic. Mixed-direction text is wrapped for
embedding (see [`../architecture/06-i18n.md`](06-i18n.md)).

### 4.10 `presentation/mapping/{errors,statuses}.py`

The exhaustive vocabulary. `error_screen(code, locale) -> Screen` and
`status_label(status, locale) -> str` implement
[`../ui/05-errors-and-status.md`](../ui/05-errors-and-status.md) exactly. A unit
test asserts **every** engine code and status is present.

---

## 5. `handlers`

Thin aiogram routers. A handler's job:

```python
@router.callback_query(F.data.startswith("ws:view:"))
async def on_view(cb: CallbackQuery, ctx: UserContext, svc: WorkspacesService,
                  screen: ScreenFactory, sender: RichSender):
    ws = await svc.get(ctx.tg_id, ws_id_from(cb.data))   # ownership enforced inside
    await sender.edit(cb.chat_id, cb.message.message_id, screen.workspace_detail(ws, ctx.locale))
```

Rules: no HTTP, no HTML, no DB, no business rules. Parse → call one service →
render one screen → send/edit. Callbacks use a namespaced scheme
(`domain:action:id`) decoded by `handlers/callbacks/`.

---

## 6. `middleware`

- **`context.py`** — resolves/creates the `User`, attaches `UserContext(tg_id,
  locale, is_admin, mode)` to handler data.
- **`error_guard.py`** — catches `DomainError`, renders the correct localized
  `ErrorScreen`, never leaks an exception string; logs the structured cause.
- **`throttle.py`** — per-user token bucket; lower/absent in DEBUG.
- **`mode_gate.py`** — blocks admin commands outside DEBUG; blocks DEV-only
  features in PRODUCTION.
- **`observability.py`** — correlation id, duration, structured log per update.

---

## 7. `bootstrap`

- **`container.py`** — builds `Settings`, then adapters, then services; wires
  ports to implementations. The only place a concrete adapter is named.
- **`app.py`** — builds `Bot` + `Dispatcher`, registers routers/middleware, sets
  FSM storage (memory in DEV, SQLite in PROD).
- **`lifecycle.py`** — startup: fetch `/v1/meta`, run DB migrations, build menus;
  shutdown: close HTTP client and DB. `/healthz`-style readiness for the bot
  process itself.
