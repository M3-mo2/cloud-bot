# cloud-bot — Implementation notes (Phase 1 + Phase 1.5 + Phase 2a/2b/2c + 2d/2e/2g)

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
  - **A closed session stays known to its owner.** `close()` ends the session on
    the engine but **keeps** the bot-side handle, so a later read surfaces the
    engine's documented `409 session_ended` (not `NotOwned`): the isolation
    answer is reserved for ids the caller never owned. A terminal session does
    not count against the cap.
  - **Closed-session removal — explicit, not automatic.** Ended rows are not
    auto-purged (they are the owner's record of a session that ended, so they
    read correctly and never masquerade as "not yours"). They leave *my
    terminals* only through an explicit **🗑 Remove from list** action
    (`term:forget` → `SessionsService.forget`, catalog `terminal.forget` in both
    locales), offered on the ended screen and on the view of any terminal
    session. The engine record is untouched (the engine drops it with the
    workspace).
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

## How to use the terminal1. Open a workspace → **🖥 Terminals** → **➕ Open terminal**.
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
4. **⏹ Close** ends the terminal; the session stays listed as ended, later input
   reads as the documented `session_ended`, and the screen offers a new terminal
   or **🗑 Remove from list** to drop the ended row from *my terminals*.
5. The view states when the terminal closes (idle and absolute lifetime), from the
   engine's configuration — never a hardcoded number.


## Phase 2a/2b/2c — packages, tunnels, ports

Added additively in the same shape as Phase 1/1.5 (port → adapter → service →
screen → handler → catalogs):

- **Adapter** (`adapters/cloud_service/`): DTOs, mapping and engine calls for
  `GET/POST/DELETE /packages*`, `GET/POST/DELETE /tunnels*`, `GET /ports`. A pip
  install is bounded by a 300 s client timeout and a tunnel create by 90 s
  (`tunnels.url_timeout_sec` was measured at 45 s); neither creation `POST` is
  auto-retried. `errors.py` now honours a **modelled code on a 5xx** — a `504
  tunnel_url_unavailable` is a refusal with its own screen, not a generic
  "unavailable" (and the code stays in `KNOWN_CODES`).
- **Services** (`services/packages.py`, `services/tunnels.py`, `services/ports.py`)
  behind the existing `EnginePort` protocol; each runs the **ownership gate
  first** and validates locally (a package name cannot contain shell
  metacharacters because it reaches `pip`; a port must be 1024–65535).
  `PackagesService.install` returns an `InstallOutcome` carrying the workspace's
  `allow_net` so the result screen can explain a no-egress failure in words; the
  non-zero `exit_code` is the honest signal and is never turned into success.
- **Screens** (`presentation/screens/{packages,tunnels,ports}.py`): the installed
  list + shared tier; the install prompt / working state / **honest result**;
  remove-one / remove-all confirmations; the active tunnels list with a `LINK`
  button per URL and the three gate states; the publish-a-port chooser (detected
  ports or typed); the listening-ports list with a **separate unix section**, the
  **verbatim `reachability` blockquote**, a partial-view note on `complete:false`,
  and a refresh.
- **Wiring**: `handlers/features.py` (router registered before `admin`); a new
  `states/feature_input.py` FSM group for the three text prompts; three entries on
  the workspace-detail menu; and the workspace-create **advanced screen gained the
  `allow_net` and `allow_tunnels` toggles** (the domain `Limits` already carried
  them, so this was UI + handler wiring) — without it, 2b was unreachable.
- **The ephemeral-URL rule** is enforced: no persistence table or query mentions a
  tunnel; the URL exists only as a `LINK` button on the live screen. Asserted by
  `scripts/verify.sh` proof 7.
- **Catalog**: every new string in both `ar.py` and `en.py` (dotted keys, icons in
  the string, Arabic `is_rtl=True`, digits via the formatters), plus three
  field-specific `InvalidInput` messages (package/port) and three distinguished
  `tunnels_disabled` messages (server / `allow_tunnels` / `allow_net`).

## Phase 2d/2e/2g — metrics, processes, archives

Added additively in the same shape (port → adapter → service → screen → handler →
catalogs):

- **Adapter** (`adapters/cloud_service/`): `GET /metrics` (one-shot),
  `GET /processes`, `POST /processes/{id}/stop`, and the three archive routes.
  The archive upload is **multipart** and the export returns **raw ZIP bytes** —
  both bypass the JSON helper deliberately. `errors.py` already recognised every
  archive refusal code; the new routes reuse it.
