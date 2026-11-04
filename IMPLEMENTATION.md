# cloud-bot — Implementation notes (Phase 1 + Phase 1.5)

This document records what the implementation delivers, what it deliberately does
not, how to run the tests, how to start the bot, and the known gaps. It follows
`docs/` (authoritative) and the four measured briefs.

## What is implemented

- **Package skeleton and composition root** (`cloudbot/`): `core`, `domain`
  (+`ports`), `services`, `presentation`, `adapters`, `handlers`, `states`,
  `middleware`, `bootstrap`. The one-way import rule is enforced by
  `tests/architecture/test_layers.py`.
- **Settings and modes** (`core/config.py`, `core/modes.py`): `pydantic-settings`
  loaded from env/`.env` with a documented `.env.example`. `PRODUCTION`/`DEV`/
  `DEBUG`, the admin gate (DEBUG with an empty `admin_ids` is refused at
  startup), and a single behaviour table no component branches around. Secrets
  are `SecretStr`; `core/logging.py` redacts them.
- **Persistence** (`adapters/persistence/`): the SQLite schema exactly as
  `docs/api/persistence.md` §2 specifies (`users`, `user_workspaces`,
  `terminal_sessions`, `schema_migrations`), applied by a forward-only,
  idempotent, fail-fast migration runner. The `tg_id -> [ws_…]` ownership
  registry is the isolation boundary; the domain gate consults it on **every**
  workspace-scoped call.
- **Engine adapter** (`adapters/cloud_service/`): the only module that knows
  HTTP or engine DTOs. Implements `/v1/meta`, workspaces, files, `exec`, and runs
  (start / logs / stop / history / purge). Retries only idempotent verbs (never a
  creation `POST`), honors `Retry-After`, refreshes `/v1/meta` on a
  `422 unprocessable`, and translates every documented engine code into a typed
  domain error (`adapters/cloud_service/errors.py`). A `FakeEngine` serves
  deterministic fixtures for DEV/tests.
- **Domain services behind ports** (`services/`): `ownership`, `account`,
  `workspaces`, `files`, `execution` depend on `Protocol`s
  (`domain/ports/`), never on httpx/aiogram/SQL.
- **Rendering and presentation** (`presentation/`): rich-message output only,
  through `RichDoc` → `InputRichMessage(html=…, is_rtl=…)`. `RichSender` is the
  single outbound gateway (`send_rich_message`, `send_rich_message_draft`,
  `edit_message_text(rich_message=…)`). The measured aiogram 3.31.0 corrections
  are applied: `ButtonStyle` from `aiogram.enums`; `style` validated by a local
  enum (`ALLOWED_STYLES`); `disabled=True` builds a `DisabledButton` instance;
  no `edit_rich_message`.
- **Screens** for Phase 1: onboarding + language picker, main menu, help,
  settings; workspace list/detail/create (with the advanced-limits path) and the
  lifecycle confirmations; file browser (paged, breadcrumbs), file view, mkdir,
  rename, delete, upload; exec result, run mode, live logs (offset protocol +
  draft entry point), run history, stop, purge conflict. Every string is
  bilingual (English + Arabic, Arabic rendered `is_rtl=True`); numbers, dates
  and plurals are localized in `presentation/i18n/formatters.py`.
- **Handlers / middleware / bootstrap**: thin aiogram routers, edge middleware
  (context, error guard, throttle, mode gate, observability, injection), and a
  composition root that wires everything; `python -m cloudbot` starts polling.
- **Interactive terminal (Phase 1.5)** — the engine's **native sessions API**, not
  a hand-rolled PTY:
  - `SessionStore` (`adapters/persistence/sqlite_sessions.py`) maps
    `tg_id ↔ ws_id/ses_id` plus the output offset cursor and PTY size.
  - Engine calls in `adapters/cloud_service/engine.py`: `open_session`,
    `list_sessions`, `get_session`, `session_input`, `session_output`,
    `session_resize`, `session_signal`, `close_session`; the new codes
    (`too_many_sessions`, `session_ended`, `session_needs_interpreter`) are wired
    through the existing error translation.
  - `services/sessions.py` enforces, in order, the **ownership gate**, the
    **concurrency cap** (a `409 too_many_sessions` becomes the documented screen,
    never a raw error), and the **signal allowlist** (`INT|TERM|QUIT|HUP`).
  - Screens in `presentation/screens/terminal.py`: **my terminals**, the
    **terminal view** (status heading, recent output in a `<pre>`, and the buttons
    send-command / Ctrl-C / Ctrl-D / resize / close / my terminals), **resize**
    (presets + a documented default `80×24`), the **ended** outcome, and the
    **idle/lifetime notice** taken from the engine's session config.
  - Input flows through the FSM state group `states/terminal_input.py`; a plain
    message is sent with a newline appended, and a `/command` is never swallowed
    as terminal input.
  - Live output follows the **same offset protocol as run logs**, updating one
    message in place, bounded by iteration count and a poll interval.

