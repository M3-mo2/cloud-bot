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

## CB-8 — Callbacks carry their key parts inline; a token only when they cannot

**Decision.** A callback is `action` (= `prefix:name`) plus its key parts: inline
as `action:part:part` when that fits Telegram's **64-byte** cap, and only as
`action:~token` (a short content-addressed hash) when it truly does not. There is
no other encoding.

**Why.** Telegram caps `callback_data` at 64 bytes, and an earlier version of this
rule routed **every** key-bearing callback through a token held in a
**process-global, in-memory** registry. That registry dies with the process, so a
button drawn before a restart decoded to `key=()` and the handler's
`decode(cb.data).key[0]` raised `IndexError` — for *every* prefix (`ws:`, `file:`,
`term:`, `run:`, …). The 64-byte cap is exactly why most callbacks should travel
inline: `ws:view:ws_…` is ~38 bytes, well under it. Inline callbacks are
**restart-proof by construction**; only a callback that genuinely overflows (a
workspace **and** a session id, `run:logs:<ws>:<run>`, a file path with colons)
falls back to the token.

**Consequences.**

* `decode` is **total**: it never raises, and always returns the action (read from
  the callback's own first two segments) plus a key tuple — empty when an inline
  key is missing or a token is unknown. A handler that *needs* key parts calls
  `decode_required(data, arity)`, which raises `StaleCallback` instead of letting
  the handler index `key[0]`. The error guard maps `StaleCallback` to the
  correctable `err.stale.*` screen, so a stale button always renders something.
* The codec asserts ≤ 64 bytes at build (raises in DEV/DEBUG, safe-truncates +
  logs in PRODUCTION).
* A callback is only tokenised when inline is impossible, so a restart can no
  longer kill navigation. `tests/contract/test_callback_restart_contract.py`
  walks every screen's every button through the real dispatcher with the registry
  **reset** (and warm) and asserts no tap raises and none renders nothing.

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

## CB-28 — Terminal input is sticky; the follow is cut short on idle and superseded by the next command

**Decision.** Entering the interactive terminal's command mode is **sticky**: one
tap on **⌨️ Send a command** and plain text messages keep going to that session
until the user leaves. There are exactly three exits — the one-tap **⏹ Stop
typing** control, a **known slash-command** (which is cleared *before* the
routers run, so it can never be swallowed), and the **session ending** (which
clears the state and renders the ended screen). While a command's output is being
followed, a newer plain message **supersedes** the in-flight follow: it claims a
new generation and the older follower stops at its next tick, *before* its next
engine read. Independently, a follow **cuts short on idle** — once the session
produces no new output for a couple of consecutive polls, it stops.

**Why.** He reported it verbatim: *«عايز الترمنال اقدر اكتب فيها علطول مش كل مره
ادوس ارسال امر»* — the old handler ran `await state.clear()` after the first text
message, so every command cost a re-tap. Measured by the previous run: one tap,
then three consecutive plain messages each reached `send_line`. The R7 choice was
forced by a **measurement**: each follow was bounded by
`FOLLOW_MAX_ITERATIONS × FOLLOW_POLL_INTERVAL` (30 × 1 s) and a PTY rarely emits
for the whole window, so a plain message took **~30 s** to handle
(`duration_ms=30089` in the log). With sticky input the user types the next
command while the bot streams the first, so that stall is a UX defect, not
theory. *Cut short on idle* fixes the common case without weakening the
bounded-engine discipline; *supersede* keeps a user's newer command authoritative
when a follow is still in flight, so a command with long-running output is never
displaced by the follower of an older one, and the engine is never polled by two
followers at once.

**Consequences.** `_read_all` now also returns the offset `next`, and `_stream`
tracks it to detect idle; both follow-limit checks sit *before* the read. Tests
pin: three consecutive messages after one tap; the state not cleared after a
command; `/start` and `/help` clearing the state without reaching the session;
the stop-typing control; a foreign user refused (`NotOwned` clears the state); an
ended session rendering the ended screen, clearing the state and not raising; the
view still carrying its buttons; the idle cut-short (a stale follower makes **no**
read); and the new-command supersede (the two followers stay near one budget).
Proof 36 in `scripts/verify.sh` restores the old `state.clear()` and shows the
sticky test go red. The async failure path question — that aiogram's default
`handle_as_tasks=True` means updates for one chat overlap — is *why* the
generation map exists at all.

**Alternatives rejected.** *Serialise* (queue commands behind the running
follow): it keeps the 30 s stall and hides it, and would need a "bot is busy"
affordance. *Cancelling* the in-flight follow with an `asyncio.Task.cancel()`:
the generation check is simpler, needs no task bookkeeping, and is testable
without scheduling. *Unbounded streaming until the PTY goes quiet for a long
time*: it risks a long hold on a session whose command is interactive (an editor
waiting at a prompt), so the idle window stays deliberately short.

**Why the FSM is not persisted, so the way back in must stay easy.** The sticky
state lives in aiogram's `MemoryStorage` ("DEV/DEBUG use memory FSM; PRODUCTION
would use SQLite", `bootstrap/app.py`). It therefore does **not** survive a
restart or redeploy — by design, since it is transient input focus, not product
data (the *workspace session* is the column-not-FSM record of CB-16). The
consequence is deliberate: because the mode can vanish silently on a restart,
the way back in must be **one tap** from the ordinary terminal view, and every
screen the mode shows keeps the terminal buttons, so no one is ever trapped or
stranded.

---

## CB-29 — The error guard stays registered once, at the update level, and unwraps the `Update`

**Decision.** `ErrorGuardMiddleware` remains a **single** middleware on
`dispatcher.update` (not duplicated onto the `message` and `callback_query`
observers). Because an update-level aiogram 3.x middleware receives the
`TelegramObject` `Update`, `_render` **unwraps** it
(`event.callback_query or event.message`) before its `isinstance` checks, then
edits a callback's message or sends to a message's chat.

**Why.** The audit (F-01) measured that the guard rendered *zero* screens for a
re-raised `DomainError`, because `_render` was written for the per-event object
while the guard was fed an `Update`. Two shapes fix it: unwrap in `_render`, or
register the guard on both observers (as `AdminGateMiddleware` is). The unwrap
keeps a single outermost catch: one registration means a `DomainError` raised
anywhere below the update is caught exactly once and rendered exactly once,
whereas two observer registrations would wrap two nested delivery paths and
invite a double render (or a missed one) whenever the two disagree. It is also
the smaller diff and keeps the guard's own `_render` unit-testable without a
dispatcher.

**Consequences.** Every refusal (invalid input, `NotOwned`, an unmapped engine
code) now renders one screen on both callbacks and messages. The isolation rule
is preserved: `NotOwned` and a missing id both map through
`mapping/errors.view_for_error` to the **same** `not_found` board, asserted
byte-identical in `tests/unit/test_port_publishing_fixes.py`. Proof 37 in
`scripts/verify.sh` drops the unwrap and shows the F-01 test go red.

**Alternatives rejected.** Registering on both observers: two seams, and a
genuine risk of double-rendering the same refusal. A new `DomainError` mapping
per event type: the guard is the one place a `DomainError` becomes a screen
(CB-27), and that must not be duplicated.

---

## CB-30 — The tunnel publish guard lives in the service; it de-dups a port and serialises creates

**Decision.** `TunnelsService.publish` is **idempotent per port** and
**serialised per `(tg_id, ws_id, port)`**. Under an `asyncio.Lock` keyed by that
triple it first lists the workspace's tunnels; if one already carries the port it
raises `TunnelAlreadyPublished(existing)` (a plain exception, not a `DomainError`
— it is a legitimate idempotency outcome, not a refusal) instead of calling
`create_tunnel`. Only when no tunnel holds the port does it create one. The
handler renders the existing link with a one-sentence note
(`tunnel.already_published`, both locales). A failed create exits the `async
with` block, releasing the lock, so a retry works.

**Why.** The audit (F-03) confirmed on the real engine that a double-tap of the
same publish button creates a **second** tunnel on the same port; the cap is
`tunnels.max_per_workspace: 2`, so one port burns the whole allowance. The fix
is at the **service** layer, not only the UI, so it also covers a retried
callback and any other caller. A per-user-per-workspace key means the guard
cannot leak across users or workspaces, and the lock covers a create that is
*already in flight* (a create is slow — `url_timeout_sec`, 45 s — so the two
taps genuinely overlap). The list-then-create under the lock is the client-side
half of the idempotency the engine itself does not offer (INTEGRATION-GUIDE has
no per-port de-dup).

**Consequences.** Two rapid publishes produce one engine create; a sequential
second publish surfaces the existing link; a failed create is retryable; two
different ports still create two tunnels; two users do not interfere. Pinned by
`tests/unit/test_port_publishing_fixes.py` (both at the service layer and
through the real dispatcher).

**Alternatives rejected.** A UI-only disabled button: does not cover a retried
callback or a second tap before the first repaint. A global lock: would serialise
unrelated workspaces and users. Checking existence without the lock: a
time-of-check/time-of-use race between the two overlapping creates. Making
`TunnelAlreadyPublished` a `DomainError`: it would route through the error guard
and read as a refusal, when the request genuinely succeeded (the link exists).

---

## CB-31 — The publish gate includes the workspace lifecycle status

**Decision.** The tunnels screen is gated by **four** conditions, not three. In
addition to the server switch (`tunnels.enabled`) and the two workspace switches
(`limits.allow_tunnels`, `limits.allow_net`), the workspace's **lifecycle
status** is consulted first: only `active` may publish. `suspended`, `locked`,
`deleted` and `unknown` all mute **➕ Publish a port** and show a blockquote that
names the status (`tunnel.gate.{suspended,locked,deleted,unknown}`, both
locales). The lifecycle gate is evaluated **before** the switch gates, so a
paused workspace says it is paused rather than naming a tunnel switch that may
also be off. The handler passes the real status (read from the workspace, exactly
as `workspace_detail_markup` already does for Run/Terminals) into
`_tunnels_screen`.

**Why.** The audit (F-04) measured that a suspended/locked workspace still
showed an enabled Publish button; tapping it reaches the engine and is refused
with `409 workspace_suspended` / `403 subscription_expired` — an action that
cannot succeed. `workspace_detail_markup` already disables Run/Terminals for
those statuses; the tunnels gate was built around the three tunnel switches and
never extended to the lifecycle. Naming the status (not just "disabled") keeps
the rule that every muted control carries a reason.

**Consequences.** The list is muted with a status-specific sentence for every
non-active status, in both locales; an active workspace renders no lifecycle
sentence. Goldens cover suspended/locked/deleted in both locales.
`tests/unit/test_tunnel_port_fixes.py` asserts the publish entry's enabled state
and the reason sentence for every `WorkspaceStatus`, and the real dispatcher path
for a suspended workspace.

**Alternatives rejected.** Trusting the engine's refusal alone: with the
lifecycle gate the user never taps a button that cannot work, and the audit's
F-04 is exactly that dead-end. Folding the status into one generic "disabled"
sentence: the rule is that the reason names the cause, and a paused vs. locked
workspace have different fixes (resume vs. renew).

---

## CB-32 — Every reported listening port is offered; the owner is display-only

**Decision.** The publish picker offers **every** TCP port the engine reports
(`protocol in {tcp, tcp6}`), including one with `attributed:false`. Attribution
(`pid`/`kind`) is used **only** to label the entry — an unattributed port is
marked "owner unknown" (`tunnel.publish_unattributed_port`) in both the body list
and its button, never dropped. The engine's order is preserved. UDP sockets are
not offered: the engine reports no `LISTEN` state for UDP, so they are not
listening TCP ports.

**Why.** The audit (F-06) measured that `_detected_ports` filtered
`if p.attributed`, so a genuinely open port with no discoverable owner was
silently absent from the picker — while the ports screen *did* show it as "owner
unknown". The two screens disagreed, the engine accepts a create for such a port,
and the 2c design rule is that an unattributed socket must "never be hidden or
guessed". Attribution is an implementation detail (whether the scanner resolved
the owning pid), not a statement about whether the port is open.