- **Services** (`services/metrics.py`, `services/processes.py`,
  `services/archives.py`) behind the `EnginePort`; each runs the **ownership gate
  first** and validates locally: a metrics interval within the server's
  `min_interval_sec`/`max_interval_sec`; a process id matching `proc_<hex>`; an
  archive source path resolved through the existing file path rules; an upload
  under `archives.limits.upload_mb`. `ProcessesService.stop` folds a repeat
  stop's `404` back into the honest `gone` outcome.
- **Screens** (`presentation/screens/{metrics,processes,archives}.py`): the
  metrics panel renders a `*_complete:false` figure as **provisional with no
  percentage** and an idle workspace (RAM 0, no cgroups) as a normal state; the
  process inventory truncates+escapes `argv`, marks+explains `detached`, and the
  stop `gone` result is its own success screen; the archives menu offers
  upload+extract, extract-in-place and export, each with a **dry-run preview**,
  and the result screen lists refused entries with their own sentences.
- **Wiring**: `handlers/features.py` gained the metrics/processes/archives
  callbacks and the archive FSM states (`states/feature_input.py`); the
  workspace-detail menu exposes **📊 Resources**, **🧠 Processes** and
  **🗜 Archives**. `RichSender.send_document`/`download_document` are the
  **only** document seam, used solely by the exported ZIP and the uploaded
  archive.
- **The metrics "live" decision**: no SSE stream. A standing SSE connection does
  not survive a Telegram update loop and the same transport was measured
  unusable through the Cloudflare tunnel, so the panel is a one-shot snapshot
  with an explicit refresh button ("live" only while the user is watching).
- **RAR** is read-only by design: extract is supported; export is always ZIP and
  no code path offers a RAR encoder (`scripts/verify.sh` proof 13).
- Catalog: every new `metrics.*`, `process.*` and `archive.*` string plus the
  new field-specific `InvalidInput` sentences, in both `ar.py` and `en.py`.

## Phase 3 — the admin panel (`/admin`)

Delivered as an additive layer (migration → ports → services → screens →
handlers → catalogs), with the architecture the task's "قابلة للتوسع" asks for:

- **The gate is identity-based, not mode-based.** `cloudbot/middleware/admin_gate.py`
  is the single seam: a non-empty `Settings.admin_ids` (i.e. `is_admin(tg_id)`)
  opens the panel in **PRODUCTION**, where `admin_commands` is False. The gate is
  attached to the `message`/`callback_query` observers (an update-level middleware
  never sees a `Message`); a non-admin's `/admin` or `adm:` callback is swallowed
  and produces nothing — indistinguishable from an unknown command. The
  `ModeGateMiddleware` and the existing `/diag`/`/health` behaviour are left
  untouched; the identity gate recognises only the new panel commands (`/admin`,
  `/notifications`) and the `adm:` callbacks.
- **A section registry** (`presentation/screens/admin/section.py`): sections are
  data descriptors registered under `admin/`; the overview menu is generated from
  `registry.menu()`. Adding a section is one module plus one import — proven by
  `tests/unit/test_admin_registry.py`.
- **Screens are pure**: an `AdminView` read model (`domain/admin.py`) is built by
  `AdminService`; the screens are pure functions of it.
- **Actions are data** (`admin/actions.py`): `key`, label key, `Intent`,
  `dangerous`/`confirm`; a dangerous action's confirmation is generated from the
  same declaration and always names the subject by name and id.
- **D1 — migration `0002_admin.sql`**: the `events` table (indexed on `ts` and
  `tg_id`), `event_subscriptions`, `admin_notify_state` (cursor + pause + optional
  per-user scope) and `admin_mode` (persisted entered mode), plus a
  `users.display_name` column. Forward-only, idempotent, fail-fast; proven against
  an existing Phase-1 database in `tests/contract/test_admin_migration.py`.
- **D2 — services behind ports**: `AdminService` (read models + actions) and
  `NotifierService`, depending on `UserRepository`, `EnginePort`, `EventLog` and
  `AdminStateStore`. The admin ownership exemption is one named method,
  `OwnershipService.assert_admin_override`, called only from `AdminService`
  (asserted by an architecture test).
