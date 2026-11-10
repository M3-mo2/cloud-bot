# UI 04 — Execution (Exec & Runs)

Two execution models, chosen by the engine and surfaced honestly by the bot
(INTEGRATION-GUIDE §4):

- **`exec`** — one command, output returned inline, **hard-capped at 60 s**. No
  run record is created.
- **`runs`** — asynchronous. Create returns `202` with a `queued` run whose object
  has **no stdout/stderr**; output is read from the **logs endpoint by byte
  offset**, and the run's `status` is polled.

Every Arabic screen is sent with **`is_rtl=True`**. Output text is rendered in a
`<pre><code>` block (logical order, never reordered by RTL).

---

## 1. Choose how to run

Reached from a workspace's **▶️ Run**.

### English

```html
<h2>▶️ Run</h2>
<p>How would you like to run code in <b>demo</b>?</p>
<ul>
  <li><b>Quick command</b> — a short command, result in up to 60 seconds.</li>
  <li><b>Background job</b> — a longer run you can watch live and stop.</li>
</ul>
```

### Arabic (`is_rtl=True`)

```html
<h2>▶️ تشغيل</h2>
<p>كيف تريد تشغيل الكود في <b>demo</b>؟</p>
<ul>
  <li><b>أمر سريع</b> — أمر قصير، والنتيجة خلال 60 ثانية كحد أقصى.</li>
  <li><b>مهمة خلفية</b> — تشغيل أطول يمكنك متابعته مباشرة وإيقافه.</li>
</ul>
```

### Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ⚡ أمر سريع | ⚡ Quick command | inline | primary | exec |
| 📜 مهمة خلفية | 📜 Background job | inline | success | run |
| 📄 تشغيل ملف | 📄 Run a file | inline | primary | if files exist |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

---

## 2. Exec — synchronous result

### 2.1 Command prompt (`force_reply=True`)

English:

```html
<h2>⚡ Quick command</h2>
<p>Send a command to run (for example <code>python3 main.py</code>).</p>
<p><i>Runs for at most 60 seconds. Output is returned here.</i></p>
```

Arabic:

```html
<h2>⚡ أمر سريع</h2>
<p>أرسل أمرًا لتشغيله (مثال <code>python3 main.py</code>).</p>
<p><i>يعمل لمدة 60 ثانية كحد أقصى. تُعرض المخرجات هنا.</i></p>
```

Keyboard: **✖️ Cancel** (primary), `force_reply=True`.

### 2.2 Success result

English:

```html
<h2>✅ Finished (exit 0)</h2>
<pre><code class="language-text">hello from main.py
args: ['a', 'b']
</code></pre>
<details><summary>stderr (empty)</summary><pre><code></code></pre></details>
<p><i>68 ms</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>✅ انتهى بنجاح (الرمز 0)</h2>
<pre><code class="language-text">hello from main.py
args: ['a', 'b']
</code></pre>
<details><summary>الخطأ القياسي (فارغ)</summary><pre><code></code></pre></details>
<p><i>68 ملّي ثانية</i></p>
```

### 2.3 Non-zero exit

English:

```html
<h2>❌ Failed (exit 1)</h2>
<pre><code class="language-text"></code></pre>
<details open><summary>stderr</summary><pre><code class="language-text">Traceback (most recent call last):
  File "/workspace/main.py", line 3, in &lt;module&gt;
    raise SystemExit(1)
SystemExit: 1
</code></pre></details>
<p><i>120 ms</i></p>
```

Arabic:

```html
<h2>❌ فشل (الرمز 1)</h2>
<pre><code class="language-text"></code></pre>
<details open><summary>الخطأ القياسي</summary><pre><code class="language-text">Traceback (most recent call last):
  File "/workspace/main.py", line 3, in &lt;module&gt;
    raise SystemExit(1)
SystemExit: 1
</code></pre></details>
<p><i>120 ملّي ثانية</i></p>
```

### 2.4 Timeout / truncated / empty

- **Timed out:** heading `⏱️ Stopped (60 s limit)` /
  `⏱️ تم الإيقاف (حد 60 ثانية)`; body explains the hard cap.
