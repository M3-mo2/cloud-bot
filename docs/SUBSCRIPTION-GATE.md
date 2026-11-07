# The subscription gate — where it is, where it is missing, and what we did

**Status:** implemented (service layer) + engine duration wired. Supersedes the
assumption in the brief that *"the gate that enforces it is middleware"*.

**Read with:** [`DECISIONS.md`](DECISIONS.md) CB-39 (a plan belongs to a
workspace), [`ADMIN-PANEL.md`](ADMIN-PANEL.md) (the moderation rule this
mirrors), and `docs-cloud-service/INTEGRATION-GUIDE.md` §8.3–8.4 (the term
grammar and what a lapsed term does). The guide wins over this document.

---

## 1. The premise did not hold — measured, not assumed

The brief says the enforcer of `domain.subscription` is **middleware**, and that
a path bypassing middleware runs free. That premise is false in this tree.

* `grep -rn "derive_status\|subscriptions" cloudbot/middleware/` → the only hit
  is `forcejoin.py`, whose docstring calls *itself* "the mandatory-subscription
  gate". That gate is **channel membership** (`fj:`), not a paid plan. It reads
  `ForceJoinService.status`, never `domain.subscription`.
* There is **no** plan-subscription middleware. There never was.
* The only plan checks that exist are:
  1. **presentation**: `WorkspacesService._with_subscription_state` rewrites an
     `active` workspace to `LOCKED` for the list/detail screens, and
     `workspace_detail_markup` / `_tunnels_screen` mute buttons for `LOCKED`;
  2. **accounting**: `WorkspacesService.plan_maxima` consults the plan to cap a
     requested limit;
  3. **billing screens**: `BillingService.status` is rendered by the wallet/menu.
* None of those is an enforcement seam. A direct service call, a scheduled job,
  a script, or any new entry point reaches the engine with **no check at all**.
  The engine cannot refuse either: `WorkspacesService.create` never sends
  `subscription`, so `EngineAdapter.create_workspace` omits it and the engine
  creates an **unlimited** workspace. Its `403 subscription_expired` and its
  `max_duration` are unreachable.

So the answer to *"which paths pass the middleware today?"* is: **none, because
the middleware does not exist.** The hole is wider than the brief describes.

This is the contradiction the brief told me to stop and report: I am reporting
it, and I choose the design that fixes the real hole rather than wiring a
middleware that would only cover Telegram updates.

---

## 2. Every path that reaches workspace-scoped work

Enumerated from the routers registered in `cloudbot/bootstrap/app.py` and the
handlers they include (not guessed). "Work" means starting compute or mutating
stored state; "read" means an idempotent read the engine also allows while
locked (INTEGRATION-GUIDE §8.4).

### 2.1 Handlers and their workspace-scoped entry points

| Router | Entry point (callback/command) | Kind | Gate today | Gate after this task |
| --- | --- | --- | --- | --- |
| `workspaces` | `ws:list`, `ws:view`, `ws:home`, `ws:more`, `ws:less` | read / enter | none (render only) | read — allowed |
| `workspaces` | `ws:new`, `ws:plan`, `ws:continue` | create (needs a plan) | plan is mandatory at create | unchanged (create attaches a plan) |
| `workspaces` | `ws:settings`, `ws:stop`, `ws:suspend`, `ws:resume`, `ws:delete` | lifecycle/control | none | control — unchanged (not compute) |
| `workspaces` | `ws:renew` | billing navigation | none | unchanged |
| `files` | `file:ls`, `file:view`, `file:more` | read | none | **read — allowed** (engine allows file reads while locked) |
| `files` | `file:mkdir`, `file:rename`, `file:delete`, `file:upload` | mutate | none | **gated** (`assert_usable`) |
| `execution` | `run:choose`, `run:history`, `run:filter`, `run:logs`, `run:refresh` | read | none | **read — allowed** |
| `execution` | `exec:prompt`→text, `run:prompt`→text, `run:file` | start compute | none | **gated** |
| `execution` | `run:stop`, `run:delete`, `run:purge` | cleanup | none | allowed (stops/purges, never starts work) |
| `features` (packages) | `pkg:list` | read | none | read — allowed |
| `features` (packages) | `pkg:install`→text, `pkg:rm_prompt`→text, `pkg:rm_all` | mutate | none | **gated** |
| `features` (tunnels) | `tnl:list`, `tnl:remove` | read / cleanup | none | read/cleanup — allowed |
| `features` (tunnels) | `tnl:publish`, `tnl:port`, `tnl:port_custom`→text | start egress | none | **gated** |
| `features` (ports/metrics/processes) | `port:list`, `met:view`, `proc:list` | read | none | read — allowed |
| `features` (processes) | `proc:stop` | kill process | none | **gated** |
| `features` (archives) | `arc:menu`, `arc:result` | read | none | read — allowed |
| `features` (archives) | `arc:upload`→document, `arc:extract_prompt`→text, `arc:apply` | write files | none | **gated** |
| `features` (archives) | `arc:export_prompt`→text | export (read into a ZIP) | none | read — allowed |
| `terminal` | `term:list`, `term:view`, `term:refresh`, `term:forget` | read / local | none | read — allowed |
| `terminal` | `term:open` | start a shell (compute) | none | **gated** |
| `terminal` | `term:cmd`→text, `term:signal`, `term:ctrl_d` | drive compute | none | **gated** |
| `terminal` | `term:exit`, `term:close` | cleanup | none | allowed |
| `billing` | `pay:wallet`, `pay:plans`, `pay:packs`, `pay:history`, `pay:buy`, `pay:pack`, pre-checkout, successful payment | money | n/a (not workspace work) | unchanged; `pay:buy` now also extends the engine term |
| `common` | `/start`, `/help`, `/settings`, `nav:*` | reads | none | read — allowed |
| `admin_panel` | every `adm:*`, `/admin`, `/notifications` | admin read/act | identity gate only | unchanged (panel acts with `assert_admin_override`) |
| `admin` | `/health`, `/diag` | admin diagnostics | identity/mode gate | unchanged |
| `dev` | `/dev`, `dev:*` | admin dev tools | identity + switch | unchanged |
| `forcejoin` | `fj:*` | channel gate | its own gate | unchanged |

