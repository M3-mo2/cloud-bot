# 02 — Layers and Package Map

The architecture is a **hexagonal core with a strict one-way dependency rule**,
plus a dedicated presentation layer for the two things users actually perceive:
rich messages and keyboards. Nothing is "okay" here — each layer has one reason
to change, and the direction of every dependency is enforced by convention and by
a test.

## 1. The dependency rule

> **Dependencies point inward. Inner layers never import outer layers.**

```
        ┌──────────────────────────── handlers ────────────────────────────┐
        │  aiogram routers · FSM · middleware · DI wiring at the edge       │
        └───────────────┬──────────────────────────┬──────────────────────┘
                        │                          │
             ┌──────────▼──────────┐    ┌──────────▼──────────┐
             │      services       │    │    presentation     │
             │  use-cases on ports │    │ rich · keyboards ·  │
             │  (no IO libraries)  │    │ screens · catalogs  │
             └──────────┬──────────┘    └──────────┬──────────┘
                        │                          │
             ┌──────────▼──────────────────────────▼──────────┐
             │                    domain                      │
             │  entities · value objects · ports · errors     │
             │         (pure; no aiogram, no httpx)           │
             └──────────────────────────┬─────────────────────┘
                                        │
             ┌──────────────────────────▼─────────────────────┐
             │                     core                        │
             │  config · modes · logging · clock · locale      │
             └─────────────────────────────────────────────────┘

        Adapters (infrastructure) implement ports and are imported
        ONLY by the composition root and by handlers, never by services.
```

**Allowed imports**

| From | May import |
| --- | --- |
| `core` | standard library, `pydantic`, nothing project-inner |
| `domain` | `core` |
| `services` | `core`, `domain` |
| `presentation` | `core`, `domain` (view models derived from domain entities) |
| `adapters/*` | `core`, `domain` (to implement ports), third-party libs |
| `handlers` | `core`, `services`, `presentation`; adapters only via DI |
| `bootstrap` | everything (composition root) |

**Forbidden imports** (checked by an architecture test): `domain` or `services`
importing `aiogram` or `httpx`; `presentation` importing `httpx` or `aiosqlite`;
`core`/`domain` importing any adapter.

## 2. Package tree

```
cloud-bot/
├── pyproject.toml
├── README.md
├── docs/                          # this documentation set
├── src/cloudbot/
│   ├── __main__.py                # `python -m cloudbot`
│   │
│   ├── core/                      # ── innermost: primitives, no business logic
│   │   ├── config.py              # Settings (pydantic-settings), RunMode enum
│   │   ├── modes.py               # RunMode behaviour table (what each mode enables)
│   │   ├── logging.py             # structlog setup, level per mode, redaction
│   │   ├── clock.py               # Clock port + SystemClock/FrozenClock (testable)
│   │   ├── locale.py              # Locale enum (AR, EN), is_rtl, default
│   │   └── errors.py              # DomainError base + subclasses (carry codes, not text)
│   │
│   ├── domain/                    # ── pure model of the product
│   │   ├── entities.py            # User, Workspace, FileEntry, Run, TerminalSession
│   │   ├── value_objects.py       # WorkspaceId, RunId, Limits, ResourceUsage, Money
│   │   ├── ports/
│   │   │   ├── engine.py          # EnginePort (all Cloud Service operations)
│   │   │   ├── users.py           # UserRepository (owners, locale, plan)
│   │   │   ├── sessions.py        # SessionStore (terminal session handles)
│   │   │   └── clock.py           # re-export of core Clock for domain use
│   │   └── errors.py              # Engine errors as typed values (code + fields)
│   │
│   ├── services/                  # ── use-cases; orchestrate ports; no IO libs
│   │   ├── account.py             # onboard, set_locale, profile
│   │   ├── workspaces.py          # list/create/get/patch/suspend/resume/stop/delete
│   │   ├── files.py               # list/stat/read/write/mkdir/rename/delete/upload
│   │   ├── execution.py           # exec, create run, list runs, logs, stop, purge
│   │   ├── terminal.py            # sessions (Phase 1.5): create/input/output/...
│   │   └── ownership.py           # assert_owned(tg_id, ws_id) — the isolation gate
│   │
│   ├── presentation/              # ── the ONLY place output shapes exist
│   │   ├── rich/
│   │   │   ├── document.py        # RichDoc builder → InputRichMessage(html=...)
│   │   │   ├── tags.py            # safe HTML escaping + element helpers
│   │   │   ├── components.py      # Heading, Table, KeyValue, Code, Collapsible, Alert
│   │   │   └── sender.py          # RichSender: send/edit/draft via aiogram
│   │   ├── keyboards/
│   │   │   ├── styles.py          # Intent → ButtonStyle convention
│   │   │   ├── buttons.py         # button factories (inline + reply, disabled, force_reply)
│   │   │   └── markup.py          # keyboard composition helpers
│   │   ├── screens/               # one module per screen; entity → Screen
│   │   │   ├── screen.py          # Screen = RichDoc + Markup; render helpers
│   │   │   ├── language.py
│   │   │   ├── menu.py
│   │   │   ├── workspaces.py
│   │   │   ├── workspace_detail.py
│   │   │   ├── files.py
│   │   │   ├── file_view.py
│   │   │   ├── run.py
│   │   │   ├── run_logs.py
│   │   │   └── errors.py          # error/status code → Screen
│   │   ├── i18n/
│   │   │   ├── catalog.py         # Catalog: key + Locale + args → str
│   │   │   ├── formatters.py      # bytes, duration, relative time, counts, plurals
│   │   │   └── catalogs/
│   │   │       ├── en.py          # English strings
│   │   │       └── ar.py          # Arabic strings
│   │   └── mapping/               # exhaustive code/status → human vocabulary
│   │       ├── errors.py          # error.code → catalog key
│   │       └── statuses.py        # run/workspace/session status → catalog key
│   │
│   ├── adapters/                  # ── the outside world
│   │   ├── cloud_service/
│   │   │   ├── client.py          # httpx.AsyncClient, auth, retry, timeout
│   │   │   ├── dto.py             # pydantic models mirroring API.md
│   │   │   ├── meta.py            # /v1/meta cache + refresh-on-422/409
│   │   │   ├── errors.py          # HTTP → domain EngineError translation
│   │   │   ├── mapping.py         # DTO → domain entity
│   │   │   └── engine.py          # EnginePort implementation
│   │   ├── persistence/
│   │   │   ├── database.py        # aiosqlite connection, migrations runner
│   │   │   ├── migrations/        # 0001_init.sql, ...
│   │   │   ├── sqlite_users.py    # UserRepository implementation
│   │   │   ├── sqlite_sessions.py # SessionStore implementation
│   │   │   └── models.py          # row ↔ entity mapping
│   │   └── telegram/
│   │       └── transport.py       # thin wrappers around RichSender for aiogram
│   │
│   ├── handlers/                  # ── aiogram routers (thin)
│   │   ├── common.py              # /start, /help, /settings, language
│   │   ├── menu.py
│   │   ├── workspaces.py
│   │   ├── files.py
│   │   ├── execution.py
│   │   ├── terminal.py            # Phase 1.5
│   │   ├── admin.py               # DEBUG-only commands (/diag, /raw, /mode)
│   │   └── callbacks/
│   │       ├── navigation.py      # nav:* callbacks
│   │       └── actions.py         # ws:*, file:*, run:*
│   │
│   ├── states/                    # aiogram FSM state groups
│   │   ├── onboarding.py          # language, first workspace
│   │   └── input.py               # awaiting name/path/content/command
│   │
│   ├── middleware/                # aiogram middleware (edge concerns only)
│   │   ├── context.py             # build UserContext (locale, tg user, repo handle)
│   │   ├── error_guard.py         # domain error → Screen; never leak
│   │   ├── throttle.py            # per-user rate limit (prod/dev)
│   │   ├── mode_gate.py           # block debug commands outside DEBUG
│   │   └── observability.py       # correlation id, timing, structured log
│   │
│   └── bootstrap/
│       ├── container.py           # composition root: build settings, adapters, services
│       ├── app.py                 # Dispatcher, routers, middleware, FSM storage
│       └── lifecycle.py           # startup/shutdown: meta fetch, DB migrate, health
│
├── tests/
│   ├── unit/                      # services, mapping, i18n, rich builder
│   ├── contract/                  # adapters vs fakes; engine adapter vs recorded fixtures
│   ├── architecture/              # import-rule test
│   └── golden/                    # screen snapshots per locale
└── scripts/
    ├── dev.sh                     # run in DEV mode
    └── lint.sh
```

