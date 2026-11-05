# Decision Record — cloud-bot

Every non-obvious call we made, why, and what it costs. Numbering is local to this
project (`CB-n`); it does not continue the engine's ADR sequence. Where a decision
follows from engine documentation, the source is cited.

Status legend: **Accepted** (locked), **Provisional** (revisit later),
**Deferred** (out of current scope).

---

## CB-1 — Layered hexagonal architecture with a dedicated presentation layer

**Decision.** Adopt `core → domain → services → (presentation, adapters) →
handlers → bootstrap`, with dependencies pointing inward and a dedicated
`presentation` layer for rendering.

**Why.** Three forces:
1. Rich-only output and a uniform keyboard convention are absolute, so message
   production deserves one owned layer rather than being scattered in handlers.
2. The engine is swappable in principle (a web console may reuse `domain`), so
   `domain` must not import `httpx` or aiogram.
3. The user explicitly asked for easy extension and maintenance.

**Consequences.** One extra indirection (screens are pure functions of entities).
In exchange, adding an endpoint is a checklist (port → adapter → service → screen
→ handler → catalogs), each step independently testable. An architecture test
enforces the import rule.

**Alternatives rejected.** Feature-folder (handlers own everything): fast to start,
but rendering and engine types leak everywhere. MVC around aiogram: no clean place
for the isolation gate or the translation layer.

---

## CB-2 — Bot-side ownership registry is the isolation boundary

**Decision.** One Cloud Service service key authenticates the bot as a single
`principal_id`; the bot stores `tg_id -> [ws_...]` in its own DB and enforces
ownership in `services/ownership.py` on every workspace-scoped call. A
non-owned id is treated exactly like `not_found`.

**Why.** The engine checks `workspace.owner_id == principal_id`, but every
workspace the bot creates has the *same* principal, so the engine cannot separate
our users (SPEC §2.6; ARCHITECTURE §1). Billing/accounts/quota/rate-limiting are
explicitly frontend responsibilities.

**Consequences.** The bot DB is now security-critical; losing it loses the
ownership map (CB-13). Every service method must call the gate — enforced by a
table-driven test asserting a foreign `tg_id` never reaches the engine.

**Alternatives rejected.** Per-user principal keys: requires operator edit of
engine config per user, static and unshippable. Trusting the engine: impossible
with a single principal.

---

## CB-3 — Rich-only output; `edit_message_text(rich_message=...)` as the edit path

**Decision.** All output uses `send_rich_message` / `edit_message_text(
rich_message=InputRichMessage(...))`. Never `send_message(parse_mode=...)`.

**Why.** Product requirement. Verified by introspection of `aiogram==3.31.0`:
`send_rich_message` exists and accepts `reply_markup`; **`edit_rich_message` does
not exist**; `edit_message_text` accepts `rich_message` and is documented to edit
"text, rich and game messages".

**Consequences.** In-place updates (menus, live logs, progress) go through the
documented rich-capable edit method, wrapped in `RichSender.edit`. If aiogram adds
`edit_rich_message`, one method changes. Telegram's "message not modified" error is
avoided by caching the last HTML per message.

**Alternatives rejected.** Editing via `send_message(parse_mode=...)`: forbidden.
Deleting and resending to "edit": spams the chat and loses message position.

---

## CB-4 — Draft streaming (`send_rich_message_draft`) for live output

**Decision.** Stream live run logs with `send_rich_message_draft` (ephemeral ~30 s,
`can_stop=True`), then persist the final document with `send_rich_message`.

**Why.** Drafts give a smooth animated live view without posting a new message per
tick, and the built-in stop button maps to a `stopped_message_generation` update we
can honor cleanly. This is a feature the environment actually provides (verified).

**Consequences.** A dedicated tail loop per streamed message; must always finalize
because drafts vanish. Stopping the *view* is distinct from stopping the *run*
(only the explicit **⏹ Stop** action stops the run).

**Alternatives rejected.** Edit-per-tick: jarring animation and possible
"not modified" churn. Send-per-tick: message spam.

---