**Consequences.** `_detected_ports` returns a `DetectedPort(port, attributed)`
record (not a bare int); the picker marks an unattributed entry and keeps its
`tnl:port` button, so it is publishable. `scripts/verify.sh` proof 38 restores
the `attributed` filter and shows the F-06 test go red. The fake engine and the
goldens carry an unattributed `LISTEN` port.

**Alternatives rejected.** Keeping the filter and relying on the typed-port path:
the user had to know an undisplayed number, and the two screens contradicted each
other. Guessing an owner: the 2c rule forbids inventing one; the entry is marked
unknown instead.

---

## Open questions deliberately left open

- **Provisional:** exact plan/quota model and per-user rate limits (Phase 2;
  owned by the bot). The seam (`services/*` + `users.plan`) exists.
- **Deferred:** file delivery as Telegram documents (CB-14), user timezones
  (CB-15), tunnels/packages/ports/metrics/subscriptions (ROADMAP).

## CB-33 — a URL button carries no `style`, and `"link"` is not a style value

**Decision.** `ALLOWED_STYLES` is `{primary, success, danger}`. A `Intent.LINK` maps to **no style**, and the
URL-button factory no longer falls back to `"link"`.

**Why.** Measured against the live Bot API: sending `style="link"` on an inline button — with a `url` **or** with
`callback_data` — fails the **entire message** with *can't parse InlineKeyboardButton: Invalid button style
specified*, while a URL button with no style at all (or `primary`/`success`/`danger`) is accepted. The bot put
`style="link"` on every tunnel LINK button, so publishing a port created the tunnel and then the screen carrying
the link was refused — the user saw nothing, which is exactly the reported symptom.

