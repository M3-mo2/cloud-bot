# AUDIT — the port-publishing flow (task 18)

Discovery-only audit of the tunnels / ports flow. **No behaviour was changed.**
Every finding below was reproduced by driving the real dispatcher with the fake
engine (`mock_engine=True`), or by calling the real engine at `http://127.0.0.1:8098`
on the operator's workspace `ws_01M6KXF997CQEC00WY75NDCFF4` (read-mostly; every
tunnel created was deleted in the same script). Raw outputs are pasted as they
came back; tunnel URLs are redacted (`https://<redacted>`).

The one root cause behind the two most severe findings is that
`ErrorGuardMiddleware` is wired at the **Update** level, so `_render` never sees a
`CallbackQuery`/`Message` and renders *nothing*; every refusal the tunnel/port
handlers re-raise therefore dies silently. See F-01.

---

## Fixed

The three high-severity findings below were fixed in commit
`02b7e32` (`fix(tunnels): render re-raised refusals, stop orphaned progress,
guard double-publish`). The original finding text is kept verbatim further down
— an audit is a record, not a rewrite.

* **F-01 (critical)** — fixed. `ErrorGuardMiddleware._render` now unwraps a
  `TelegramObject` `Update` to its `callback_query`/`message` before the
  `isinstance` checks, so a re-raised `DomainError` on any event level renders
  exactly one screen. `NotOwned` and a genuinely missing workspace id render the
  **byte-identical** "not found" board (the isolation rule holds); an unmapped
  code renders the generic board, never silence. The guard stays registered once,
  at the update level (see `docs/DECISIONS.md`, CB-29).
* **F-02 (high)** — fixed. The publish progress indication now becomes the
  message that carries the result: for a callback it edits the originating
  message; for a typed port it sends the progress message and edits *that* one.
  No orphaned "Publishing…" is left on success, refusal or timeout.
* **F-03 (high)** — fixed. `TunnelsService.publish` short-circuits when a tunnel
  already exists for the port (surfacing it with a sentence) and serialises
  concurrent creates per `(user, workspace, port)` with an `asyncio.Lock`, so a
  double-tap makes one engine create. A failed create releases the lock (see
  `docs/DECISIONS.md`, CB-30).

Proofs: `tests/unit/test_port_publishing_fixes.py` (15 tests, driven through the
real dispatcher + fake engine), `scripts/verify.sh` proof 37 (a bite test that
reintroduces the F-01 silence), and goldens for the publishing /
already-published screens in both locales.

---

## F-01 · Re-raised refusals render nothing: the error guard receives an `Update`, not the callback/message

[severity: critical]

**Where** `cloudbot/middleware/error_guard.py:52` (`ErrorGuardMiddleware._render`) and
`cloudbot/bootstrap/app.py:61` (registration as `dispatcher.update.middleware`).
Reached from `cloudbot/handlers/features.py:288` (`_publish` re-raises an unmapped
`EngineRefused`), `features.py:256` (`parse_port` raises `InvalidInput`), and every
workspace-scoped service through `OwnershipService.assert_owned` (`NotOwned`).

**What** When a handler raises a `DomainError` on a callback (or on a typed
message), the guard catches it but its `_render` only handles `CallbackQuery` /
`Message`. It is fed an `Update` (the level it is registered at), falls through
both `isinstance` branches, and **edits/sends nothing**. The user is left staring
at the previous screen — for a publish, the "⏳ Publishing…" screen forever.

**Proof** Reproducer drives the real dispatcher; the guard's own `_render` is
traced to show the object it receives:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/prove_guard.py
A) WITHOUT the fix:
   guard._render got: Update | expects CallbackQuery or Message -> renders: False
   screens: 1 ['<h2>➕ Publish a port</h2><p>⏳ Publishing… cloudflared is finding a URL.</p>']
B) WITH a one-line unwrap in the guard (repro only, not committed):
   screens: 2
      SEND <h2>➕ Publish a port</h2><p>⏳ Publishing… cloudflared is finding a URL.</p>
      EDIT <h2>⚠️ Something went wrong</h2><p>That request couldn't be completed.</p>
```

Same silent death for the typed-port path and for a non-owner:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/final_repro.py
F-01b out-of-range typed port (message) -> InvalidInput renders NOTHING
new screens after typing '80': 0
last screen: <h2>➕ Publish a port</h2><p>Send the port number to publish (1024–65535).</p>…

F-01c non-owner tapping any callback -> NotOwned renders NOTHING
screens rendered for a non-owner: 0
```

