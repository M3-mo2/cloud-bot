# 04 — Data Flow / Lifecycles

Every user action follows the same spine:

```
Update → middleware (context, throttle, mode) → handler (thin)
       → service (ownership + policy) → EnginePort → Cloud Service
       → domain entities → screen (rich doc + markup) → RichSender → Telegram
```

The handler never touches HTTP, HTML, or SQL. The service never touches aiogram.
The screen never does I/O.

---

## 1. Application startup

```
main() → Settings(mode)
  → logging.setup(level_from(mode))
  → bootstrap.container.build(settings)
       EngineAdapter(httpx.AsyncClient, bearer key)
       SqliteUserRepository, SqliteSessionStore
       Services(engine, users, sessions, clock)
       RichSender(bot)
  → lifecycle.startup()
       GET /v1/meta                     # cache limits, profiles, interpreters
       run DB migrations
       bot.set_my_commands(...)         # per default locale
  → Dispatcher.start_polling()
```

`/v1/meta` is read at startup and **re-read after any 422/409 refusal** whose
`details` suggests a stale cached value (INTEGRATION-GUIDE §1). The meta cache
drives every picker, slider, and cap.

---

## 2. First contact — onboarding and language

```
/start (no stored user)
  handler.common.on_start
    → account.get_or_create(tg_id, default_locale=EN)
    → user is new → screen.language()          # Arabic | English, each in its script
    → sender.send(chat, language_screen)        # plain inline keyboard, no params

callback "lang:ar"
  handler.common.on_language
    → account.set_locale(tg_id, AR)             # persisted
    → menu screen (AR, is_rtl=True)
    → sender.edit(...)
```

Returning users go straight to the menu in their stored locale.

---

## 3. Create a workspace

```
menu → "➕ New Workspace" → callback "ws:new"
  handler.workspaces.on_new
    → FSM → states.input.WaitingName (force_reply prompt: "Send a name")
user sends name
  handler.workspaces.on_name
    → workspaces.create(tg_id, name, profile=meta.default_profile, limits=None)
         ownership: n/a (new)
         pre-validate name length/limits against meta.limits_maxima
         EnginePort.create_workspace(...) → Cloud Service POST /v1/workspaces
         users.add_workspace(tg_id, ws.id)       # register ownership
    → screen.workspace_detail(ws, locale)
    → FSM.clear()
    → sender.send(chat, detail_screen, markup=workspace_actions)
```

The workspace id is registered to the user **after** the engine returns it; if
registration fails, the bot compensates by `DELETE`-ing the orphan (logged).

---

## 4. The isolation gate (every workspace call)

```
service.workspaces.get(tg_id, ws_id)
  ownership.assert_owned(tg_id, ws_id)
      users.owns(tg_id, ws_id)  → False ⇒ raise NotOwned
  EnginePort.get_workspace(ws_id)
  return Workspace(owner_tg_id=tg_id, ...)
```

`NotOwned` renders the "not found / not yours" screen — deliberately identical to
a genuine not-found, so a user cannot probe ids. See `ui/05`.

---

## 5. Browse files

```
workspace detail → "📁 Files" → "file:ls:<ws>:0"
  handler.files.on_list
    → workspaces.get(tg_id, ws)          # ownership
    → files.list(ws, path="/", offset=0, limit=20)
         EnginePort.list_files(...)      # GET /files?path=/&offset=&limit=
    → screen.files(page, locale)
         table: name | type | size | (runnable?)
         dirs first (engine already sorts)
    → sender.edit(chat, msg, files_screen, markup=file_actions+breadcrumbs)
```

Pagination buttons carry `offset`; breadcrumbs map each path segment to a
`file:ls` callback.

---

## 6. Exec — synchronous result

```
workspace → "▶️ Run" → choose file or command
  exec path:
    → execution.exec(ws, cmd, cwd, timeout_sec<=60)
         EnginePort.exec(...)            # POST /exec → {exit_code, stdout, stderr, ...}
    → screen.exec_result(result, locale)
         heading: "✅ Finished" / "❌ Failed (exit 1)"
         <pre><code>stdout</code></pre>
         collapsible <details> for stderr
         footer: duration, truncated flag
    → sender.send(...)
```

`exec` returns inline output and creates **no** run record (SPEC §2.5) — the screen
says so implicitly by not offering run history for it.

---

## 7. Async run with live logs (the interesting one)