**Why it survived every gate.** `InlineKeyboardButton.style` is typed `str | None`, so a value the server rejects
is not a type error; `ALLOWED_STYLES` had been built from `ButtonStyle.LINK.value` (an aiogram enum that is not
the server's contract); and the repo **asserted the defect** in three places — `test_only_four_styles_exist`,
`test_intent_to_style`, and `verify.sh` proof 7 (`b.style == "link"`). A wrong premise locked by an assertion is
invisible to every local gate.

**What now pins it.** `tests/live/test_live_telegram_markup.py` sends the real markup to the real Bot API (with a
bogus chat id, so nothing is delivered) and asserts Telegram parses it — the only instrument that can catch a
server-side contract, since no local rule knows what the server accepts. Plus a per-button guard over every screen
in `SCREEN_BUILDERS`, and `verify.sh` proof 27 (a bite test that reintroduces `style or "link"`).

## CB-34 — The screen catalog lives in the product, and one walk serves the tool and the tests

**Decision.** The registry of every screen, its **section** grouping, and the fixed fixtures its builders need
live in **production code** (`cloudbot/presentation/catalog.py`) — not in a test. The verdict logic that walks a
screen's buttons and classifies each one lives there too, with the caller supplying the real dispatcher's own
routing as a predicate. The golden test, the button contract tests, the keyboard tests and the new admin-only
`/dev` inspector all **consume** this one catalog.

**Why.** The 111-screen catalog *existed* but was buried in `tests/golden/test_golden_screens.py`. Consequences:
the product could not render a screen it did not know a name for; the only thing that walked every screen was a
golden test; and anything wanting a catalog view had to import a test module. Worse, every past defect lived in
the gap between "the builder is fine" and "the real button works" (`style="link"`, the callback registry,
`term:resize` with no handler, the 30 s terminal stall) — and no tool could see that gap from inside the bot.
Promoting the catalog makes adding a screen **one registry entry**, after which it is automatically covered by
the goldens, by the button walk and by the tool; and a `presentation` module that imports `tests/` would be an
architectural inversion, so the catalog is product code and a test asserts no product module imports the suite.

**Consequences.** `presentation.catalog` is the single source of truth for screens (grep proves no duplicated
screen list remains). The verdict walk is one implementation with two callers — the tool and the contract tests —
so they cannot drift. A `Section` is a first-class value (not a name prefix), the tool navigates it, and the
tests iterate it. The module stays dependency-pure: `presentation` may not import `adapters`, so the fixtures are
rebuilt from the domain entities rather than importing the fake engine's `_ws`. `/dev` is admin-gated **and**
behind an explicit `CLOUDBOT_DEV_TOOLS` switch, off by default (`middleware/admin_gate.py` checks the switch
before identity, so a disabled tool is invisible to everyone). `verify.sh` proof 31 (a bite test reintroducing a
dead button) shows the tool's own catalog check goes red.

**Alternatives rejected.** Keeping the catalog in the test and importing it from the tool: the product would
depend on `tests/`, and a production tool could not exist without the test suite. A second, tool-only screen list:
exactly the duplication that rots. Classifying verdicts in the handler: the tool and the tests would drift, which
is the maintainability failure the user named.

---

## CB-35 — One pager helper (`presentation/pagination.py`) for every list screen

**Decision.** Every screen that renders a list which grows with the data pages through
`cloudbot/presentation/pagination.py`: `PAGE_SIZE` (one project-wide default, 10 — the size the workspace
list, terminal list, file browser and run history already used, and what fits a phone viewport), `window()`
returns `(page_items, total_pages, has_more)` and clamps an out-of-range page, `footer()` renders the
`common.page` line, and `pager_rows()` renders previous/next with an end's arrow **omitted** (the R1 rule the
nav block follows), never muted. The six screens that had no pages (`port_list`, `process_list`,
`package_list`, `metrics`, the tunnel list, `archive_result`) now take `page: int = 0`, render only the
window, print the footer, add the pager row, and build the keyboard from the page. `ws_list`,
`terminal_list` and `file_browser` were converted to the same helper so there is exactly one pager.