- **D3 — one event seam**: `EventSourceMiddleware` gives every update a
  `RequestEvents` intent buffer and flushes it after the handler succeeds;
  `EventsService` is the only writer. Handlers record *intents*
  (`events.workspace_created(...)`, `events.run_started(...)`, ...); the error
  guard turns a transient engine failure into `engine.unreachable` into the same
  buffer. Adding a category is a `domain/events.py` member plus one method — the
  dispatcher never changes.
- **D4/D5 — screens + registry + actions**: overview (honest "engine
  unreachable = unknown, not zero"), users list (paged + search + honest empty),
  user page (identity, workspaces + live usage from `/{id}/usage`, actions,
  notification scope, recent activity), entered-workspace view (reuses the user's
  workspace-detail screen; a persistent banner and a Leave button are added by the
  render seam so every reached screen is marked), notification settings
  (per-category toggles, all switch, pause, cursor, per-user scope), the audit log
  (paged, filterable), and the generated confirmations — every one in both
  locales, Arabic `is_rtl=True`, numbers/dates through the formatters.
- **Entered mode + audit attribution**: while entered, the admin's subject
  (`ctx.tg_id`) is the owner so the ordinary ownership gate passes and the right
  data is shown, while `ctx.admin_tg_id` is the admin. Every interaction is logged
  as `admin.action` with `actor = admin:<id>` and the subject `tg_id`/`ws_id` kept
  separate.
- **Notifications**: a persisted event log; per-admin subscriptions; a durable
  per-admin cursor; a dispatcher (in `bootstrap/notify.py`, the one place that may
  touch both the service and presentation layers) that renders a plan and commits
  the cursor only after delivery. The notifier coalesces per (admin, category) in
  a 60 s window, caps deliveries per flush and reports the real suppressed **event**
  count in one message, summarises a large backlog (or one accumulated while
  paused) in one gap message, and resets a cursor that is ahead of the log
  (backwards clock) with a warning. A `NotificationPoller` runs inside the bot
  process (started in `__main__`, stopped on shutdown) — never a separate
  long-poller.
- **D7 — docs**: `docs/ADMIN-PANEL.md` (the extension guide), `docs/ui/07-admin-panel.md`,
  this section, and the roadmap entry.
- **Task 07 — two live bugs, fixed structurally.** (a) The `adm:` callback shape
  is now owned by **one codec**, `presentation/screens/admin/callbacks.py`, with
  one typed builder and one total parser per action. Every producer (screen)
  builds callbacks through it and every consumer (`handlers/admin_panel.py`)
  parses through it; no handler does `cb.data.split(":")[n]` (asserted by
  `tests/contract/test_admin_callback_contract.py`). The parser returns an
  `InvalidAdminCallback` for a short/malformed callback instead of raising, and
  the handler answers it with the correctable stale screen — the live
  `adm:audit_user:<tg_id>` (no page) `IndexError` can no longer happen. The same
  audit also found and fixed two other raw-callback producers (`file:ls`/`file:cd`
  breadcrumbs and the run-mode cancel) that bypassed the codec. (b)
  `RichSender.edit` now suppresses only on the **full payload** — rich body, RTL
  flag and reply markup — so a changed keyboard with unchanged text is still
  sent, and it treats a `message is not modified` `TelegramBadRequest` as success
  (the payload is already on screen). Any other `TelegramBadRequest` propagates.
  Both fixes have bite tests in `scripts/verify.sh` (proofs 28–29).

## What is deliberately not implemented (and why)

- **Packages, tunnels, ports, metrics, processes, archives** — Phase 2a/2b/2c and
  2d/2e/2g, **now implemented** (see the sections above). **Subscriptions (2f)**
  are out of scope for this task — the operator is handling them himself; their
  error codes are already mapped and catalogued so the vocabulary cannot silently
  rot.
- **Per-user plans/quota/billing and real rate-limit tiers** — Phase 3; the seams
  (`services/*`, `users.plan`) exist. A simple token-bucket throttle is present.
- **File delivery as Telegram documents** — deferred (CB-14) for *general* files;
  binary files show metadata plus a "preview not available" note. The **only**
  two document paths implemented in Phase 2g are the exported ZIP and the archive
  the user uploads, through the single `RichSender` seam.
- **The metrics SSE stream** — the one-shot snapshot is implemented; the stream
  is deliberately not (see the metrics "live" decision above).
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
bash scripts/verify.sh                                             # the nine key proofs
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
