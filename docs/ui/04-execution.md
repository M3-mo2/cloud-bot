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
  <li><b>أمر سريع</b> — أمر قصير، والنتيجة خلال ٦٠ ثانية كحد أقصى.</li>
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
<p><i>يعمل لمدة ٦٠ ثانية كحد أقصى. تُعرض المخرجات هنا.</i></p>
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
<h2>✅ انتهى بنجاح (الرمز ٠)</h2>
<pre><code class="language-text">hello from main.py
args: ['a', 'b']
</code></pre>
<details><summary>الخطأ القياسي (فارغ)</summary><pre><code></code></pre></details>
<p><i>٦٨ ملّي ثانية</i></p>
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
<h2>❌ فشل (الرمز ١)</h2>
<pre><code class="language-text"></code></pre>
<details open><summary>الخطأ القياسي</summary><pre><code class="language-text">Traceback (most recent call last):
  File "/workspace/main.py", line 3, in &lt;module&gt;
    raise SystemExit(1)
SystemExit: 1
</code></pre></details>
<p><i>١٢٠ ملّي ثانية</i></p>
```

### 2.4 Timeout / truncated / empty

- **Timed out:** heading `⏱️ Stopped (60 s limit)` /
  `⏱️ تم الإيقاف (حد ٦٠ ثانية)`; body explains the hard cap.
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
<p><i>حُدّث الآن · ٣١٢ بايت</i></p>
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
  <tr><th>Duration</th><td>4.2 s</td></tr>
  <tr><th>Output</th><td>1.4 KB</td></tr>
</table>
```
Arabic:

```html
<h2>✅ انتهى بنجاح (الرمز ٠)</h2>
<pre><code class="language-text">done: 500 records
</code></pre>
<details><summary>الخطأ القياسي (فارغ)</summary><pre><code></code></pre></details>
<table is_bordered="true">
  <tr><th>المدة</th><td>٤٫٢ ثانية</td></tr>
  <tr><th>المخرجات</th><td>١٫٤ كيلوبايت</td></tr>
</table>
```

### 4.3 Terminal states (banner + heading)

Each terminal run status maps to a humanized heading (full table in
[`05-errors-and-status.md`](05-errors-and-status.md)):

| `run.status` | EN heading | AR heading |
| --- | --- | --- |
| `exited` (exit 0) | ✅ Finished (exit 0) | ✅ انتهى بنجاح (الرمز ٠) |
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
| 🧹 مسح | 🧹 Clear output | inline | primary | — |
| ⬅️ رجوع | ⬅️ Back | inline | primary | leaves stream running |

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
  <tr><th>Run</th><th>Status</th><th>Command</th><th>When</th></tr>
  <tr><td><code>run_…a1</code></td><td>✅ Finished</td><td><code>python3 main.py</code></td><td>2 min ago</td></tr>
  <tr><td><code>run_…b2</code></td><td>🐌 Rate limited</td><td><code>dd if=/dev/zero …</code></td><td>1 h ago</td></tr>
  <tr><td><code>run_…c3</code></td><td>⏱️ Timed out</td><td><code>python3 slow.py</code></td><td>3 h ago</td></tr>
</table>
```

### 5.2 Arabic (`is_rtl=True`)

```html
<h2>📜 التشغيلات</h2>
<p>١٢ تشغيلًا</p>
<table is_bordered="true">
  <tr><th>التشغيل</th><th>الحالة</th><th>الأمر</th><th>الوقت</th></tr>
  <tr><td><code>run_…a1</code></td><td>✅ انتهى بنجاح</td><td><code>python3 main.py</code></td><td>قبل دقيقتين</td></tr>
  <tr><td><code>run_…b2</code></td><td>🐌 بلغ حد السرعة</td><td><code>dd if=/dev/zero …</code></td><td>قبل ساعة</td></tr>
  <tr><td><code>run_…c3</code></td><td>⏱️ انتهت المهلة</td><td><code>python3 slow.py</code></td><td>قبل ٣ ساعات</td></tr>