**Why.** An audit of all screens found no shared pager: each screen hand-rolled its own, and six list screens
had none at all, so a list grew without bound. `port_list` was the longest screen in the product.

**Consequences.** `page` travels on the existing two-segment callbacks as an optional trailing key part
(`port:list:<ws>:<page>`), restart-proof and well inside the 64-byte cap. `port_list` shares **one** window
between its port and unix-socket tables, so the one pager covers both. The owner cell no longer prints the raw
pid (`run · pid 890648` → `run`): the pid is not an address any action accepts, and it was the longest cell on
the longest screen. `archive_result`'s pager remembers the last result per user+workspace in a bounded
in-process map; a stale pager button after a restart falls back to the archive menu. A test walks
`catalog.SCREEN_BUILDERS` and fails any screen that renders more than `PAGE_SIZE` data rows without a pager.

**Alternatives rejected.** A second pager for the screens that "would never grow" (metrics, archive result):
that is exactly how six screens ended up without one.

---

## CB-36 — No duplicate destinations; a scannable default row; unique terminal names; a real help screen

**Decision.** Four rules, each measured on the live catalog (screens-b):

1. **A keyboard never names one destination twice.** Two buttons on the same callback waste a slot and read
   as two actions. Where the two labels meant the same thing, the redundant one is removed or re-pointed:
   * `run_history` — the active filter button carried `run:history:<ws>` while "Load more" did too. The filter
     row is now filters only (the active one is **omitted**, R1), and paging uses the shared pager with the
     filter carried inline (`run:history:<ws>:<page>` / `run:filter:<ws>:<filter>:<page>`).
   * `live_logs_running` — **🧹 Clear output** carried the same `run:refresh` callback as **🔄 Refresh**. The
     engine has no log-truncation endpoint, so the button could never do anything different; it is removed
     rather than renamed.
   * `file_browser_empty` — the disabled current-directory breadcrumb duplicated the `🏠 /` crumb's
     destination. The current segment is omitted (R1) and each ancestor crumb navigates with `file:cd` to its
     own directory (the old code always emitted page 0, which was also wrong).
   * `terminal_list` — the two fixtures shared an id; see rule 3.
   * `ports` — already fixed before this task.

   A test walks `SCREEN_BUILDERS` in both locales and fails on any repeated callback in one keyboard.