## CB-5 — Run output is always read by offset, never from the run object

**Decision.** Never read `stdout`/`stderr` from a run; always poll the logs
endpoint with `{data, offset, next, eof, truncated}`.

**Why.** The engine's run object has **no** stdout/stderr (INTEGRATION-GUIDE §4.2);
reading it yields empty output and is a known downstream failure.

**Consequences.** Every execution screen needs a small offset-follow helper in
`services/execution.py`. `eof` means "read so far", not "finished" — we also poll
`status`. `truncated` is always surfaced.

---

## CB-6 — Language as a structural concern, not a translation pass

**Decision.** `Locale` lives in `core`; every screen factory and the sender take
it; `is_rtl` is derived in `RichDoc.build()`; numbers/dates/plurals are localized
by formatters; key parity is tested.

**Why.** Arabic is RTL with six plural forms and Arabic-Indic digits; embedding LTR
tokens (filenames, ids) in Arabic prose reorders visually unless handled. A
post-hoc translation would produce broken layouts and missing strings.

**Consequences.** More design up front (formatters, RTL handling of code/paths,
catalog keys), but a new language is a catalog file plus an enum member. Mixed-
direction safety is centralized in `presentation/rich/tags.py`.

---

## CB-7 — Intent → style button convention

**Decision.** Screens declare `Intent` (`PRIMARY/SUCCESS/DANGER/LINK/MUTED`);
`styles.py` maps to `ButtonStyle`/`disabled`. Suspend is `PRIMARY`; Stop/Delete are
`DANGER`; Resume/Create/Run are `SUCCESS`.

**Why.** Uniform color semantics across every screen; a screen author cannot pick
a wrong shade.

**Consequences.** A short rule set to remember; a golden test asserts styles match
the table and that every `DANGER` button is behind a confirm.

---

## CB-8 — Callbacks carry tokens, not entity ids

**Decision.** `callback_data` is `action:token:extra` with a compact token
indexing FSM context; full `ws_`/`run_` ids live in state.

**Why.** Telegram caps `callback_data` at **64 bytes**; a naive id-bearing callback
(`run:logs:<ws>:<run>:stdout:0`) overflows because `ws_`+ULID is ~29 bytes.

**Consequences.** A small context stack per user; the codec asserts ≤ 64 bytes at
build (raises in DEV/DEBUG, safe-truncates + logs in PRODUCTION).

---

## CB-9 — Cache `/v1/meta`, refresh on stale-value refusals

**Decision.** Read `/v1/meta` at startup; re-read on a `422`/`409` that suggests a
stale cached value; drive every picker/cap from the cache.

**Why.** INTEGRATION-GUIDE §1: never hardcode limits/profiles/interpreters;
requests above maxima are refused, never clamped. Offering a refused value is a
hard failure for the user.

**Consequences.** One meta cache with an async lock; a test simulating a `422`
asserts a refresh + retry.

---

## CB-10 — Modes as an edge-only behavior table

**Decision.** `PRODUCTION`/`DEV`/`DEBUG` change only logging, error verbosity,
admin access, mock engine, throttling, and reachable screens. Raw output is
DEBUG+admin only, collapsed, never primary.

**Why.** Maintenance needs deep visibility; users must never see raw output. A
single `modes.py` table makes every behavioral difference reviewable in one place.

**Consequences.** Golden tests run per mode; a PRODUCTION snapshot containing a raw
block fails CI. `DEBUG` with no admin ids is a fatal config error.

---

## CB-11 — SQLite now, repository-abstracted

**Decision.** `aiosqlite` + WAL, behind `UserRepository`/`SessionStore` ports.

**Why.** Simplest correct deployment for an early single-instance bot; the ports
make Postgres a one-line swap in `bootstrap/container.py` with no service/screen
changes.

**Consequences.** WAL backup nuance (`VACUUM INTO`); a future Postgres adapter and
Redis FSM are additive. Accepted by the user (infra answer).

---

## CB-12 — Polling, not webhooks (Phase 1)

**Decision.** Use `Dispatcher.start_polling()`.