</table>
```

### 5.3 History keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🔎 الكل | 🔎 All | inline | primary | filter |
| ✅ انتهت | ✅ Finished | inline | primary | filter exited |
| ❌ فشلت | ❌ Failed | inline | primary | filter failed/killed |
| ⏳ النشطة | ⏳ Active | inline | primary | filter queued/running |
| ➕ المزيد | ➕ More | inline | primary | next page |
| 🗑 مسح السجل | 🗑 Purge history | inline | danger | confirm; refused if a run is live |
| ⬅️ رجوع | ⬅️ Back | inline | primary | — |

The active filter is shown as a **`disabled=True`** label on the current row.

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

## 7. Interactive terminal — sticky input (Phase 1.5, task 16)

A workspace's **🖥 Terminals** opens the engine's native PTY sessions (never a
hand-rolled FIFO; INTEGRATION-GUIDE §5.4). Each command used to need a fresh tap
on **⌨️ Send a command** — the user reported it verbatim:
*«عايز الترمنال اقدر اكتب فيها علطول مش كل مره ادوس ارسال امر»*.

**The rule: entering command mode is sticky.** One tap on **⌨️ Send a command**
enters it; from then on **plain text messages keep going to that session** until
the user explicitly stops. There is no re-tap per command.

### 7.1 Entering and the live view

Tapping **⌨️ Send a command** does **not** open a separate prompt: it enters the
sticky state and (re)renders the **terminal view itself**, so the state is always
paired with the visible terminal buttons. While the user is typing, the view
adds one italic line and one button:

English:

```html
<h2>🖥 Terminal</h2>
<p><b>Status:</b> 🟢 Running</p>
<pre><code class="language-text">$ </code></pre>
<p><i>This terminal closes after 15m 00s idle, or 1h 00m 00s after it opens.</i></p>
<p><i>This terminal is active. Send a command, or use the buttons below.</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>🖥 الطرفية</h2>
<p><b>الحالة:</b> 🟢 قيد التشغيل</p>
<pre><code class="language-text">$ </code></pre>
<p><i>تُغلق هذه الطرفية بعد ١٥ د ٠٠ ث من الخمول، أو ١ س ٠٠ د ٠٠ ث من فتحها.</i></p>
<p><i>هذه الطرفية نشِطة. أرسل أمرًا، أو استخدم الأزرار بالأسفل.</i></p>
```

### 7.2 Keyboard (sticky)

Every button the terminal view normally carries is **kept**, plus the one-tap
exit. A plain message in this state is the command (sent with Enter).

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ⌨️ إرسال أمر | ⌨️ Send a command | inline | primary | keeps the mode |
| ⏹ إيقاف الكتابة | ⏹ Stop typing | inline | danger | **exits the mode** (R3) |
| Ctrl-C | Ctrl-C | inline | primary | — |
| Ctrl-D (نهاية الملف) | Ctrl-D (EOF) | inline | primary | — |
| ⏹ إغلاق | ⏹ Close | inline | danger | — |
| 🖥 طرفياتي | 🖥 My terminals | inline | primary | **the single exit** (task term26) |

### 7.3 Leaving the mode — three ways, none of them a dead end

1. **⏹ Stop typing** (danger) clears the state and returns to the ordinary
   terminal view. This is the primary exit.
2. **A known slash-command** (`/start`, `/help`, `/settings`, `/admin`, …) clears
   the state *before* the routers run, so a sticky state can never swallow a
   command — it always does its own thing (R4).
3. **The session ending** (close, Ctrl-D, idle/lifetime, workspace stop) clears
   the state and renders the ended screen, so the mode never outlives the session
   it pointed at (R5).

An **unknown** slash-command is deliberately *not* sent into the session: it
re-renders the terminal and the sticky state is kept, so it neither vanishes nor
becomes terminal input.

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

### 7.6 My terminals — compact and paginated (task term26)

The list is one line per terminal (`id · status · relative age`), running first
then newest, and paginated with the **workspace list's own contract**
(`PAGE_SIZE = 10`, `page`/`total_pages`/`has_more`, `⬅️ Previous` / `Next ➡️`,
`صفحة {page} من {total}`). Per-session buttons follow the page, so the keyboard
cannot grow one-for-one with the session count; **Open terminal** and **Back**
stay fixed at the bottom. The count is the page's count. The `too_many_sessions`
cap path still renders this same list.

### 7.7 `command_prompt` is retired

The separate **⌨️ Send a command** prompt screen is no longer shown on this path:
the sticky mode edits the terminal view in place instead. (The catalog keys
remain, but the handler no longer routes here.)