2. **The default row is what a user scans.** `run_history` rendered eight columns of markup (the header row
   was double-wrapped, so "Run · Status · Command · When" counted as eight). It now renders **three**:
   identity, state, when. Command, duration and exit code remain one tap away on the run's own screen
   (`run:logs`, which the run's identity button opens). `admin_users` loses Language and Workspaces (6 → 3;
   both are on the user's own page); `admin_audit` loses the internal event id and the category (5 → 3; the
   category is the filter you are already viewing through).

3. **Terminal labels are unique among the list.** Session ids are ULIDs and share their first ~12 characters
   when created in the same millisecond, so `ses_…Z…` and `ses_…Y…` rendered identically. `unique_short_ids()`
   extends the prefix only until it is distinct (and numbers an exact duplicate), so two terminals never show
   the same label; the callback still carries the full id. Short when it can be.

4. **The help screen explains the product.** It now says what a workspace is (isolation, the tool screens),
   that a user may keep several workspaces and several terminals (and that a terminal ends on idle), and —
   the gap that mattered — how to change the terminal size in-session (`stty cols N rows M`), which was
   documented nowhere a user could see. It stays one short screen with the command list collapsed; the depth
   is a sentence each, not a manual.

**Why.** `docs/ui/01-language-and-menu.md` §3 previously showed a 480-character help screen with three
commands and nothing about the terminal size. The run table was the widest in the product (`port_list` had
already been reduced to five). The terminal-name collision is latent but real: two terminals opened together
is the normal case, not the corner.