**Why.** No public HTTPS endpoint required to run the bot; simplest operation for
early stage. Webhooks are a deployment swap (`bootstrap/app.py`) when needed.

**Consequences.** Slightly higher latency and a persistent process; fine for
Phase 1. The repository and app wiring already isolate the choice.

---

## CB-13 — The bot DB is security-critical and must be backed up

**Decision (risk accepted).** Ownership mapping lives only in the bot DB. Loss of
the DB orphans workspaces to users (the engine still holds them).

**Why.** Storing ownership engine-side is impossible with one principal.

**Consequences.** Document backup (WAL-safe snapshots). A Phase 2 admin tool may
re-adopt orphan workspaces by id. Recorded as a known operational risk.

---

## CB-14 — Binary/large file delivery deferred

**Decision.** Phase 1 renders file **metadata** and previews **text** only;
binary/large files show a "preview not available" note. Document delivery (sending
the actual bytes back as a Telegram document) is Phase 2.

**Why.** Rich messages display text and media, but delivering arbitrary file bytes
is a separate concern (download endpoint, size caps, `send_document` which is
outside the rich-only path and thus needs explicit design).

**Consequences.** Honest UI ("not available") rather than a broken preview. Noted
so we do not silently lose the feature.

---

## CB-15 — All timestamps from the engine are rendered as-is (UTC)

**Decision.** v1 renders engine RFC3339 UTC timestamps in the user's locale
**format** without timezone conversion.

**Why.** The engine emits UTC (SPEC §2, RFC3339 UTC); the bot has no reliable user
timezone yet, and guessing is worse than a consistent, labeled UTC-format display.

**Consequences.** "Created" dates are UTC-format. A user-timezone setting is a
Phase 2 nicety. Recorded so it is a conscious choice, not a bug.

---

## CB-16 — Sessions (interactive terminal) designed now, built Phase 1.5

**Decision.** The `sessions` engine API is fully specified in the client doc and
data-flow doc, but sequenced **after** the core loop (workspaces/files/exec/runs).

**Why.** The user asked to sequence it after the core without dropping it. The
engine's native PTY API is the supported way and must not be hand-rolled
(INTEGRATION-GUIDE §5.4).

**Consequences.** `SessionStore`, session DTOs, session screens, and offset-based
output are documented and ready; build follows the core.

---

## CB-17 — No raw engine output; exhaustive vocabulary

**Decision.** A single document (`ui/05`) plus `presentation/mapping` covers every
`error.code`, `run.status`, `workspace.status`, `summary.state`, `session.status`,
and `reason`, in AR and EN. An unknown future code maps to a safe generic and
fails the CI exhaustiveness test.

**Why.** The user's strongest requirement: users see human words; no JSON, no
engine English. An exhaustive, tested mapping is the only way to guarantee it.

**Consequences.** The mapping and catalogs must stay in lockstep; both are tested.

---

## CB-18 — Rich messages standardize on the `html` field

**Decision.** Use `InputRichMessage(html=...)` exclusively; do not mix `markdown`
or `blocks` in one message.

**Why.** HTML composes trivially, matches all the tags we need (headings, tables,
details, pre/code), and keeps one escaping path (`tags.py`). `blocks` are available
later if a screen benefits.

**Consequences.** All authoring is HTML strings via `RichDoc`; escaping is
centralized and adversarial-input tested.

---

## CB-19 — English is the default locale before onboarding

**Decision.** A brand-new user's very first card is the bilingual language picker
(sent LTR); English is the fallback if no choice is recorded.

**Why.** The picker must be readable to someone who cannot read the current
language, so it shows each language in its own script and is inherently bilingual.
English is the conventional fallback.

**Consequences.** The picker is the one screen sent `is_rtl=False` regardless of
locale.

---

## CB-20 — `exec` and runs are distinct, and the UI says so

**Decision.** Present `exec` (sync, ≤60 s, no run record) and `runs` (async,
offset-readable logs, stoppable) as different tools, and never link an `exec`
result to run history.