- **Truncated** (`truncated:true`): a row
  `⚠️ Output was shortened.` / `⚠️ تم اختصار المخرجات.`
- **No output:** `📭 No output.` / `📭 لا توجد مخرجات.`

### 2.5 Keyboard (result)

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🔁 إعادة | 🔁 Run again | inline | primary | — |
| 📜 مهمة خلفية | 📜 Background job | inline | success | — |
| 🧹 مسح | 🧹 Clear | inline | primary | replaces body with empty card |
| ⬅️ رجوع | ⬅️ Back | inline | primary | — |

### 2.6 `exec` does not create a run

The result screen offers **no** run-history link, because the engine creates no
run record for `exec` (SPEC §2.5). The screen's help text says output "is shown
here and not kept."

---

## 3. Background job — create

### 3.1 Command prompt (`force_reply=True`)

English:

```html
<h2>📜 Background job</h2>
<p>Send the command to run in the background.</p>
<p><i>You can watch its output live and stop it anytime.</i></p>
```
Arabic:

```html
<h2>📜 مهمة خلفية</h2>
<p>أرسل الأمر لتشغيله في الخلفية.</p>
<p><i>يمكنك متابعة مخرجاته مباشرة وإيقافه في أي وقت.</i></p>
```

If the user chose **Run a file** instead, the argument prompt is added:
`<p>Arguments (optional):</p>` / `<p>الوسائط (اختياري):</p>`.

### 3.2 Started

After `POST /runs` returns `202` with `status:"queued"`:

English:

```html
<h2>📜 Job started</h2>
<table is_bordered="true">
  <tr><th>Status</th><td>Queued</td></tr>
  <tr><th>Command</th><td><code>python3 main.py</code></td></tr>
  <tr><th>Working dir</th><td><code>/</code></td></tr>
  <tr><th>Run ID</th><td><code>run_01M6H…</code></td></tr>
</table>
<p><i>Tap “Live logs” to watch output.</i></p>
```
Arabic:

```html
<h2>📜 بدأت المهمة</h2>
<table is_bordered="true">
  <tr><th>الحالة</th><td>في الانتظار</td></tr>
  <tr><th>الأمر</th><td><code>python3 main.py</code></td></tr>
  <tr><th>مجلد العمل</th><td><code>/</code></td></tr>
  <tr><th>معرّف التشغيل</th><td><code>run_01M6H…</code></td></tr>
</table>
<p><i>اضغط «السجلّ المباشر» لمتابعة المخرجات.</i></p>
```

### 3.3 Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📜 السجلّ المباشر | 📜 Live logs | inline | primary | — |
| 🔄 تحديث | 🔄 Refresh | inline | primary | — |
| ⏹ إيقاف | ⏹ Stop | inline | danger | confirm |
| 📜 التاريخ | 📜 History | inline | primary | — |
| ⬅️ رجوع | ⬅️ Back | inline | primary | — |

---

## 4. Live logs (the offset protocol, humanized)

Live logs are the engine's logs endpoint read by byte offset
(`{data, offset, next, eof, truncated}`), polled from `next` while the run's
`status` is `queued`/`running` (INTEGRATION-GUIDE §4.2). The bot:

1. **Streams a draft** with `send_rich_message_draft` for a smooth live preview
   (`can_stop=True`, so the user gets a stop button; a
   `stopped_message_generation` update cancels the loop cleanly).
2. On each tick, edits the preview; on a terminal status, **persists** the final
   complete document with `send_rich_message`.

### 4.1 Running

English:

```html
<h2>📜 Live logs</h2>
<p><b>Status:</b> Running ⏳</p>
<pre><code class="language-text">Loading data...
processed 100 records
processed 200 records
</code></pre>
<p><i>Updated just now · 312 B</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>📜 السجلّ المباشر</h2>
<p><b>الحالة:</b> قيد التشغيل ⏳</p>
<pre><code class="language-text">Loading data...
processed 100 records
processed 200 records
</code></pre>
<p><i>حُدّث الآن · 312 بايت</i></p>
```

