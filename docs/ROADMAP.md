# Roadmap

Phases are sequenced by dependency and value. Each phase is independently
shippable and testable. The engine contract (v0.0.20, `/v1`) is assumed
throughout; see `docs-cloud-service/INTEGRATION-GUIDE.md`.

---

## Phase 0 — Foundation (design, now)

Deliverable: this documentation set — architecture, client, persistence,
rendering, UI, decisions.

Exit criteria: the architecture, the rich-only contract, the i18n model, the
keyboard convention, and the exhaustive error/status vocabulary are all specified
and internally consistent, and match the engine's integration guide.

---

## Phase 1 — Core loop

Goal: a user can onboard, manage workspaces, browse files, and run code.

Scope:

1. **Bootstrap** — settings, modes, logging, composition root, polling, DB
   migrations, `/v1/meta` startup fetch.
2. **Onboarding & language** — language picker, main menu, settings, help,
   persistence of locale.
3. **Workspaces** — list, detail, create (name + profile + optional limits),
   suspend/resume/stop/delete, usage; ownership registry.
4. **Files** — browse (paged), breadcrumbs, view (text), mkdir, rename, delete,
   upload (with pre-check and progress).
5. **Execution** — exec (sync), runs (async) with the offset-log protocol and
   draft streaming; run history, stop, purge.
6. **Error/status mapping** — every code and status, AR + EN, tested.
7. **Modes** — PRODUCTION/DEV/DEBUG behavior and the admin gate.
8. **Tests** — unit (services, mapping, i18n), contract (adapter fixtures),
   architecture (import rule), golden (screens × locales × modes).

Exit criteria: end-to-end flow works for a real user; no raw engine output
anywhere (test-enforced); both locales complete; all destructive actions confirm;
all screens edit-in-place; all buttons follow the intent convention.

Engine endpoints used: `/v1/meta`, `/v1/workspaces*`, `/{id}/usage`,
`/files*`, `/exec`, `/runs*`. (INTEGRATION-GUIDE §2, §3, §4, §8.1.)

---

## Phase 1.5 — Interactive terminal (sessions)

Goal: a real PTY in Telegram, using the engine's native sessions API.

Status: **shipped** (the live engine on this host currently refuses `exec` and
`sessions` with `503 workspace cgroup unavailable`, an environment limitation —
see `IMPLEMENTATION.md` "Known gaps").

Scope:

1. `SessionStore` (offset cursor per session). — **done**
   (`adapters/persistence/sqlite_sessions.py`, port in `domain/ports/sessions.py`).
2. Engine client: create/input/output/resize/signal/delete. — **done**
   (`adapters/cloud_service/engine.py`, DTOs in `dto.py`).
3. Screens: open, terminal view (streamed via drafts or periodic edits), resize,
   signal buttons, close; concurrency cap (`409 too_many_sessions`) handled. —
   **done** (`presentation/screens/terminal.py`); the live view follows the
   offset cursor with bounded in-place edits.
4. Output uses the **same offset protocol** as run logs. — **done** (the session
   follower reads `{data, offset, next, eof, truncated}` and persists `next`).
5. Timeouts surfaced before they happen (idle/lifetime). — **done** from the
   engine's session configuration (`EngineMeta.sessions`), never hardcoded.

Explicitly **not** doing: hand-rolled PTY over a run/FIFO
(INTEGRATION-GUIDE §5.4 documents why it breaks). Asserted by a test and by
`scripts/verify.sh`.

Exit criteria: `cd`, `export`, job control and interrupts work through the UI; a
session ends and closes cleanly; ended sessions read as `session_ended`.

Engine endpoints: `/v1/workspaces/{id}/sessions*`. (INTEGRATION-GUIDE §5.)

---

## Phase 2 — Platform features

Each item is additive (port → adapter → service → screen → handler → catalogs).