**Why.** SPEC §2.5: `POST /exec` creates no run record; only `POST /runs` persists
a run. Pretending otherwise would confuse and disappoint.

**Consequences.** The run-mode chooser explains both; exec screens omit history
links.

---

## CB-21 — Telegram-side rich-message limits are designed within

**Decision.** Cap displayed output (~12 KB), page lists, chunk oversized values
into `<details>`, and stay far under 32,768 chars / 500 blocks.

**Why.** Bot API rich-message limits (text 32,768; blocks 500; nesting 16; table
columns 20). Exceeding them is a hard send failure.

**Consequences.** Trim/collapse helpers in the rich layer; always disclose
truncation.

---

## CB-22 — Timezone, multi-instance, and horizontal scale are out of scope for v1

**Decision.** Single-instance bot, in-process FSM (memory in DEV, SQLite in PROD),
no distributed scheduling.

**Why.** The engine is single-node and static-key (SPEC §0), and the bot is its
frontend; premature distribution adds cost with no benefit yet.

**Consequences.** Scaling later means Redis FSM + a shared job runner; the port
boundaries make that additive.

---

## CB-23 — SQLite runs in autocommit; multi-statement writes are idempotent upserts

**Decision.** One shared aiosqlite connection per process, opened with
`isolation_level=None` (autocommit). No explicit `BEGIN`. Any operation that must
touch several rows is expressed as `INSERT ... ON CONFLICT` upserts.

**Why.** aiogram processes updates concurrently, so two handlers can overlap on
the single connection. An explicit `BEGIN` in `get_or_create` raised
`sqlite3.OperationalError: cannot start a transaction within a transaction`
whenever a second first-message arrived mid-transaction — the live crash this
fixed.

**Consequences.** Each statement is individually atomic; cross-statement atomicity
is replaced by idempotency (`ON CONFLICT DO NOTHING` / `DO UPDATE`). Moving to
Postgres (or a connection pool) restores real transactions without changing the
services.

---

## CB-24 — A rich message carries one markup; the empty board keeps its inline keys

**Decision.** The first-run welcome board (no workspaces) is sent with its own
**inline** keyboard (Start, How it works). The persistent **reply** menu is
attached only once the user owns a workspace.

**Why.** `send_rich_message` takes a single `reply_markup`; attaching the reply
menu overwrote `screen.markup`, so the inline Start button never appeared. Inline
keyboards cannot be introduced by an edit either, and the welcome board must be
tappable immediately.

**Consequences.** Onboarding is `[language card] → [welcome board] → tap Start →
create wizard`. The reply menu arrives with the workspace menu. Nothing is lost:
the welcome board's "How it works" opens `/help`, and Start opens the wizard.

> **Superseded in part by CB-25 / CB-26.** The persistent reply menu no longer
> exists; navigation is a shared inline block. The welcome board still keeps its
> own inline Start button, which is why CB-24's rule holds.

---

## CB-25 — Navigation is a shared inline row at the bottom of every top-level screen

**Decision.** There is **no reply keyboard anywhere, ever** — every button is an
inline button. Every top-level screen (main menu, welcome board, help, settings,
workspaces list, language picker) ends with one shared **nav block**: the four
destinations Workspaces (`ws:list`), Settings (`nav:settings`), Language
(`nav:language`) and Help (`nav:help`) — plus Menu (`nav:menu`) where the screen
is not the menu itself — laid out **two buttons per row, at most three rows**.
**A nav entry never points at the screen you are already on: the current
destination is omitted, not muted (task 14, R1).** A disabled entry was tried
first (task 09); it is wrong because a button-shaped no-op still wastes a grid
slot and reads as a destination. The workspaces list also drops Menu, whose body
duplicates the list (R2). The main menu's own primary action (`➕ New Workspace`) sits full width
*above* the block. One helper (`presentation/screens/nav.py`) renders it, so every
screen agrees.

**Why.** He asked for no reply keyboard and a *smart placement with good
distribution*. The bottom of the message is where a thumb already expects
navigation. Because the old reply keyboard was *persistent*, the user could reach
any destination from anywhere; an inline block restores that reachability from
every top-level screen while never scrolling away or stacking up, and it can be
edited in place. Two columns across two-to-three short rows is the densest layout
that keeps every label readable on a phone.