> **Progress bar.** When the run exposes a finite `timeout_sec`, a bar is added:
> `progress 45% · 2m 15s / 5m`. For `unlimited` runs (`timeout_sec:null`), the
> elapsed time is shown instead and no percentage is implied.

### 4.2 Finished

English:

```html
<h2>✅ Finished (exit 0)</h2>
<pre><code class="language-text">done: 500 records
</code></pre>
<details><summary>stderr (empty)</summary><pre><code></code></pre></details>
<table is_bordered="true">
  <tr><th>Command</th><td><code>python3 main.py</code></td></tr>
  <tr><th>Exit code</th><td><code>0</code></td></tr>
  <tr><th>Duration</th><td>4.2 s</td></tr>
  <tr><th>Output</th><td>1.4 KB</td></tr>
</table>
```
> **The run's screen owns its identity (screens-b).** Command, exit code, duration
> and output are shown here, and while a run is live its command line is printed
> above the progress line. This is why the history row can be three columns.

Arabic:

```html
<h2>✅ انتهى بنجاح (الرمز 0)</h2>
<pre><code class="language-text">done: 500 records
</code></pre>
<details><summary>الخطأ القياسي (فارغ)</summary><pre><code></code></pre></details>
<table is_bordered="true">
  <tr><th>المدة</th><td>4.2 ثانية</td></tr>
  <tr><th>المخرجات</th><td>1.4 كيلوبايت</td></tr>
</table>
```

### 4.3 Terminal states (banner + heading)

Each terminal run status maps to a humanized heading (full table in
[`05-errors-and-status.md`](05-errors-and-status.md)):

| `run.status` | EN heading | AR heading |
| --- | --- | --- |
| `exited` (exit 0) | ✅ Finished (exit 0) | ✅ انتهى بنجاح (الرمز 0) |
| `exited` (exit ≠ 0) | ❌ Failed (exit N) | ❌ فشل (الرمز N) |
| `killed` | ⏹ Stopped | ⏹ أُوقف |
| `timeout` | ⏱️ Timed out | ⏱️ انتهت المهلة |
| `failed` | ⚠️ Could not start | ⚠️ تعذّر البدء |
| `interrupted` | 🔌 Interrupted (server restart) | 🔌 انقطع (إعادة تشغيل الخادم) |
| `oom` | 🧠 Out of memory | 🧠 الذاكرة غير كافية |
| `rate_limited` | 🐌 Write-rate limit reached | 🐌 تم بلوغ حد سرعة الكتابة |

### 4.4 Log cap hit

When the engine reports `truncated:true` (the run/log cap dropped older bytes):

English:

```html
<blockquote>⚠️ Older output was dropped (log size limit).</blockquote>
```
Arabic:

```html
<blockquote>⚠️ تم حذف المخرجات الأقدم (حدّ حجم السجل).</blockquote>
```

### 4.5 Bounded display

The bot keeps the last **~12 KB** of output in the message (rich text limit is
32,768 chars; we stay well below it and avoid a wall of text). When trimmed, a
line notes it: "Showing the latest N KB of output." / "يتم عرض آخر N كيلوبايت من
المخرجات." Earlier output is still readable in the run-history view (via a line
range).

### 4.6 Live-logs keyboard

While running:

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ⏹ إيقاف | ⏹ Stop | inline | danger | confirm |
| 🔄 تحديث | 🔄 Refresh | inline | primary | manual poll |
| ⬅️ رجوع | ⬅️ Back | inline | primary | leaves stream running |

