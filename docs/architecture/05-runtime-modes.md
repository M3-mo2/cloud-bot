# 05 — Runtime Modes

There are exactly three modes: **PRODUCTION**, **DEV**, **DEBUG**. The mode is a
single `Settings.mode` value and a single `core/modes.py` table. **No component
branches on the raw enum** — components ask the table (`modes.verbose_errors(mode)`,
`modes.mock_engine(mode)`), so behavior differences live in one reviewed place.

The mode changes **edge behavior only**: logging, error verbosity, admin access,
mocking, throttling, and which screens are reachable. It never changes domain
logic, never changes the rich-only contract, and never changes the no-raw-output
rule for normal users.

---

## 1. The comparison table

| Concern | PRODUCTION | DEV | DEBUG |
| --- | --- | --- | --- |
| **Who runs it** | End users | Developers | Operators, on incident |
| **Access** | Everyone | Allow-list (optional) or everyone | **Admins only** |
| **Log level** | `INFO` (JSON) | `DEBUG` (pretty) | `TRACE` (pretty + payloads) |
| **Engine adapter** | Real `EngineAdapter` | Real, or **`FakeEngine`** when `mock_engine` on | Real |
| **Error copy** | Human sentence (AR/EN) | Human + field details | Human + **collapsed raw** payload |
| **Raw payloads to user** | Never | Never (admins may in DEV) | **Yes, admin-only, collapsed** |
| **Throttling** | On | Relaxed | Off |
| **Admin commands** | Blocked | Available to admins | Available; `/mode`, `/diag`, `/raw` |
| **WIP screens** | Hidden | Shown | Shown |
| **Telemetry/log redaction** | On | On | On, but raw allowed to admins |
| **FSM storage** | SQLite | Memory | Memory |
| **Polling vs webhook** | Webhook (later) or polling | Polling | Polling |
| **Auto-reload** | No | Optional | No |

---

## 2. PRODUCTION

**Purpose:** serve real users safely and predictably.

- Structured JSON logs at `INFO`; every update logged with correlation id,
  user id, duration, outcome. **Secrets and full payloads are redacted.**
- Only the human, localized error sentence is shown. The engine's message and any
  JSON are logged server-side only.
- Throttling enforced per user; long operations bounded and cancellable.
- Admin commands and DEV/DEBUG screens are unreachable (a
  `/diag` from a normal user gets the generic help screen, not an error that
  reveals the command exists).
- Limits, profiles and interpreters are derived from cached `/v1/meta`, refreshed
  on stale-value refusals.
- Files larger than the plan cap are refused before upload.

## 3. DEV

**Purpose:** fast iteration without touching real infrastructure when desired.

- Pretty logs at `DEBUG`; field-level detail of refusals logged.
- **`mock_engine` toggle**: when on, `FakeEngine` (in `adapters/cloud_service/`)
  serves deterministic fixtures — a couple of workspaces, a sample tree, a run
  that transitions `queued → running → exited` with fake logs. This lets every
  screen be exercised with no engine.
- **WIP screens** (e.g. Phase 1.5 terminal, Phase 2 tunnels) are reachable behind
  a DEV gate so they can be demonstrated before they ship. They carry a visible
  "⚠️ DEV" banner and can never appear in PRODUCTION.
- Relaxed throttling; memory FSM storage; optional auto-reload.
- Admins *may* see a "details" line on errors, but **never raw JSON to non-admins**.

## 4. DEBUG

**Purpose:** incident response and maintenance. This is the *only* mode in which
raw data can be shown, and then only to admins, and only collapsed.

- `TRACE` logs with correlation ids, engine request/response timings, retries.
- **Admin allow-list enforced** (`settings.admin_ids`): every admin command and
  every raw view checks `is_admin`. A non-admin sees ordinary PRODUCTION
  behavior even when the bot runs in DEBUG.
- **Raw payloads** appear only as a collapsed `<details>` block appended to the
  human screen, e.g.:

  ```
  <details><summary>🧪 Raw engine response (admin)</summary>
    <pre><code class="language-json">{ ...exact JSON... }</code></pre>
  </details>
  ```

  This is the *single* documented exception to "no raw output", and it is:
  (a) DEBUG-only, (b) admin-only, (c) collapsed by default. It is never the
  primary content, and it never replaces the human sentence.
- Throttling off; `/diag` (engine `/healthz`+`/readyz`+meta), `/mode` (read/set
  at runtime for the process), `/raw on|off` (per-admin toggle), `/replay` (replay
  a recorded update id through the pipeline).
- The engine's real English `message` is surfaced **inside the collapsed block
  only**, so operators can read "sandbox unavailable: nsjail could not set up…"
  while users never do.

---

## 5. The guard rails (why modes can't leak)

1. **One table.** `modes.py` is data; all branching reads it. Reviewing the table
   reviews every behavioral difference.
2. **Admin gate is enforced in middleware**, not in individual handlers, so a new
   admin command cannot forget it (`mode_gate.py` runs before admin routers).
3. **Raw rendering is a capability, not a habit.** Only
   `components.RawPayloadDetails` can emit raw content, and it asserts
   `mode == DEBUG and ctx.is_admin` at construction. It is impossible to call it
   accidentally from a normal screen.
4. **Golden tests per mode.** The screen snapshot tests run for PRODUCTION, DEV
   and DEBUG × AR/EN; a PRODUCTION snapshot that contains a `<details>Raw` block
   fails CI.
5. **The seven-day rule.** DEBUG is meant for incidents. The bot logs a startup
   banner naming the mode; running DEBUG against real users is an operator
   mistake the banner and docs discourage loudly.

---

## 6. Setting the mode

```bash
CLOUDBOT_MODE=production python -m cloudbot
CLOUDBOT_MODE=dev        python -m cloudbot
CLOUDBOT_MODE=debug      CLOUDBOT_ADMIN_IDS=111,222 python -m cloudbot
```

`mode` is validated at startup. Unknown values are a fatal configuration error.
`DEBUG` with an empty `admin_ids` is refused with a clear message (it would be a
footgun).