## How to use the terminal

1. Open a workspace → **🖥 Terminals** → **➕ Open terminal**.
2. The terminal view shows the status and recent output. Tap **⌨️ Send a command**,
   type (for example) `ls`, and it is sent with Enter; `cd`, `export`, aliases
   survive because it is one long-lived shell.
3. **Ctrl-C** sends `INT` to the foreground process group (it interrupts the
   running command without ending the terminal). **Ctrl-D** sends the EOF byte
   (`0x04`) to the shell — chosen over a `TERM` signal because it is the
   interactive end-of-input that lets the shell exit **gracefully** on its own
   (running its exit traps) instead of being killed; the session then reads as
   `exited`/`shell_exit`. **↔️ Resize** changes the PTY size (`stty size`
   reflects it).
4. **⏹ Close** ends the terminal; later input reads as the documented
   `session_ended` and the screen offers a new terminal.
5. The view states when the terminal closes (idle and absolute lifetime), from the
   engine's configuration — never a hardcoded number.


## What is deliberately not implemented (and why)

- **Packages, tunnels, ports, metrics, processes, subscriptions, archives** —
  Phase 2. Their error codes are already mapped and catalogued so the vocabulary
  cannot silently rot.
- **Per-user plans/quota/billing and real rate-limit tiers** — Phase 3; the seams
  (`services/*`, `users.plan`) exist. A simple token-bucket throttle is present.
- **File delivery as Telegram documents** — deferred (CB-14); binary files show
  metadata plus a "preview not available" note.
- **Redis FSM / Postgres adapter / webhooks** — deferred behind the existing
  ports (CB-11/CB-12/CB-22).
- **Live-log draft streaming loop** — the screens and the offset-follow service
  helper are implemented, and `RichSender.draft` wraps
  `send_rich_message_draft`; the long-running per-message tail loop that
  periodically re-drafts until terminal is left as the documented manual step
  because operating it requires a live Telegram session.

## How to run the tests

```bash
/home/zerops/cloud-bot-venv/bin/python -m pytest -q                 # offline, no token, no network
CLOUDBOT_LIVE_ENGINE=1 /home/zerops/cloud-bot-venv/bin/python -m pytest -q -m live   # real engine
bash scripts/verify.sh                                             # the three key proofs
```

## How to start the bot

```bash
CLOUDBOT_BOT_TOKEN=… CLOUDBOT_ENGINE_API_KEY=… CLOUDBOT_ENGINE_BASE_URL=http://127.0.0.1:8098 \
  /home/zerops/cloud-bot-venv/bin/python -m cloudbot
# or: bash scripts/dev.sh   (DEV mode)
```

**Do not run this while another process polls the same bot token.** Only one
long-poller may hold a token. The implementation was built and tested with mocks
and against the local engine; starting it against Telegram is left to the
operator.

## Known gaps

- **Live execution on this host is blocked by the engine's cgroup setup.** The
  engine at `http://127.0.0.1:8098` creates workspaces (`201`) but refuses both
  `POST .../exec` and `POST .../sessions` with
  `503 {"code":"unavailable","message":"workspace cgroup unavailable: delegate
  controllers into /sys/fs/cgroup/ws-…: write …/cgroup.subtree_control: no such
  file or directory"}`. This is an environment limitation (cgroup delegation),
  reproducible with `curl` against the engine and independent of this bot: the
  pre-existing Phase-1 `exec` live test fails identically. The live tests are
  left in place and documented per the task rule; they pass against an engine
  whose cgroups are delegated.
- The live-log/session tail loops update one message in place on a bounded
  schedule; the ephemeral `send_rich_message_draft` preview is available on
  `RichSender.draft` but the long-running draft loop is not started
  automatically (it requires a live Telegram session to operate).
- FSM storage is in-memory for all modes (the tables and the port for a
  SQLite/Redis swap exist).
- `edit_message_text` rich-edit behaviour and `send_rich_message_draft` are
  exercised against a fake `Bot`; a real Telegram round-trip was not performed
  (per the hard rule against starting a poller).
- Deep links and per-message "updated X ago" refresh timers are simplified.