### 2.2 Non-Telegram callers

| Caller | Reaches | Gate today | Gate after this task |
| --- | --- | --- | --- |
| `BackupScheduler` (`bootstrap/backup_scheduler.py`) | `BackupService` only — no workspace-scoped engine work | n/a | n/a |
| `AdminService` (panel) | engine directly with `assert_admin_override` | admin identity gate | unchanged (admin exemption) |
| a future script / job / entry point | the services directly | **none** | **gated by the service** |

### 2.3 What leaks if the (imaginary) middleware is bypassed

Every work row in §2.1 leaks today. Concretely: `exec`, `run`, `term:open`,
`term:cmd`, `pkg:install`, `tnl:publish`, `proc:stop`, file writes, archive
extract — all reach the engine for a workspace whose plan is `EXPIRED` or
`NONE`, and the engine accepts because it was told nothing about a term.

---

## 3. The two candidate designs

### (a) Enforce in the service layer — chosen

Every work service method calls `SubscriptionService.assert_usable(tg_id, ws_id)`
after the ownership gate. It reads the workspace's own subscription and raises
`SubscriptionExpired` when `derive_status(...).is_active` is false. The refusal
maps to the same `err.subscription_expired` screen the engine's `403` maps to.

* **Why it wins:** it mirrors the moderation rule the owner already stated
  ("blocking lives in the service layer — every path must fail"), it is the
  **only** seam every caller (handler, job, script, future entry point) passes
  through, and it is testable without Telegram. Ownership runs first, so a
  foreign id still cannot probe.
* **Cost:** one subscription read per work call (a single indexed SQLite row on
  a shared connection). It is cacheable later if it ever shows up in a profile;
  correctness comes first.
* **Admin exemption:** the actor's `tg_id` in the operator allowlist skips the
  gate, exactly like the ban and force-join gates. An admin entering another
  user's workspace is subject to that user's plan for **work** (the workspace is
  genuinely locked); the panel's own actions are unaffected because they never
  go through the user services.

### (b) Push expiry into the engine — defence in depth, not the primary

Send `subscription` at create and call `POST /subscription/extend` on every
forward extension, so the engine locks the workspace itself.

* **Why it is not the primary:** the engine's clock is not the bot's; a revoke or
  a shorten cannot be pushed (the engine refuses `422 subscription_would_shorten`
  and a non-future `expires_at`), so the two truths can disagree; and it does not
  help at all if the engine is unreachable or a future caller talks to the
  service layer without having synced.
* **Why it is still worth doing:** it is the only way `403 subscription_expired`
  and `max_duration` can ever fire, and it stops a locked workspace's compute at
  the source. Wired best-effort (a failed sync is logged, never fatal: the local
  store remains the source of truth and the service gate still refuses).

---

## 4. Recommendation

**Implement (a) fully; wire (b) as best-effort defence in depth.** The service
gate is the revenue fix; the engine sync is the second lock on the same door and
is cheap now that the guide (§8.3) defines `subscription/extend`.

This is not half-wiring: the service gate is complete on the work paths, and the
engine sync is complete on every **forward** extension (create, purchase, admin
grant). We cannot push a shorten/revoke backwards; that is a property of the
engine contract, and it is documented here rather than hidden.

---

## 5. What was implemented

* `cloudbot/services/subscription.py` — `SubscriptionService`: `status`,
  `assert_usable` (raises `SubscriptionExpired`), `sync_engine` (best-effort).
* `cloudbot/core/errors.py` — `SubscriptionExpired` (`code="subscription_expired"`).
* The gate is injected into `execution`, `files`, `sessions`, `packages`,
  `tunnels`, `archives`, `processes`; each **work** method calls it after the
  ownership gate (and after the moderation gate, so a ban still outranks a plan).
  Reads are deliberately not gated: the engine allows them while locked, and the
  admin panel and a user's own file safety depend on them.
* `EnginePort.extend_subscription` + `EngineAdapter` + `FakeEngine`, wired from
  `WorkspacesService.create`, `BillingService.buy_plan` and `AdminService.grant`
  (forward extensions only).
* Tests: `tests/unit/test_subscription_gate.py` (bite without middleware, valid
  pass, admin exempt, engine-sync contract) and
  `tests/contract/test_engine_adapter.py` (request shape for
  `subscription/extend`). See §6 for the tally.

## 6. Documented limitation (the honest follow-up)

The engine is **only** advanced, never shortened. A `revoke` or an admin
`shorten` sets the bot's expiry backwards; the engine's term stays where it was,
so the engine will not lock that workspace at the new time. The **service gate
still refuses work immediately** because it reads the bot's store, which is the
source of truth. Making the engine match a shorten would require a new engine
capability; it is out of scope here and is recorded as a follow-up.