An unmapped engine code (the exact R1 "never swallowed" rule) also renders nothing
— only the publishing screen remains:

```
F-01a uncaught engine refusal on a publish CALLBACK (tnl:port)
screens rendered: 1
    SEND <h2>➕ Publish a port</h2><p>⏳ Publishing… cloudflared is finding a URL.</p>
```

**Why it matters** This defeats the property the whole flow is built around
("every refusal renders a sentence; an unmapped code re-raises to the error
screen"). A user who types `80` (the copy literally suggests the range), hits a
future engine code, or is simply not the owner sees **no feedback at all** and is
stranded on a stale screen. It also means the rejection of a foreign tap is
invisible to the attacker (arguably desirable) but *also* invisible to a legitimate
user whose workspace id has been stale for a moment.

**Suspected cause** `dispatcher.update.middleware(ErrorGuardMiddleware(...))`
(`bootstrap/app.py:61`). aiogram 3.x middleware registered at the update level
receives a `TelegramObject` (the `Update`); `_render` was written for the
per-event object. The `_wants ... isinstance(event, CallbackQuery)` branches never
match. (Confirmed: `run_guard4.py` printed `_render event type: Update message: None`.)

**Suggested fix** In `_render`, unwrap a `Update` to `event.callback_query` /
`event.message` before the `isinstance` checks (or register the guard on the
`message`/`callback_query` observers, as `AdminGateMiddleware` already is). One or
two lines; not implemented here.

---

## F-02 · A callback publish leaves an orphaned "⏳ Publishing…" message in the chat

[severity: high]

**Where** `cloudbot/handlers/features.py:279-294` (`_publish` → `publishing_screen`
with `send_new=True`, then `show_screen` edits the *original* callback message).
`cloudbot/presentation/screens/tunnels.py:265-269`.

**What** Tapping a publish button on the publish-picker sends a **new** message
"⏳ Publishing…" (because `publishing_screen` is `send_new=True`), and the final
list is then edited into the **originating** message. The "Publishing…" message is
never updated, edited or deleted — it stays in the chat above the result forever.
Every successful callback publish adds one orphan; a refusal adds another.

**Proof** Message ids traced across one callback publish:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/final_repro.py
F-02 orphaned 'Publishing...' message on a callback publish
   EDIT msg_id=1: <h2>➕ Publish a port</h2>…Choose a port discovered in this workspace…
   SEND msg_id=101: <h2>➕ Publish a port</h2><p>⏳ Publishing… cloudflared is finding a URL.</p>
   EDIT msg_id=2: <h2>🌐 Public links</h2>…<code>8080</code>…active…
-> the 'Publishing…' message (msg_id 101) is never updated again
```

**Why it matters** The chat accumulates stale "Publishing…" screens; a user may
tap the *old* publish screen's buttons again thinking it is live. It reads as a
hang ("it's been publishing for ten minutes").

**Suspected cause** `publishing_screen` returns `Screen(..., send_new=True)`, so
`show_screen` sends rather than edits; the final render targets
`event.message.message_id`, not the just-sent status message.

**Suggested fix** Prefer a one-shot draft / edit-the-originating-message for the
in-flight state, or delete/track the status message id and edit it to the result.
Not implemented.

---

## F-03 · A double-tap of the same port creates two tunnels on that port

[severity: high]

**Where** `cloudbot/handlers/features.py:271-294` (`on_port_pick` / `_publish`);
`cloudbot/services/tunnels.py:48-51` (`publish` has no idempotency and no in-flight
guard).

**What** Two taps on the same publish button (or a retried callback) run two
`create_tunnel` calls; the engine happily creates a second tunnel for the same
port. There is no busy flag, no de-dup, and the callback is not disabled once
tapped.

**Proof** Fake engine, two `tnl:port` callbacks:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/final_repro.py
F-03 double tap of the same port -> two tunnels on one port
engine tunnels: [('0001', 8080, 'active'), ('0002', 8080, 'active')]
```

Confirmed against the **real engine** (same port twice; both deleted after):

```
$ /home/zerops/cloud-bot-venv/bin/python /tmp/opencode/real_dup.py
before: []
create#1 SAME PORT 18090 -> id=tun_…RF port=18090 status=active url=https://<redacted>
create#2 SAME PORT 18090 -> id=tun_…F1 port=18090 status=active url=https://<redacted>
tunnels now: [('tun_…F1', 18090, 'active'), ('tun_…RF', 18090, 'active')]
after cleanup: []
real creations this script: 2
```

**Why it matters** The cap is per workspace (2) and the create is slow
(`url_timeout_sec`, 45 s). A user who taps twice burns the entire allowance on one
port, and the screen shows two identical rows. The second URL is unreachable the
moment the first listener stops.

**Suspected cause** No client-side idempotency/ownership of "port already has a
tunnel" and no per-user in-flight lock before the (slow) create.

**Suggested fix** Short-circuit a create when a tunnel already exists for that
port, and/or disable the port's button while a create is in flight. Not
implemented.

---

## F-04 · The create gate ignores a SUSPENDED / LOCKED workspace; Publish is offered and then fails

[severity: medium]

**Where** `cloudbot/presentation/screens/tunnels.py:100-102` (`_gate_note`) and
`tunnels_markup` (`publishable = server_enabled and allow_tunnels and allow_net`,
line 142). The workspace `status` is never consulted.

**What** Three gates are checked (server, `allow_tunnels`, `allow_net`), but a
suspended or locked workspace still shows an enabled **➕ Publish a port** button.
Tapping it hits the engine and is refused with `409 workspace_suspended` /
`403 subscription_expired`.

**Proof** The status here comes straight from the render call's arguments; a
suspended workspace renders an enabled publish entry:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/final_repro.py
F-04 SUSPENDED workspace still shows an ENABLED Publish button
publish button: [('➕ Publish a port', 'enabled')]
```

For comparison, `workspace_detail_markup` (`screens/workspaces.py:227-252`) *does*
disable Run/Terminals for suspended/locked — the tunnels screen does not follow
that pattern.

**Why it matters** A paused workspace offers an action that cannot succeed. With
F-01, that refusal renders nothing (the user sees the stale screen again).

**Suspected cause** The gate was built around the three documented tunnel switches
and never extended to the lifecycle status.

**Suggested fix** Pass the workspace status into `tunnels_screen` and include
`suspended`/`locked` in `publishable`/`_gate_note`, with the matching sentence.
Not implemented.

---

## F-05 · `tunnels_disabled` re-renders the tunnels screen using stale local state and drops the engine's own reason

[severity: medium]

**Where** `cloudbot/handlers/features.py:204-218` (`_tunnel_refusal_screen`,
`kind == "gate"` → `_tunnels_screen`) and `features.py:188-201` (`_tunnels_screen`
reads `ws.limits` from the bot's own repository). Contrast with
`cloudbot/presentation/mapping/errors.py:119-134` (`_tunnels_disabled_message_key`),
which *does* know how to name the switch from `details.config`/`field`.

**What** On a `422 tunnels_disabled`, the handler ignores the engine's
`details.config`/`field` and just redraws the tunnels list from the bot's cached
workspace limits. If that cached `allow_tunnels`/`allow_net` are both true (e.g.
the server switch is off, or the bot's copy is stale), the redraw contains **no
gate sentence** — the refusal is a silent redraw, exactly the class of bug task 15
fixed elsewhere.

**Proof** Bot believes `allow_net=True, allow_tunnels=True`; the engine refuses
with `field=allow_net`:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/final_repro.py
F-05 tunnels_disabled with an engine detail the bot's ws state does not reflect
rendered: <h2>🌐 Public links</h2><p>No public links in this workspace.</p><p><i>Publish a port…
-> no gate sentence at all (the engine's own detail was discarded)
```

**Why it matters** The engine says *why* (server off vs. no egress vs. workspace
opt-out) and the bot throws that away. The user sees the same list they were on,
with no explanation.

**Suspected cause** The dedicated `_tunnels_disabled_message_key` mapping is only
used by the generic `error_screen` path (which, per F-01, never renders on a
callback). The tunnel-specific path bypasses it.

**Suggested fix** In `_tunnel_refusal_screen` for the gate kind, render the
specific `tunnels_disabled` sentence from `exc.details` (via
`_tunnels_disabled_message_key`) instead of/in addition to redrawing the list.
Not implemented.

---

## F-06 · A listening port with no discoverable owner is silently dropped from the publish picker

[severity: medium]

**Where** `cloudbot/handlers/features.py:166-174` (`_detected_ports`, filter
`if p.attributed`).

**What** `/ports` reports every listening socket, including ones with no owner
(`attributed:false`). The picker filters those out ("attributed listening ports"),
so a port that is genuinely open is not offered; the user must know its number and
type it. The port list screen itself *does* show unattributed entries ("owner
unknown"), so the two screens disagree.

**Proof** A `LISTEN` socket with `attributed=False` is filtered away:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/f67.py
detected offered to the user: [8080]
-> port 9090 is LISTEN with no discoverable owner; it is silently not offered
```

Live confirmation of the mechanism on the real engine (a tunneled port whose
listener is gone still exists server-side, and the pid attribution can vanish):
the tunnel create on a *non-listening* port also succeeded there
(`create2 port=19999 status= active`), so non-attributed/non-listening ports are
accepted by the engine but hidden by the picker.

**Why it matters** "Why is my server not offered?" — the owner heuristic is an
implementation detail leaking into the UI. The design doc for 2c says an
unattributed socket must "never be hidden or guessed"; the picker hides it.

**Suspected cause** `attributed` conflates "we know the process" with "it is a
real listening port".

**Suggested fix** Offer all `state == LISTEN` (or all reported) TCP ports; keep
the unattributed marker only for display. Not implemented.

---

## F-07 · Tunnel status is raw engine English in both locales, and a dead tunnel still gets a live-looking LINK button

[severity: low]

**Where** `cloudbot/presentation/screens/tunnels.py:192-197` (`_status_label`
returns the engine string verbatim) and `tunnels_markup` line 130-140 (a LINK
button for any tunnel with a non-empty url).

**What** The engine statuses are `active`/`stopped` ("Each tunnel's `status` is
refreshed from the live cloudflared process"). The screen prints them raw — so the
Arabic UI shows English `active`/`stopped` — and a `stopped` tunnel keeps a
prominent **🔗 Open {port}** LINK button to a URL that no longer serves. There is
no "broken/stopped" signal beyond the raw word.

**Proof** Arabic render keeps English status and a LINK button for `stopped`:

```
$ /home/zerops/cloud-bot-venv/bin/python -u /tmp/opencode/f67.py
AR html: <h2>🌐 الروابط العامة</h2><table …><tr><td><code>٨٠٨٠</code></td><td>stopped</td></tr>…
LINK buttons for a 'stopped' tunnel: [('🔗 فتح ٨٠٨٠', 'https://x.trycloudflare.com')]
```

**Why it matters** Bilingual-by-construction is a hard rule; a raw English token
in the Arabic UI breaks it. Offering a live-looking link to a stopped tunnel
invites the user to share a dead URL.

**Suspected cause** `_status_label` has no status→catalog mapping; the markup
renders the link for every non-empty url regardless of status.

**Suggested fix** Map `active`/`stopped` to catalog strings (both locales) and
mute/hide the LINK button when the status is not `active`. Not implemented.

---

## F-08 · Dead/confusing code in the tunnels flow

[severity: low]

**Where** `cloudbot/presentation/screens/tunnels.py:36-39` (`_short`, never
called), `tunnels.py:192` (`_status_label` returns the input unchanged), and
`cloudbot/adapters/cloud_service/engine.py:76` (`PORTS_TIMEOUT_S` defined but
never used — `list_ports` uses the default client timeout).

**What** `_short` is dead. `_status_label`'s docstring promises "a safe generic
word" but it returns the raw status. `PORTS_TIMEOUT_S = 30.0` is unreferenced;
`list_ports` relies on the general `engine_timeout_s` (default 30 s) — the same
value by luck, but not the named constant, so bumping one would not affect the
other.

**Proof** Static:

```
$ grep -rn "_short(" cloudbot/presentation/screens/tunnels.py
36:def _short(tunnel_id: str) -> str:
$ grep -rn "PORTS_TIMEOUT_S" cloudbot --include=*.py
cloudbot/adapters/cloud_service/engine.py:76:PORTS_TIMEOUT_S = 30.0
```

**Why it matters** Minor: misleading code (the `_status_label` promise) and a
constant that does not do what its name implies.

**Suspected cause** Incremental refactors left orphans.

**Suggested fix** Delete `_short`; either map statuses (F-07) or drop the
docstring claim; wire `PORTS_TIMEOUT_S` into `list_ports` or delete it. Not
implemented.

---

## Positive results (checked, no defect found)

These were examined and behaved correctly; recorded so the absence of a finding is
deliberate, not an oversight:

- **Input validation (F).** `parse_port` never raises a raw `ValueError`. `0`,
  `80`, `70000`, `-1`, `65536`, `1023`, a 500-digit string and Arabic-Indic
  `١٢٣٤٥٦` all raise `InvalidInput(out_of_range)`; `abc`, `0x1F90`, `8080.0` raise
  `InvalidInput(invalid)`; empty/whitespace raises `InvalidInput(empty)`; and
  `'8080 '`, `'8080\n'`, `'  8080  '`, `'١٠٢٤'`, `'８０８０'`, `'1_024'`, `'+8080'`
  all normalise to `1024`/`8080`. The defect is that the resulting sentence never
  reaches the user (F-01).
- **Ownership / isolation (G).** `list`, `publish` and `remove` each go through
  `OwnershipService.assert_owned` before any engine call; a foreign workspace is
  treated as absent. No cross-user data was reachable in the fake-engine probes.
- **Ports screen shape (H).** Empty, many, partial (`complete:false` + `notes`),
  unattributed and unix-only reports all render; numbers are localised
  (Arabic-Indic in AR) and the reachability note is surfaced verbatim as a
  blockquote; both locales are well-formed. Ports come back in a stable order
  (engine order) so refreshes do not reorder the table.
- **URL-as-text rule (C, partial).** No code path renders a tunnel URL as text or
  a code block; it appears only as a `LINK` button (`grep` over
  `presentation/` confirms `tunnel.url` is used solely at
  `screens/tunnels.py:136`). The URL is not persisted (no store).

## Could not answer

- Whether a **suspended** workspace's publish actually returns
  `409 workspace_suspended` from the real engine — that would require suspending
  the operator's live workspace, which the task forbids. F-04 is proven at the
  screen level and from the documented error table.
- Whether the engine ever returns a `tunnels_disabled` whose `details` conflict
  with the bot's local state in the *live* deployment (F-05) — the fake proves the
  rendering gap; the real trigger depends on operator config drift I did not
  manufacture.
- The exact global effect of F-01 on every non-tunnel feature (files, packages,
  processes, archives, metrics, terminals). The guard runs at update level for all
  of them, so the same silent-death likely applies broadly; I only proved it for
  the publish/typed-port/foreign-owner paths.
- Whether `PORTS_TIMEOUT_S` was intended to differ from `engine_timeout_s` (F-08).

## Workspace-left-clean evidence

Real engine, workspace `ws_01M6KXF997CQEC00WY75NDCFF4`, before and after the whole
run (tunnel URLs redacted):

```
$ /home/zerops/cloud-bot-venv/bin/python /tmp/opencode/probe_read.py
TUNNELS before: []

$ /home/zerops/cloud-bot-venv/bin/python /tmp/opencode/real_engine.py
[BEFORE] tunnels: []
… (2 tunnels created and used, then deleted) …
delete unknown -> not_found
deleted tun_…RF
deleted tun_…F1
double-delete -> not_found
[AFTER-CLEANUP] tunnels: []

$ /home/zerops/cloud-bot-venv/bin/python /tmp/opencode/real_dup.py
after cleanup: []
…

$ /home/zerops/cloud-bot-venv/bin/python /tmp/opencode/final_read.py
FINAL tunnels: []
FINAL ports: []
FINAL runs (any running?): [('run_…MQ', 'killed'), ('run_…TC', 'failed')]
```

The one listener used for the port-discovery poses was a `create_run` I stopped;
it shows as `killed`. No tunnel remains.

## Real engine calls

- **4 tunnel creations** (the whole run's budgeted maximum was 6; all deleted).
- ~31 HTTP calls to the engine in total across five scripts:
  `probe_read.py` 4 reads; `real_engine.py` ~17 (1 run create, 3 port reads,
  3 create attempts, 4 tunnel lists, 2 run stops, 4 deletes);
  `real_dup.py` 7; `final_read.py` 3.
- Read-only fake-engine probes (dispatcher + screens) were used for the rest.

## Summary table

| id | severity | one-line title |
|----|----------|----------------|
| F-01 | critical | Re-raised refusals render nothing — the guard gets an `Update`, not the callback/message |
| F-02 | high | Callback publish leaves an orphaned "Publishing…" message |
| F-03 | high | Double-tap of the same port creates two tunnels (real-engine confirmed) |
| F-04 | medium | Gate ignores SUSPENDED/LOCKED; Publish is offered then fails |
| F-05 | medium | `tunnels_disabled` redraws from stale local state, dropping the engine's reason |
| F-06 | medium | Unattributed listening ports are silently dropped from the picker |
| F-07 | low | Raw English tunnel status (also in AR) and a live-looking LINK for a stopped tunnel |
| F-08 | low | Dead/misleading code (`_short`, `_status_label`, unused `PORTS_TIMEOUT_S`) |