- **2a. Python packages** — list, install (network-dependent, `exit_code`-honest),
  remove one/all. (`/packages`, `/packages/install`.) — **shipped**.
  `services/packages.py` validates a package name before it can reach `pip` (a
  name with shell metacharacters is refused locally). `install()` returns an
  `InstallOutcome` carrying the workspace's `allow_net` alongside the engine
  result, so a non-zero `exit_code` renders as a **failure screen** and, when the
  cause is no egress, says so in human words ("this workspace has no internet, so
  pip cannot reach an index") instead of only echoing pip. The raw pip output
  rides the DEBUG+admin collapsed block. The account for an install is slow
  (measured ~10 s) so the adapter bounds the call with a 300 s client timeout, and
  the handler shows a working screen and awaits it; no update-loop block.
  Screens: `presentation/screens/packages.py`.
- **2b. Tunnels** — create/list/delete quick tunnels; URL is **ephemeral**, only
  shown to the user as a `LINK` button, never cached; `504
  tunnel_url_unavailable` handled. (`/tunnels*`.) — **shipped**.
  `services/tunnels.py` validates the port (1024–65535) locally. The three gates
  (`tunnels.enabled`, `limits.allow_tunnels`, `limits.allow_net`) each resolve to
  their own sentence from the engine's `details.config`/`field`; the tunnels
  screen states the off switch and disables *Publish* until all three hold. The
  URL is placed **only** on a `LINK` button (asserted by `scripts/verify.sh`);
  no persistence layer mentions a tunnel. The **workspace-create advanced screen
  now carries an `allow_tunnels` toggle** (and one for `allow_net`), so publishing
  is reachable at create time. Screens: `presentation/screens/tunnels.py`.
- **2c. Port discovery** — list listening ports, with the engine's reachability
  disclaimer surfaced verbatim as a blockquote (they are **not** public).
  (`/ports`.) — **shipped**. `services/ports.py` is a read behind the ownership
  gate. The screen shows port/protocol/scope/owner, a separate `unix` section
  (a unix socket is not a port), a partial-view note when `complete:false`, never
  invents `LISTEN` for a UDP entry's empty state, and reports an unattributed
  socket instead of hiding or guessing the owner. Screens:
  `presentation/screens/ports.py`.
- **2d. Metrics** — one-shot `/{id}/metrics` and (optionally) the SSE stream for
  live resource display; `ram_percent`/`disk_percent`, `*_complete` honesty.
- **2e. Processes** — list and stop individual process trees (complements
  workspace stop-all). (`/processes*`.)
- **2f. Subscriptions** — create/extend with the duration grammar; locked
  workspaces show the renewal flow; `subscription_would_shorten` etc. handled.
  (`/subscription/extend`.)
- **2g. Archives** — upload-and-extract, in-place extract, export a directory;
  every stable refusal code mapped. (`/files/archive`, `/files/extract`.)

---

## Phase 3 — Product hardening

- **Plans & quota** — the bot's own per-user limits, enforced in services before
  engine calls; usage accounting (the engine has none — SPEC §2.6.1).
- **Rate limiting** — real per-user throttling and engine-call budgeting.
- **Billing** — entitlement model; ties to subscriptions.
- **File delivery** — send results/files back as Telegram documents (CB-14),
  with size caps and a safe path.
- **User timezones** — resolve CB-15's UTC-only display.
- **Admin tooling** — re-adopt orphan workspaces (CB-13), audit views, `/diag`,
  `/replay`.
- **Webhooks** — optional switch from polling (`bootstrap/app.py`).
- **Postgres + Redis** — swap adapters for horizontal scale (CB-11/CB-22).

---

## Non-goals (all phases)

- Reimplementing the engine's isolation or inventing a sandbox.
- Exposing the engine API to users or Telegram clients.
- A general Telegram framework; this is a product.
- Hand-rolled PTY, FIFO terminals, or anything INTEGRATION-GUIDE §5.4 warns
  against.

---

## Traceability

| Phase | Docs | Engine sections |
| --- | --- | --- |
| 1 | UI 01–06, client, persistence, rendering | §2, §3, §4, §8.1 |
| 1.5 | UI 04/06 (sessions), client §4.5 | §5 |
| 2 | UI/status docs extended per feature | §4.3, §7, §8.3–8.5, §6, client §4.6 |
| 3 | DECISIONS CB-11/13/14/15/22 | SPEC §2.6.1 |