```
workspace → "▶️ Run" → "Run as background job"
  → execution.run_file(ws, path)                 # POST /runs → 202 run(status=queued)
  → screen.run_started(run, locale)
  → sender.send(..., markup=[ "📜 Live logs", "⏹ Stop", "🔄 Refresh" ])

user taps "📜 Live logs" → "run:logs:<ws>:<run>:<stream>:<offset>"
  handler.execution.on_logs
    → tail loop (bounded):
        loop:
          chunk = execution.tail_logs(ws, run, stream, offset)
              EnginePort.run_logs(...)            # GET /runs/{id}/logs?offset=...
          append rendered <pre><code> chunk
          offset = chunk.next
          status = execution.get_run(ws, run).status
          if status is terminal and chunk.eof: break
        # after 2 s or on each poll: update the SAME message in place
        sender.edit(chat, msg, run_logs_screen(accumulated, status, locale))
```

**In-place updates** use `edit_message_text(rich_message=...)` (there is no
`edit_rich_message` in 3.31.0). For a *live* preview that should not post a new
message per tick, `RichSender.draft(...)` streams an ephemeral preview via
`send_rich_message_draft`, then the final, complete document is persisted with
`send_rich_message` once the run is terminal. `can_stop=True` gives the user a
stop button; a `stopped_message_generation` update cancels the tail loop cleanly.

Key contract points honored:
- The run object has **no** stdout; output always comes from the logs endpoint
  read by byte offset (`data`, `offset`, `next`, `eof`, `truncated`) — INTEGRATION
  GUIDE §4.2.
- `truncated:true` renders a warning row: "older output was dropped".

---

## 8. Upload a file

```
"📤 Upload" → force_reply prompt → user sends a document/photo
  handler.files.on_upload
    → pre-check size against plan cap (fail early with a friendly screen)
    → files.upload(ws, path="/"+filename, stream)
         EnginePort.upload_file(...)     # POST /files/upload multipart
    → screen.upload_done(name, size, locale)
```

Limit is read from `/v1/meta.archives.limits.upload_mb` and the user's plan; both
must pass.

---

## 9. Error handling (universal)

```
any service raises DomainError
  → middleware.error_guard catches it
       EngineRefused(code="workspace_suspended")
         → mapping.errors.error_screen(code, locale)
             AR: "⏸️ هذه المساحة متوقفة مؤقتًا."
             EN: "⏸️ This workspace is paused."
             + action buttons: Resume (success) / Back (primary)
       EngineUnavailable(retryable=True)
         → "⚙️ الخدمة غير متاحة مؤقتًا…" / "⚙️ Service temporarily unavailable…"
           + Retry (primary)
       NotOwned / WorkspaceMissing
         → the not-found screen
  → sender.(edit|send)(error_screen)
  → observability logs code + correlation id (never shown to the user)
```

The raw engine `message` (e.g. `"sandbox unavailable: nsjail could not set up..."`)
is **never** rendered to a normal user. It is mapped to a human sentence; the raw
text is logged and, in DEBUG for admins, offered collapsed in a `<details>` block.

---

## 10. Lifecycle actions

| Action | Service | Engine call | Confirm? | Result screen |
| --- | --- | --- | --- | --- |
| Stop | `workspaces.stop` | `POST /stop` | no | "Everything stopped" + counts |
| Suspend | `workspaces.suspend` | `POST /suspend` | yes (danger) | "Paused" + Resume button |
| Resume | `workspaces.resume` | `POST /resume` | no | "Active again" |
| Delete | `workspaces.delete` | `DELETE /workspaces/{id}` | yes (danger, typed name) | back to list |

Delete is destructive: the confirm button is `danger`, and the confirmation screen
shows exactly what is lost ("all files will be permanently removed").

---

## 11. Concurrency and rate limiting

- One `UserContext` per update; services are stateless and safe to call
  concurrently.
- `throttle.py` limits updates per user (token bucket). Exceeding it answers the
  callback silently and shows a "slow down" screen rather than hammering the
  engine.
- The engine has **no** server-side rate limiting (SPEC §2.6.1) — the bot is the
  only throttle, so it must be real.
- Long operations (log tailing) are bounded and cancellable; the loop checks the
  run status each tick and stops when terminal.

---

## 12. Readiness

The bot exposes no public HTTP (polling). Its readiness is internal:
`lifecycle.startup()` fails fast if `/v1/meta` is unreachable or the DB cannot
migrate, and logs a clear operator message. A `/health` admin command (DEBUG/DEV)
reports engine `/healthz` + `/readyz` state and the cached meta version.