**Consequences.** `run.history_column.when` and `admin.users.col.user` are new keys in both catalogs;
`run.clear_output`, `admin.users.col.locale` and `admin.users.col.workspaces` are removed. The goldens for
`run_history`, `terminal_list`, `admin_users`, `admin_audit`, `help`, `help_inside`, `file_browser*` and
`dev_sections` changed accordingly.

---

## CB-37 — The main board leads with workspaces, and one destination wears one label

**Decision.** The main menu's body and keyboard were rebuilt around the order a user's attention moves:

1. **The body is one compact line per workspace, not a table.** The old board rendered a four-column grid
   (`مساحة العمل · الحالة · التشغيلات · القرص`) — the same shape already reduced on the workspace home and
   the terminal list, because four columns do not fit a phone. A line now reads `**name** — state · one
   number` (the active-run count, when there is one). The disk figure is dropped from the board: it is a
   workspace *detail*, not a menu fact.
2. **No silent truncation.** The old body sliced `[:5]` and said nothing, so a user with six workspaces
   simply never learned about the sixth. The board still shows five lines (one phone screen), then says
   `… and N more — See all N in My workspaces.`, which is a real destination (`ws:list`). A test pins both
   the "and N more" copy and its absence at exactly five.
3. **The subscription line is a seam, not an invented flow.** `main_menu(plan=…)` renders `Plan: <label>`
   from the derived plan code; the wallet screens from `subs2` are not in the tree yet, so nothing passes it
   today and no payment path is invented. When those screens land, this one argument becomes live.
4. **The grid reads: what they came to do, then their own things, then the account.** `➕ New Workspace` is
   full width on its own first row; `📦 My workspaces` and the account destinations follow, two per row.
   The board never exceeds three rows.
5. **One destination, one label, everywhere.** `ws:list` was `📦 المساحات` on some screens and `📦 مساحاتي`
   on others; the error screens' `nav:menu` recovery button said `⬅️ Back` while the nav block said
   `🏠 Menu`. Both are fixed: `ws:list` is always **📦 My workspaces / 📦 مساحاتي**, and `nav:menu` is
   always **☰ Menu / ☰ القائمة**. The emoji also stopped lying: `🏠` means the *workspace* home
   (`ws:home`), never the menu, and `▶️` is no longer shared by Start and Run — the welcome board's primary
   action is the same **➕ New Workspace** as the menu's.

   Two guards walk `SCREEN_BUILDERS` in both locales: no two buttons on the main board share a leading
   emoji, and every shared destination callback carries exactly one label across the whole product.

**Why.** He asked for better organisation and naming of the buttons themselves («ابدع بجد فيها»). The old
board mixed account destinations with the workspace list, put `➕ مساحة جديدة` wherever the catalog happened
to order it, and leaked internal names (`📦 المساحات`, `🎛 القائمة`) into the UI.

**Consequences.** `menu.column.*` are removed; `menu.ws.running`, `menu.and_more`, `menu.workspaces_more`
and `menu.plan_line` are new in both catalogs. `welcome.start` now reuses the New Workspace wording.
`ws.open` loses its `📦` prefix (the button lives inside a list, next to the `📦 My workspaces` destination,
so a second package icon was noise). Goldens for `menu*`, the nav-block keyboards (`help*`, `settings*`,
`language_picker*`, `ws_list*`, `ws_detail*`) and the four `err_*` screens changed.