> **No "Clear output" while running (screens-b).** The engine has no
> log-truncation endpoint, so the old button re-read the same bytes and carried
> the same callback as Refresh — a duplicate destination. It is removed; the
> output clears when the run ends (or with the finished run's **Delete log**).

When finished: **⏹ Stop** becomes **🔁 Run again** (primary) and
**🗑 حذف السجل / 🗑 Delete log** (danger).

### 4.7 Stop confirm

English:

```html
<h2>⏹ Stop this job?</h2>
<p>The run is terminated. Its output so far is kept.</p>
```
Arabic:

```html
<h2>⏹ إيقاف هذه المهمة؟</h2>
<p>سيتم إنهاء التشغيل. تبقى المخرجات حتى الآن.</p>
```

Keyboard: **⏹ إيقاف / ⏹ Stop** (danger), **✖️ إلغاء / ✖️ Cancel** (primary).

---

## 5. Run history

`GET /runs?status=&kind=&limit=&offset=` returns newest-first with `total`
(before pagination).

### 5.1 English

```html
<h2>📜 Runs</h2>
<p>12 runs</p>
<table is_bordered="true">
  <tr><th>Run</th><th>Status</th><th>When</th></tr>
  <tr><td><code>run_…a1</code></td><td>✅ Finished</td><td>2 min ago</td></tr>
  <tr><td><code>run_…b2</code></td><td>🐌 Rate limited</td><td>1 h ago</td></tr>
  <tr><td><code>run_…c3</code></td><td>⏱️ Timed out</td><td>3 h ago</td></tr>
</table>
<p><i>Page 1 of 2</i></p>
```

### 5.2 Arabic (`is_rtl=True`)

```html
<h2>📜 التشغيلات</h2>
<p>12 تشغيلًا</p>
<table is_bordered="true">
  <tr><th>التشغيل</th><th>الحالة</th><th>الوقت</th></tr>
  <tr><td><code>run_…a1</code></td><td>✅ انتهى بنجاح</td><td>قبل دقيقتين</td></tr>
  <tr><td><code>run_…b2</code></td><td>🐌 بلغ حد السرعة</td><td>قبل ساعة</td></tr>
  <tr><td><code>run_…c3</code></td><td>⏱️ انتهت المهلة</td><td>قبل 3 ساعات</td></tr>
</table>
<p><i>صفحة 1 من 2</i></p>
```

> **Three columns, not eight (screens-b).** The row is what a user scans — the
> run's identity, its state, and when. The command, duration and exit code are
> **not lost**: tapping a run opens its own screen (`run:logs`), where they are
> shown. The history pages through the shared pager (`PAGE_SIZE`,
> `⬅️ Previous`/`Next ➡️`, 「Page n of m」), and the active filter is carried
> inline so a page turn keeps it.

### 5.3 History keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📜 run_…a1 | 📜 run_…a1 | inline | primary | opens that run (`run:logs`) |
| 🔎 الكل | 🔎 All | inline | primary | filter |
| ✅ انتهت | ✅ Finished | inline | primary | filter exited |
| ❌ فشلت | ❌ Failed | inline | primary | filter failed/killed |
| ⏳ النشطة | ⏳ Active | inline | primary | filter queued/running |
| ⬅️ السابق | ⬅️ Previous | inline | primary | previous page (no page 0) |
| التالي ➡️ | Next ➡️ | inline | primary | next page (no past the last) |
| 🗑 مسح السجل | 🗑 Purge history | inline | danger | confirm; refused if a run is live |
| ⬅️ رجوع | ⬅️ Back | inline | primary | — |

The active filter is **omitted** (R1 — it points at the screen you are already
on), never a muted placeholder. Per-run buttons are two per row.

### 5.4 Purge — conflict

If the engine refuses the purge with `409 conflict` (a run is still live), the
honest screen:

English:

```html
<h2>🗑 Purge history</h2>
<blockquote>⚠️ Can't purge while a run is active. Stop it first.</blockquote>
```
Arabic:

```html
<h2>🗑 مسح السجل</h2>
<blockquote>⚠️ لا يمكن المسح أثناء وجود تشغيل نشِط. أوقفه أولًا.</blockquote>
```

Keyboard: **⏹ إيقاف الكل / ⏹ Stop all** (danger), **⬅️ رجوع / ⬅️ Back** (primary).

---

## 6. Transitions

| From | Action | Result |
| --- | --- | --- |
| Workspace | "Run" | run-mode screen |
| Run-mode | "Quick command" | prompt → exec result |
| Run-mode | "Background job" | prompt → job started |
| Run-mode | "Run a file" | file list → args → job started |
| Job started | "Live logs" | streamed drafts → final screen |
| Live logs | "Stop" | confirm → stopped screen |
| Live logs | terminal status | final persisted screen |
| Workspace | "Runs" | history |
| History | a row | that run's final screen |
| History | "Purge" | confirm → (refused if live) |

---

## 7. Interactive terminal — opening it is being in it (task terminal-always)

A workspace's **🖥 Terminals** opens the engine's native PTY sessions (never a
hand-rolled FIFO; INTEGRATION-GUIDE §5.4). The story in two beats:

- **task 16** made command input *sticky*: one tap on **⌨️ Send a command** and
  plain text kept reaching the session (*«عايز الترمنال اقدر اكتب فيها علطول مش كل
  مره ادوس ارسال امر»*).
- **task terminal-always** removed the button and the tap with it: *"obviously
  when I open a terminal I'm going to run commands."* Opening a terminal **is**
  entering it, so plain text reaches the session immediately; the button was only
  ever the thing that armed the state.

### 7.1 Entering is automatic

`term:open` and `term:new` set `TerminalInput.awaiting_command` (with the session
ids) **as part of opening**, before the view renders. The next plain-text message
goes to that session with no extra tap. There is no **Send a command** button: the
view *is* the input mode.

The body adds one italic line while the input is active (no extra control):

English:

```html
<h2>🖥 Terminal</h2>
<p><b>Status:</b> 🟢 Running</p>
<pre><code class="language-text">$ </code></pre>
<p><i>This terminal closes after 15m 00s idle, or 1h 00m 00s after it opens.</i></p>
<p><i>This terminal is active — type a command and it runs here.</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>🖥 الطرفية</h2>
<p><b>الحالة:</b> 🟢 قيد التشغيل</p>
<pre><code class="language-text">$ </code></pre>
<p><i>تُغلق هذه الطرفية بعد 15 د 00 ث من الخمول، أو 1 س 00 د 00 ث من فتحها.</i></p>
<p><i>هذه الطرفية نشِطة — اكتب أمرًا وسيُشغَّل هنا.</i></p>
```

### 7.2 Keyboard (live view)

A live terminal carries exactly what a terminal needs: no "Send a command", no
"Stop typing". A plain message is the command (sent with Enter).

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| Ctrl-C | Ctrl-C | inline | primary | interrupts the running process |
| ⏹ إيقاف الطرفية | ⏹ Stop terminal | inline | danger | **kills the session and returns to the workspace home** (the one destructive verb, on its own row) |
| ⬅️ رجوع | ⬅️ Back | inline | primary | **leaves without killing**: clears the input state and renders the workspace home |

**Ctrl-D is gone from the view** (TASK terminal-direct). He did not ask for it, and
an EOF that ends the shell looks identical to the stop button while doing
something different. **Close and Stop are one verb**, never two: two names for one
effect is how a hand taps the wrong one. **Back is not a stop**: it is primary and
on its own row, so it can never be mistaken for the danger square above it.

### 7.3 Leaving the mode — four ways, none of them a dead end

1. **⬅️ Back** clears the state and renders the workspace home. The session stays
   alive and is one tap away again (open-or-reuse).
2. **Any non-`term:` callback** (navigation to another screen) clears the state
   *before* dispatch — `TerminalLeaveMiddleware`, one choke point. A user who taps
   anywhere else has left the terminal, so their next plain message is ordinary
   navigation, not a command. This is what makes automatic entry safe.
3. **A known slash-command** (`/start`, `/help`, `/settings`, `/admin`, …) clears
   the state *before* the routers run — `TerminalStickyMiddleware` — so a command
   can never be swallowed (R4).
4. **The session ending** (stop, idle/lifetime, workspace stop) clears the state
   and renders the ended screen, so the mode never outlives the session it
   pointed at (R5).

An **unknown** slash-command is deliberately *not* sent into the session: it
re-renders the terminal and the state is kept, so it neither vanishes nor becomes
terminal input.

The invariant: *the input state is set if and only if the user's last interaction
was with a terminal.*

### 7.4 One command, one follow — cut short on idle, supersede on the next

Each plain message sends the line and then follows the session's output by the
same offset protocol as run logs, updating one message in place. The follower is
**bounded and paced** (an iteration cap and a poll interval), so the engine is
never hammered. Two rules make the follow behave under sticky input:

- **Cut short on idle.** The follower stops once the session produces no new
  output for a couple of consecutive polls; a quiescent command is done. (Before
  this, each command spent the whole budget pacing — measured live at ~30 s per
  command, which made typing a second command wait for the first to finish.)
- **Supersede on a new command.** A newer plain message claims a new follow
  generation; an older follower notices this at its next tick and stops *before*
  its next engine read. Two commands never fight over one message, and the engine
  is never polled by two followers at once.

The state survives because the FSM is per user/chat; a foreign user's message is
refused by the ownership gate and clears the state, and can never reach the
session (R5/R6).

### 7.5 No size control (task term26)

The view carries no size line and no **↔️ Resize** button: the user's viewport is
the Telegram chat, and the app decides how many columns it can draw, so picking
"120×40" promised a capability the medium does not have. The PTY is opened with a
deliberate default (**100×30** — every TUI assumes at least 80×24, and this leaves
room for a status line and a prompt without wrapping so wide that output becomes
unreadable). A program that queries the PTY (any TUI) therefore gets a usable
geometry with no setup.

Power users keep two routes, neither of them a screen:

- **In-session:** `stty cols N rows M` (and `stty size` to read it) — the
  terminal-native, scriptable tool, since it is a real PTY.
- **At open:** the documented `term:open` callback may carry `cols`/`rows`
  (`--cols`/`--rows`); absent, the service default is used unchanged.

Old `term:resize` / `term:rsz` / `term:rsz_custom` buttons still exist in chats
drawn before this change. They are **answered** by re-rendering the live view and
clearing any resize input state — never a dead button and never a swallowed text
message.

### 7.6 The terminal button opens a terminal (task terminal-direct)

**🖥 الطرفيات** on the workspace home is **open-or-reuse**: if this user already has
a **live** session in this workspace, its view is shown; otherwise one is created
and shown. There is no intermediate screen and no archive — one tap from the
workspace home to a working prompt.

What this replaced, and why it was wrong: the button used to render a paginated
list of *every* session ever opened (`ses_… · status · relative age`), and every
idle/lifetime expiry added another dead row. Ten dead rows across three pages, none
of which opened anything, was the natural result of the design — not bad luck. The
list, its pager, its catalog keys, its short-id labels and its `/dev` fixture are
deleted, not hidden: nothing stored relies on the old route (the bot-side handle
store holds session ids, never callback data), so an alias would only resurrect a
route this task removes.

Finished sessions stop accumulating: a handle whose terminal status was observed
longer than a **one-hour grace window** ago is forgotten (the bot owns only the
handle; the engine owns the process). A user already looking at a dead session's
view keeps that rendered view — it simply stops being reachable afterwards. The
dead view offers **➕ New terminal** (a genuinely fresh session), **🗑 Forget this
terminal** and **Back to the workspace** — never a list.

At the `too_many_sessions` cap the screen names the live sessions, each with the
same **⏹ Stop terminal** control, so the message says what to stop and how.

### 7.7 Opening a terminal *is* being in it (task terminal-always)

`term:open` and `term:new` set `TerminalInput.awaiting_command` as part of opening,
so once the live view renders the next plain-text message goes to that session with
no extra tap. There is no **⌨️ Send a command** button any more: a terminal is an
input mode, and the view *is* that mode. The `term:cmd` action, its handler and its
catalog keys are deleted.

The live view's controls are exactly:

```
[Ctrl-C]
[⏹ إيقاف الطرفية]     (destructive: kills the session, goes to the workspace home)
[⬅️ رجوع]              (non-destructive: clears the input state, goes to the workspace home)
```

Leaving by navigation is automatic: `TerminalLeaveMiddleware` (on
`dispatcher.callback_query`) clears the state for any callback data that does not
start with `term:`, before dispatch. A known slash-command still leaves via
`TerminalStickyMiddleware`; an unknown one still re-renders the terminal. The
invariant: *the input state is set if and only if the user's last interaction was
with a terminal.*

The separate `command_prompt` screen stays retired (it predates the sticky mode);
nothing routes to it.
