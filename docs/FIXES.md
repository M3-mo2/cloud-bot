# Fixed defects — measured, then pinned

A short, factual log of defects found live in production, what the measurement was,
what was wrong, and what now stops them coming back. (Design decisions live in
`DECISIONS.md`; port/tunnel findings in `AUDIT-PORT-PUBLISHING.md`.)

---

## F-22.1 — the document upload never reached the engine

**Reported.** In a workspace, the user opened *Upload a file*, was prompted, sent a
document, and nothing happened — no error, no file, no message.

**Measurement.** The bot's journal carried the exact traceback:

```
File ".../cloudbot/handlers/files.py", line 206, in on_upload_document
File ".../cloudbot/adapters/cloud_service/engine.py", line 274, in upload_file
    data = b"".join([chunk async for chunk in stream])
File ".../cloudbot/handlers/files.py", line 199, in _stream
    async with buffer as reader:
TypeError: 'coroutine' object does not support the asynchronous context manager protocol
RuntimeWarning: coroutine 'Bot.download' was never awaited
```

**What was wrong.** `cloudbot/handlers/files.py` did
`buffer = message.bot.download(document)` **without `await`**, then used `buffer` as
an `async with` context manager. In aiogram 3.31.0 `Bot.download` is a **coroutine**
returning a plain `BinaryIO` (a `BytesIO` when `destination is None`) — a
**synchronous** readable object, not an async context manager. Without the `await`
the variable held a coroutine, so the `async with` raised and the exception died
inside the handler; the user saw nothing. It was green because **no test ever drove
`on_upload_document`** (there was no `FakeBot.download` in the suite at all).

**What now pins it.**

* The handler awaits the download and yields the `BinaryIO`'s chunks
  (`while chunk := buffer.read(64 * 1024)`); the size guard still runs first.
* `tests/unit/test_file_upload.py` adds the missing double — a `download` that
  mirrors aiogram exactly (a coroutine returning a `BinaryIO`) — and drives a
  document through the **real dispatcher**, asserting the engine received the file's
  exact bytes and the refreshed listing came back. It also asserts the seam itself
  (`inspect.iscoroutinefunction(Bot.download)`) and that the handler source has no
  `async with buffer`.
* `scripts/verify.sh` proof 28 drops the `await` and shows that test go red.

**The layer below (checked, not assumed).** `EngineAdapter.upload_file` posts
multipart via `EngineClient.post(..., data={"path": path}, files={"file":
(filename, data)})`. The client wrapper (`adapters/cloud_service/client.py`)
forwards `files=` and `data=` straight to `httpx`, so **multipart is supported**;
verified against the real engine by uploading bytes through the bot's own
`upload_file` and reading them back through its own `read_file` (identical content).

---

## F-22.2 — a terminal button raised `IndexError` after a restart

**Reported.** Tapping a terminal control produced no screen at all.

**Measurement.** After a service restart at 14:38:03, a button drawn by the previous
process was tapped at 14:44:47:

```
File ".../cloudbot/handlers/terminal.py", line 95, in on_list
    ws_id = _wid(decode(cb.data).key[0])
IndexError: tuple index out of range
```

**What was wrong.** Terminal callbacks carry a workspace id **and** a session id,
which together exceed Telegram's 64-byte `callback_data` cap, so they are encoded
through the generic content-addressed codec (CB-8): the compact token is a hash of
the key parts, and the parts live in a **process-global, in-memory** registry. The
14:38:03 restart emptied that registry; at 14:44:47 the tap decoded to `key=()`, and
`key[0]` raised. It is the same *family* as the ``adm:`` Bug A (Task 07): a
producer/consumer arity mismatch, invisible until the button is tapped. The audit
of the whole screen set also found a **second** dead button, `term:resize` (the
**↔️ Resize** control on the terminal view), which had **no handler at all**.

**What now pins it.**

* `presentation/screens/terminal_callbacks.py` is the **only** place `term:` data is
  built and parsed. Its `parse` is **total**: an unknown action, a malformed
  callback or a *stale* token (decoded arity ≠ the action's declared arity) returns
  `InvalidTermCallback` instead of raising. Every terminal handler parses through it
  and answers a stale button with `terminal.stale` — never an `IndexError` and never
  silence.
* A missing handler for `term:resize` was added (the picker screen opens).
* `tests/unit/test_terminal_callbacks.py` pins builder round-trips, the stale-token
  case (the exact live shape), and the live path through the real dispatcher.
* `tests/contract/test_screen_callback_contract.py` walks **every** button of
  **every** screen in `SCREEN_BUILDERS` (both locales) and asserts each is claimed by
  the real dispatcher's own routing — the rule that would have caught both dead
  buttons.
* `scripts/verify.sh` proof 29 indexes a possibly-lost key again and shows the
  stale-button test go red.

**Catalog strings** for the stale screen were added in both locales
(`terminal.stale.title` / `.body` / `.reopen`).

---

## F-23.1 — the callback registry died with the process, so *every* keyed button could raise

**Reported.** Tapping the **⬅️ Back** button on the terminal view produced no
screen. The same report had already produced two point fixes (the `adm:` fix and
the `term:` fix); this is the **root cause** of the whole family.

**Measurement.** Driving the real terminal screen through the real dispatcher,
resetting the codec registry (exactly what a process restart does), then tapping
every button:

```
⌨️ Send a command   ⇒ ✅ a screen
Ctrl-C              ⇒ ⚠️ no response
Ctrl-D (EOF)        ⇒ ⚠️ no response
↔️ Resize           ⇒ ⚠️ no response
⏹ Close            ⇒ ⚠️ no response
🖥 My terminals      ⇒ ⚠️ no response
⬅️ Back             ⇒ ❌ IndexError: tuple index out of range
```

**What was wrong.** `presentation/keyboards/callbacks.py` routed **every**
key-bearing callback through a **process-global, in-memory** token registry
(CB-8). After a restart the registry is empty, `decode` returned `key=()`, and a
handler's `decode(cb.data).key[0]` raised. The `term:` fix (F-22.2) and the
`adm:` fix (Task 07) each only covered **their own prefix**: the terminal view's
**⬅️ Back** is `ws:view:<ws_id>` — a `ws:` action — so it still died, as did
`file:`, `run:`, `port:`, `nav:` wherever they carried ids.

**What now pins it.**

* Key parts are encoded **inline** (`action:a:b`) whenever they fit the 64-byte
  cap — the common case — so they are restart-proof with no registry at all. Only
  a callback that genuinely cannot fit (a workspace **and** a session id, a run
  log, a file path with colons) falls back to the in-memory token, and the handler
  is written to survive its loss.
* `decode` is **total** (never raises; always returns an action and a key tuple),
  and a handler that needs key parts calls `decode_required(data, arity)`, which
  raises `StaleCallback`. The error guard maps that to the correctable
  `err.stale.*` screen — **never an `IndexError` and never silence**, whatever the
  prefix.
* `tests/contract/test_callback_restart_contract.py` walks **every** button of
  **every** screen through the **real dispatcher** with the token registry
  **reset** (and again warm), asserting no tap raises and none renders nothing —
  the single test that makes the class impossible, independent of prefix or
  handler. It also pins the three named prefixes (`ws:`, `file:`, `term:`)
  directly.
* `scripts/verify.sh` proof 30 reintroduces the root cause (the unguarded
  `decode_required`) and shows the contract test go red; proof 29 still covers the
  terminal stale screen.

**Catalog strings** for the generic stale screen were added in both locales
(`err.stale.title` / `err.stale.body`).