**Consequences.** The catalog keys `menu.placeholder` and `menu.my_workspaces`
(both tied to the old reply menu) are deleted, along with `welcome.how` (now
covered by the Help destination). An architecture test forbids
`ReplyKeyboardMarkup`/`KeyboardButton` anywhere and permits one
`ReplyKeyboardRemove` (CB-26). Screen-specific keyboards (workspace detail, files,
runs) keep their own layout; only the top-level surfaces carry the nav block.

---

## CB-26 — One `ReplyKeyboardRemove` on `/start`, and only there

**Decision.** On `/start` for a returning user, the bot sends exactly one extra
message carrying `ReplyKeyboardRemove`, then the menu (or the welcome board). It
is never sent on any other screen, never more than one per `/start`, and the
screen's own inline keyboard is never displaced — the removal rides its own
message with an invisible zero-width body.

**Why.** An inline keyboard cannot remove a persistent reply keyboard a user
already has installed from an older build, so it would stay stuck at the bottom
of their chat forever. `/start` is the natural migration point: it is where a
user returns to re-anchor the bot. Doing it once bounds the clutter to a single
extra message.

**Consequences.** A legacy user who sends `/start` gets a clean chat and the new
inline navigation. New users never see a reply keyboard, so for them the removal
is a harmless no-op. The removal is guarded by tests (fires on `/start`, not on
`/help`, `/settings`, or a nav callback) and by the architecture guard (CB-25).

---

## CB-27 — Every refusal renders a sentence; the tunnel limit is reported, not hardcoded

**Decision.** On the publish path, **no engine refusal is silently swallowed**.
Each known `engine_code` (`too_many_tunnels`, `tunnel_url_unavailable`,
`tunnels_disabled`) maps to a dedicated, rendered screen with the cause, the fix
or the retry, and a back path; an **unmapped** code is re-raised to the error
screen. The per-workspace cap is read from `/v1/meta`
(`tunnels.max_per_workspace`) and displayed ("N of M") — it is never a constant
in the bot. At the cap the publish entry is a disabled button whose label names
the reason. A non-listening port gets a one-sentence warning in a confirm, never
a block.

**Why.** Measured live: publishing a third tunnel against the real engine
(`tunnels.max_per_workspace: 2`) produced `too_many_tunnels`, which the handler
folded into a plain list re-render — the catalog sentence existed but was never
shown. The user saw the publishing screen and then the list, with no explanation
("بيقولي انه بيجيب رابط بس مش بيحصل حاجه"). Two more defects compounded it: the
screen never stated the ceiling (so it was invisible until hit), and nothing said
a link only works once something listens on the port. The general rule the user
asked for — *every* refusal must explain itself — turns a dead end into a
recoverable one, and a data-driven test over the refusal codes stops a future
code from regressing to silence.

**Consequences.** The refusal/publish logic moved behind two functions
(`_unlistened_warning`, `_tunnel_refusal_screen`) so the mapping is testable
without a dispatcher; `TunnelsService` caches `/v1/meta` (one call, not one per
render) and exposes the cap. Goldens cover the list below/at the cap, the refusal
and timeout screens, and the warning confirm, in both locales. A bite test
(proof 35) proves the dedicated refusal screen is load-bearing.

**Alternatives rejected.** A toast-only refusal: the rule is that the *screen*
carries the full explanation. Hardcoding `2`: the engine's cap is
config (`config.yaml`), and the integration guide says never hardcode
limits/versions. Blocking a non-listening port: publishing before the server is
running is a legitimate workflow.

---

## Open questions deliberately left open

- **Provisional:** exact plan/quota model and per-user rate limits (Phase 2;
  owned by the bot). The seam (`services/*` + `users.plan`) exists.
- **Deferred:** file delivery as Telegram documents (CB-14), user timezones
  (CB-15), tunnels/packages/ports/metrics/subscriptions (ROADMAP).
