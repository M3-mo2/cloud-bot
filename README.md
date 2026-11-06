# cloud-bot

**cloud-bot** is the operator-owned Telegram frontend for **Cloud Service**
(`sandboxd`), a headless sandbox engine. It is a Telegram bot written in
**Python** with **aiogram 3.31.0**. A person talks to the bot to create an
isolated sandbox workspace, put files in it, and run code — without ever
knowing that an engine called `sandboxd` exists.

---

## The one idea

**cloud-bot is the only product that holds a Cloud Service bearer key; end
users never see, hold, or send one.** The bot owns accounts, plans, quota,
billing, and user isolation — none of which the engine implements. The engine
treats cloud-bot as a single trusted backend (`principal_id`), so it cannot
tell user A from user B; the bot keeps its own registry (`tg_id -> owned
ws_...`) and enforces it itself, in the domain layer, on every
workspace-scoped call. A workspace id the caller does not own is treated as
nonexistent.

Paired with that: **no raw engine output ever reaches a user.** No JSON and no
raw English engine `message` is ever shown; a translation layer maps every
error code and status to human words in the user's language. DEBUG mode may
show raw payloads, but only to admins and only collapsed.

---

## Features

- **Workspaces** — create, inspect, and manage the lifecycle (suspend, resume, stop, delete).
- **Files** — browse directories, view and edit files, upload and download, manage permissions, create and rename.
- **Execution** — run a command synchronously (`exec`), or start a background run and follow its live logs and history.
- **Terminal** — an interactive PTY session through the engine's native sessions API, with signal support; the PTY defaults to 100×30 and `stty` adjusts it in-session.
- **Packages** — list, install, and remove Python packages, with honest `exit_code` handling.
- **Tunnels and ports** — publish ephemeral quick tunnels and discover listening ports.
- **Metrics** — one-shot CPU/RAM/disk snapshots with explicit `*_complete` honesty.
- **Processes** — list and stop individual process trees.
- **Archives** — upload-and-extract, in-place extract, and export a directory.
- **Admin panel** (`/admin`) — users, a page per user, entering a workspace, notifications, and the audit log.

The whole interface is bilingual: an **Arabic** and an **English** catalog,
both complete, with Arabic sent right-to-left.

---

## Quick start

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
mkdir -p data
cp .env.example .env      # then fill in CLOUDBOT_BOT_TOKEN and the engine URL/key
python -m cloudbot
```

- `data/` must exist: SQLite will not create the directory.
- Dependencies live only in `pyproject.toml` — there is no `requirements.txt`.
- **One bot token = one long-poller.** If this token is already polled by
  another process, both look broken (Telegram returns a `Conflict` to whichever
  asks second). For local work, create a separate bot with @BotFather.

---

## Configuration

Settings are read from the environment through `pydantic-settings` with the
`CLOUDBOT_` prefix. [`.env.example`](.env.example) is the authoritative,
commented reference — link to it rather than duplicating it here. `.env` is
**gitignored and must never be committed**.

| Variable | Meaning |
| --- | --- |
| `CLOUDBOT_BOT_TOKEN` | Bot token from @BotFather; the bot refuses to start without it. |
| `CLOUDBOT_MODE` | `production`, `dev`, or `debug` (see below). |
| `CLOUDBOT_ENGINE_BASE_URL` | Where the engine listens. |
| `CLOUDBOT_ENGINE_API_KEY` | The one key the bot sends as `Authorization: Bearer`. |
| `CLOUDBOT_ENGINE_TIMEOUT_S` | Engine request timeout, in seconds. |
| `CLOUDBOT_ENGINE_MAX_RETRIES` | Retry count for transient engine failures. |
| `CLOUDBOT_DATABASE_URL` | The bot's own SQLite database (users, workspaces, events). |
| `CLOUDBOT_ADMIN_IDS` | Comma-separated Telegram ids allowed to open `/admin`. |
| `CLOUDBOT_DEFAULT_LOCALE` | `ar` or `en`; only the default, each user's choice is stored. |
| `CLOUDBOT_THROTTLE_RATE` | Minimum seconds between two actions from the same user. |
| `CLOUDBOT_LOG_LEVEL` | structlog level; empty uses the mode's default. |
| `CLOUDBOT_DEBUG_SHOW_RAW` | Allow raw engine payloads in error screens (debug + admin only). |
| `CLOUDBOT_MOCK_ENGINE` | In `dev`, run against an in-process fake engine (no engine, no key). |

---

## Architecture

The code is a hexagonal core with a strict one-way dependency rule. The
direction is:

```
core → domain (+ports) → services → presentation → adapters → handlers → bootstrap
```

- **core** — configuration, runtime modes, logging, clock, locale; no business logic.
- **domain** — entities, value objects, and the ports (`Protocol`s) that describe the product; no `aiogram`, no `httpx`.
- **services** — use-cases that orchestrate ports, including the ownership gate.
- **presentation** — rich messages, keyboards, screens, and the bilingual catalogs.
- **adapters** — the only place with I/O libraries; implement the ports for the engine, SQLite, and Telegram.
- **handlers** — thin aiogram routers that call one service and render one screen.
- **bootstrap** — the composition root that wires everything together.

The direction is **enforced by an architecture test**, so a violation fails the
suite instead of being caught in review. For depth, read
[`docs/architecture/`](docs/architecture/) — start with
[`01-overview.md`](docs/architecture/01-overview.md) and
[`02-layers.md`](docs/architecture/02-layers.md).

Runtime behaviour has exactly three modes — `production`, `dev`, and `debug`.
No component branches on the raw enum; every difference lives in one table in
`cloudbot/core/modes.py`, so all behavioural differences are reviewable in a
single place.

---

## Running the checks

```bash
python -m pytest -q
bash scripts/lint.sh
bash scripts/verify.sh
```

`scripts/verify.sh` is not a test runner: it is **29 proofs**, each asserting a
specific product guarantee (the rich-only rule, no raw engine output reaching a
user, ownership isolation, the terminal using the engine's native sessions API,
and so on). Several are **bite tests** that deliberately break a guard, assert
the suite goes red, restore it, and assert green again.

---

## Documentation map

| Document | What it is |
| --- | --- |
| [`docs/README.md`](docs/README.md) | The documentation index, and the five rules that never bend. |
| [`docs/architecture/01-overview.md`](docs/architecture/01-overview.md) | What the bot is, who uses it, the guiding principles. |
| [`docs/ADMIN-PANEL.md`](docs/ADMIN-PANEL.md) | The `/admin` panel architecture and extension guide. |
| [`docs/LOCAL-DEV.md`](docs/LOCAL-DEV.md) | Running the bot on your own machine. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phase scope and sequencing. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Every call that was made, and why. |
| [`docs/ui/`](docs/ui/) | Per-screen behaviour for every screen. |
| [`docs/api/`](docs/api/) | The engine client and the persistence layer. |

---

## What this is not

- The **engine is a separate project** (`cloud-service`, written in Go); this
  repository is only its client.
- **Subscriptions and billing (Phase 2f) are deliberately not built.**
- There is **no licence file** and **no CI** configured.
- It is a **Telegram bot**, not a web app and not an HTTP API.