## 3. Why this shape

- **`core` is tiny and dependency-free**, so it is trivially testable and cannot
  become a dumping ground.
- **`domain` holds no framework.** Entities and ports describe the product; they
  never mention `aiogram`, `httpx`, or SQL. This is what makes the engine and the
  database swappable.
- **`services` is where policy lives**: ownership checks, limit pre-validation,
  ordering of engine calls. It depends on ports, not implementations, so a fake
  engine is one line away.
- **`presentation` is isolated on purpose.** Because rich HTML is mandatory and
  the keyboard convention must be uniform, rendering deserves its own layer that
  both handlers and (later) background jobs share. Nothing else may build a
  message.
- **`adapters` is the only place with I/O libraries.** Swapping SQLite for
  Postgres, or httpx for another transport, touches exactly one folder.
- **`handlers` are thin.** A handler parses an update, calls one service, hands
  the result to one screen, and sends. If a handler grows logic, that logic is in
  the wrong place.
- **`middleware` handles only edge concerns** (identity, error presentation,
  throttling, mode gating, timing). It never contains product logic.

## 4. Extension recipes (the architecture earns its keep)

**Add an engine operation (e.g. tunnels later):**
1. Add a method to `domain/ports/engine.py`.
2. Implement it in `adapters/cloud_service/engine.py` (+ DTO in `dto.py`).
3. Add a service method in `services/`.
4. Add a screen in `presentation/screens/`.
5. Add a handler/callback in `handlers/`.
6. Add strings to both catalogs.

No other file changes. Steps 1–3 are unit-testable with a fake engine; step 4 is
snapshot-testable per locale.

**Add a language:** add a catalog module and a `Locale` member; the formatters
and RTL flag follow from `core/locale.py`.

**Swap SQLite → Postgres:** implement `UserRepository`/`SessionStore` against
Postgres in `adapters/persistence/`; change one line in `bootstrap/container.py`.
No service, screen, or handler changes.

## 5. Testing strategy

- **unit** — services against in-memory port fakes; mapping and i18n tables
  against their source documents.
- **contract** — each adapter proven to satisfy its port; the engine adapter
  replayed against recorded Cloud Service fixtures (the transcripts in
  `docs-cloud-service/INTEGRATION-VERIFICATION.md` are the template).
- **architecture** — a test asserts the import rule (no `aiogram`/`httpx` under
  `domain`/`services`, etc.), so the boundaries cannot rot.
- **golden** — every screen rendered for both locales, snapshot-compared, so a
  stray raw string or an unescaped tag fails CI.
