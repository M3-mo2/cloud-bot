# UI 02 — Workspaces

A **workspace** is the user's private sandbox. This document covers: the list, the
detail view, creation, and the lifecycle actions (suspend / resume / stop /
delete). All Arabic screens are sent with **`is_rtl=True`**.

Status vocabulary used here comes from
[`05-errors-and-status.md`](05-errors-and-status.md): `active` → "نشِطة / Active",
`suspended` → "متوقفة مؤقتًا / Paused", `locked` → "مقفلة / Locked",
`unlimited` → "بلا انتهاء / No expiry".

---

## 1. Workspace list

### 1.1 English

```html
<h2>📦 My Workspaces</h2>
<p>2 workspaces</p>
<table is_bordered="true">
  <tr><th>Name</th><th>Status</th><th>Runs</th><th>Disk</th></tr>
  <tr><td><code>demo</code></td><td>Active</td><td>1 active</td><td>2.3 MB / 256 MB</td></tr>
  <tr><td><code>scraper</code></td><td>Paused</td><td>—</td><td>12 MB / 512 MB</td></tr>
</table>
<p><i>Page 1 of 1</i></p>
```

### 1.2 Arabic (`is_rtl=True`)

```html
<h2>📦 مساحاتي</h2>
<p>مساحتان</p>
<table is_bordered="true">
  <tr><th>الاسم</th><th>الحالة</th><th>التشغيلات</th><th>القرص</th></tr>
  <tr><td><code>demo</code></td><td>نشِطة</td><td>1 نشِط</td><td>2.3 ميغابايت / 256 ميغابايت</td></tr>
  <tr><td><code>scraper</code></td><td>متوقفة مؤقتًا</td><td>—</td><td>12 ميغابايت / 512 ميغابايت</td></tr>
</table>
<p><i>صفحة 1 من 1</i></p>
```

### 1.3 Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📦 demo | 📦 demo | inline | primary | opens detail |
| 📦 scraper | 📦 scraper | inline | primary | opens detail |
| ➕ مساحة جديدة | ➕ New Workspace | inline | success | — |
| ➕ المزيد | ➕ Load more | inline | primary | only if more pages |

The list shows up to 10 per page, then a **Load more** button (primary), which
requests the next page (the engine's offset pagination). When there are no more
pages, the button is omitted.

**The nav block depends on whether the user is inside a workspace.** Outside a
session, the **main menu is the only hub** (TASK nav-onehub): the list does not
repeat the account's destinations. **R1 still holds — a nav entry never points at
the screen you are already on — so the list is not offered its own `ws:list`.**
The block is therefore exactly one entry, the home destination:

```
[ 🏠 الصفحة الرئيسية ] / [ 🏠 Home ]   ← `nav:menu`
```

When the list is shown *inside* a session the block is the session's (§2.5.2, and
R1 again): `📦 My workspaces` is this screen, so only `🏠 <workspace>` remains.

### 1.4 Empty state

English:

```html
<h2>📦 My Workspaces</h2>
<p>You don't have any workspaces yet.</p>
<p>A workspace is your own private sandbox where you can run code safely.</p>
```

Arabic (`is_rtl=True`):

```html
<h2>📦 مساحاتي</h2>
<p>لا توجد لديك أي مساحات عمل بعد.</p>
<p>مساحة العمل هي بيئتك الخاصة المعزولة التي تشغّل فيها الأكواد بأمان.</p>
```

Keyboard: **➕ New Workspace** (inline, success), **🏠 Home** (inline, primary).

---

## 2. Workspace detail

### 2.1 English

```html
<h2>📦 demo</h2>
<p><b>Status:</b> Active</p>
<table is_bordered="true">
  <tr><th>Runtime</th><td>python3</td></tr>
  <tr><th>Profile</th><td>python</td></tr>
  <tr><th>CPU</th><td>1 core</td></tr>
  <tr><th>Memory</th><td>512 MB</td></tr>
  <tr><th>Disk</th><td>2.3 MB / 256 MB (1%)</td></tr>
  <tr><th>Runs</th><td>3 total, 1 active</td></tr>
  <tr><th>Last activity</th><td>2 minutes ago</td></tr>
  <tr><th>Created</th><td>3 Nov 2026, 20:41</td></tr>
</table>
<hr>
<p><b>ID:</b> <code>ws_01M6HJASCWM9Z1PF755GT89NK4</code></p>
```

### 2.2 Arabic (`is_rtl=True`)

```html
<h2>📦 demo</h2>
<p><b>الحالة:</b> نشِطة</p>
<table is_bordered="true">
  <tr><th>بيئة التشغيل</th><td>python3</td></tr>
  <tr><th>الملف التعريفي</th><td>python</td></tr>
  <tr><th>المعالج</th><td>نواة واحدة</td></tr>
  <tr><th>الذاكرة</th><td>512 ميغابايت</td></tr>
  <tr><th>القرص</th><td>2.3 ميغابايت / 256 ميغابايت (1%)</td></tr>
  <tr><th>التشغيلات</th><td>3 إجمالًا، 1 نشِط</td></tr>
  <tr><th>آخر نشاط</th><td>قبل دقيقتين</td></tr>
  <tr><th>أُنشئت</th><td>3 نوفمبر 2026، 20:41</td></tr>
</table>
<hr>
<p><b>المعرّف:</b> <code>ws_01M6HJASCWM9Z1PF755GT89NK4</code></p>
```

> `python3`, `python` and the id stay ASCII; the id is in `<code>` and never
> digit-localized (see [`../architecture/06-i18n.md`](../architecture/06-i18n.md) §6).

### 2.3 Keyboard — `active` workspace

**One flat grid, no disclosure control.** A hosting product's home shows the
tools; a user should not have to discover a «المزيد» button to find them. The
grid reads as a user's day:

```
[ 📁 الملفات ]      [ ▶️ تشغيل ]
[ 🗜 الأرشيفات ]    [ 🖥 الطرفيات ]
[ 📜 التشغيلات ]    [ 📦 قوالب جاهزة ]
[ 🐍 المكتبات ]     [ 🌐 الروابط العامة ]
[ 🔌 المنافذ ]      [ 🧠 العمليات ]
[ 📊 الموارد ]
[ ⚙️ الإعدادات ]
[ 🚪 تسجيل الخروج من مساحة العمل ]
```

Everyday work first, then what the workspace is doing, then observation.
**Settings sits alone on its own row** and is the last grid row: pausing,
stopping everything and deleting live behind it, and a destructive screen is
never adjacent to a button a hand taps by habit. The grid's odd count leaves the
last row with one button; it is never padded with a placeholder.

| Label (AR) | Label (EN) | Callback | Style | Flags |
| --- | --- | --- | --- | --- |
| 📁 الملفات | 📁 Files | `file:ls` | primary | — |
| ▶️ تشغيل | ▶️ Run | `run:choose` | success | `disabled` if a run is active |
| 🗜 الأرشيفات | 🗜 Archives | `arc:menu` | primary | — |
| 🖥 الطرفيات | 🖥 Terminals | `term:open` | primary | opens or reuses a live terminal |
| 📜 التشغيلات | 📜 Runs | `run:history` | primary | — |
| 📦 قوالب جاهزة | 📦 Starter templates | `tpl:list` | primary | owner's home only, not the admin's entered view |
| 🐍 المكتبات | 🐍 Packages | `pkg:list` | primary | — |
| 🌐 الروابط العامة | 🌐 Public links | `tnl:list` | primary | — |
| 🔌 المنافذ | 🔌 Ports | `port:list` | primary | — |
| 🧠 العمليات | 🧠 Processes | `proc:list` | primary | — |
| 📊 الموارد | 📊 Resources | `met:view` | primary | — |
| ⚙️ الإعدادات | ⚙️ Settings | `ws:settings` | primary | own row, last grid row |
| 🚪 تسجيل الخروج من مساحة العمل | 🚪 Log out of workspace | `ws:logout` | primary | leaves the session; one button on its own row |

There is no `ws:more`/`ws:less` and no `ws_detail_more`: the disclosure control
and its routes were deleted (TASK no-more-less), so the home has exactly one
shape. Administrative actions (suspend, stop-all, delete) are on the Settings
screen, not here.

### 2.4 Keyboard — `suspended` workspace

Work on a suspended workspace is refused (`409 workspace_suspended`), so the
app-like actions are **disabled** rather than hidden, and Resume is the primary
action.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ▶️ استئناف | ▶️ Resume | inline | success | — |
| 📁 الملفات | 📁 Files | inline | primary | read-only ops work |
| ▶️ تشغيل | ▶️ Run | inline | — | **disabled** |
| 🚪 تسجيل الخروج من مساحة العمل | 🚪 Log out of workspace | inline | primary | leaves the session |
| 🗑 حذف | 🗑 Delete | inline | danger | confirm |
| … | … | inline | — | the shared nav block (see below) |

### 2.5 Keyboard — `locked` workspace (subscription lapsed)

While locked, the engine refuses compute with `403 subscription_expired`, but
reads and delete are allowed. The bot shows a renewal action.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🔄 تجديد | 🔄 Renew | inline | success | Phase 2 |
| 📁 الملفات | 📁 Files | inline | primary | read-only |
| ▶️ تشغيل | ▶️ Run | inline | — | **disabled** |
| 🚪 تسجيل الخروج من مساحة العمل | 🚪 Log out of workspace | inline | primary | leaves the session |
| 🗑 حذف | 🗑 Delete | inline | danger | confirm |
| … | … | inline | — | the shared nav block (see below) |

#### 2.5.1 The one exit rule

**The workspace home has exactly one *exit*, and it is the explicit log-out;
navigation lives in the shared nav block.** There is **no bottom `⬅️ Back` row**
on this screen, because "back" and "log out" landed on the same destination (the
workspace list) and offering both was pure duplication. The distinction that
remains is by intent:

* **`🚪 Log out of workspace`** (`ws:logout`) *ends the session* — it calls
  `leave` and returns to the list with an acknowledgement. It is present in
  **every** status branch, because being unable to leave a suspended or locked
  workspace would be a trap. It never uses `DANGER` (leaving harms nothing).
* **`📦 My workspaces`** in the shared nav block (`ws:list`) *browses/switches* to
  another workspace **without** leaving the session first.

The two labels already read differently (`ws.logout` "تسجيل الخروج من مساحة العمل /
Log out of workspace" vs `menu.workspaces` "📦 مساحاتي / 📦 My workspaces"), so no
new wording is needed. The nav block sits **last** (below the destructive row),
following the shared bottom-placement rule (§0 of `01-language-and-menu.md`) and
its ≤2-columns, ≤3-rows limit.

> **Only the workspace home changes.** Every *inner* screen — a file browser, a
> run history, a settings form, a confirm — keeps its own `⬅️ رجوع`/`🏠 الصفحة الرئيسية`;
> those are not duplicating anything and are deliberately untouched.

A banner line is added when locked:

```html
<blockquote>⏳ Your plan has expired. Renew to run code again.
Your files are safe.</blockquote>
```
```html
<blockquote>⏳ انتهت صلاحية خطتك. جدّد لتشغيل الأكواد مجددًا.
ملفاتك آمنة.</blockquote>
```

#### 2.5.2 The session nav block — inside is not the account

**While the user is inside a workspace the nav block belongs to the session, not
to the account.** On every screen reachable from a session — the workspace home,
the workspace list, help, settings and the language picker — the shared block is
**exactly one entry**, subject to R1 (§1.3):

| Label (AR) | Label (EN) | Callback | Notes |
| --- | --- | --- | --- |
| 🏠 demo | 🏠 demo | `ws:home` | the workspace home; **omitted** on the home itself (R1/R5), leaving the block empty |

`ws:list` is **not** offered inside a session at all: switching workspaces is an
account action, taken after logging out, and carrying it inside made the block
read as a hierarchy with two ways out on one screen. The single way out is the
explicit log-out.

The **account destinations — Settings, Language, Help and Menu — are removed
from the block whenever the user is inside a workspace.** A place the user has
*entered* should not re-offer the account's own screens; that is what made the
workspace home feel cluttered (reported live: the top-level menu appeared at the
very bottom of the session home). They remain reachable exactly as before — the
`/settings` and `/help` commands, and by leaving the workspace. Note this means
the docs must not rely on the inside block for the account menu; that is the
point of the change.

This took the inside block from six entries (home + four destinations + menu) to
one, comfortably inside the ≤2-columns, ≤3-rows layout rule. TASK nav-onehub left
this inside rule untouched; the one-hub change is about the **outside** screens.

### 2.6 "Run" disabled while a run is active

When the workspace already has an active run, the Run button is `MUTED`
(`disabled=True`) and a caption explains why:

English: `<p><i>A run is already in progress.</i></p>`
Arabic: `<p><i>هناك تشغيل جارٍ بالفعل.</i></p>`

---

## 3. Create a workspace

Creation is one linear flow — no sections inside sections:

```
name → network (internet + public links) → «متابعة» → plan → created
```

The engine's default runtime is used automatically: the runtime/profile picker is
**not** in the flow any more (it was the "section inside a section" the report
objected to, and a workspace's toolchain can be decided after it exists). The
plan picker is the step that makes a workspace; there is no other way to create
one.

### 3.1 Name prompt (send, `force_reply=True`)

English:

```html
<h2>➕ New Workspace</h2>
<p>Send me a name for your workspace.</p>
<p><i>Up to 128 characters. This is just a label — you can rename it later.</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>➕ مساحة جديدة</h2>
<p>أرسل لي اسمًا لمساحة العمل.</p>
<p><i>حتى 128 حرفًا. هذا مجرد اسم — يمكنك تغييره لاحقًا.</i></p>
```

Keyboard (markup has `force_reply=True`):

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

### 3.3 Network and links

The old numeric limits table is gone: **the plan sets cpu, memory and disk**, so
the user is never offered a picker. The only thing a plan cannot know is what the
workspace may reach, so this step carries the two switches and a «متابعة»
(continue) button.

English:

```html
<h2>🌐 Network & links</h2>
<p>Decide what this workspace may reach, then continue to the plans.</p>
<table is_bordered="true">
  <tr><th>Network</th><td>On</td></tr>
  <tr><th>Public links</th><td>Off</td></tr>
</table>
```

Arabic (`is_rtl=True`):

```html
<h2>🌐 الشبكة والروابط</h2>
<p>حدّد ما يمكن لمساحة العمل الوصول إليه، ثم تابع إلى الخطط.</p>
<table is_bordered="true">
  <tr><th>الشبكة</th><td>مُفعّلة</td></tr>
  <tr><th>الروابط العامة</th><td>مُعطّلة</td></tr>
</table>
```

Keyboard: two toggles (`🌐 Network: On/Off`, `🌐 Public links: On/Off`), then
**متابعة** (success) and **✖️ Cancel** (primary).

### 3.4 Plans — the step that makes a workspace

**A workspace cannot exist without a plan.** «متابعة» opens the plan picker, and
no workspace row is written until a plan is chosen. Each row shows the plan, its
period, its price and the allowances it grants; the trial row reads differently
(free, once, 3 days) and disappears once it has been used.

- **Trial** (if unused): 3 days, 0 Stars — creates at once.
- **Paid plan**: charged from the wallet in the same tap. Enough balance → the
  workspace is created with that plan's `to_limits(maxima)` and that plan's
  expiry. Not enough → nothing is created, the balance is untouched, the screen
  says exactly how many Stars are missing and offers **شراء نجوم**; the wizard
  state (name + network) is kept so the user returns to the same plan step.

### 3.5 Success

After the engine returns, the workspace is registered and its detail screen is
shown. A short confirmation block is prepended:

English:

```html
<blockquote>✅ <b>demo</b> is ready.</blockquote>
```

Arabic:

```html
<blockquote>✅ <b>demo</b> جاهزة.</blockquote>
```

---

## 4. Lifecycle confirms

Destructive actions always confirm, and the confirm button is `danger`. The
confirmation is a distinct screen (not a popup) so the consequences are visible.

### 4.1 Delete workspace — confirm

English:

```html
<h2>🗑 Delete workspace?</h2>
<p><b>demo</b> will be permanently deleted.</p>
<blockquote>⚠️ All files inside it will be removed. This cannot be undone.</blockquote>
```

Arabic (`is_rtl=True`):

```html
<h2>🗑 حذف مساحة العمل؟</h2>
<p>سيتم حذف <b>demo</b> نهائيًا.</p>
<blockquote>⚠️ ستُحذف جميع الملفات داخلها. لا يمكن التراجع عن هذا الإجراء.</blockquote>
```

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ✅ حذف نهائيًا | ✅ Delete permanently | inline | danger | — |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

### 4.2 Delete — result

English:

```html
<blockquote>🗑 <b>demo</b> has been deleted.</blockquote>
```
Arabic: `<blockquote>🗑 تم حذف <b>demo</b>.</blockquote>`
Then the list screen.

### 4.3 Stop all — confirm / result

English confirm:

```html
<h2>🛑 Stop everything?</h2>
<p>All runs, terminals and background processes in <b>demo</b> will be stopped.</p>
<p><i>The workspace stays active. Your files are not affected.</i></p>
```
Arabic confirm:

```html
<h2>🛑 إيقاف كل شيء؟</h2>
<p>سيتم إيقاف جميع التشغيلات والجلسات والعمليات الخلفية في <b>demo</b>.</p>
<p><i>تبقى مساحة العمل نشِطة. لا تتأثر ملفاتك.</i></p>
```

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🛑 إيقاف الكل | 🛑 Stop all | inline | danger | — |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

Result uses the engine's counts, humanized:

English:

```html
<blockquote>🛑 Stopped 2 runs and 1 terminal.</blockquote>
```
Arabic:

```html
<blockquote>🛑 تم إيقاف تشغيلين وجلسة واحدة.</blockquote>
```

Zero-count cases read naturally: "Nothing was running. / لا شيء كان يعمل."

### 4.4 Suspend — confirm / result

English confirm:

```html
<h2>⏸️ Suspend workspace?</h2>
<p><b>demo</b> will be paused. Running work stops and new work is refused.</p>
<p><i>Your files are kept. Resume anytime.</i></p>
```
Arabic confirm:

```html
<h2>⏸️ إيقاف مؤقت؟</h2>
<p>سيتوقف <b>demo</b> مؤقتًا. يتوقف العمل الجاري وتُرفض المهام الجديدة.</p>
<p><i>تبقى ملفاتك محفوظة. يمكنك الاستئناف في أي وقت.</i></p>
```

Keyboard: **⏸️ Suspend** (primary — reversible), **✖️ Cancel** (primary).

Result:

```html
<blockquote>⏸️ demo is paused.</blockquote>
```
```html
<blockquote>⏸️ تم إيقاف demo مؤقتًا.</blockquote>
```

### 4.5 Resume — result

```html
<blockquote>▶️ demo is active again.</blockquote>
```
```html
<blockquote>▶️ أصبحت demo نشِطة مرة أخرى.</blockquote>
```

---

## 5. Transitions

| From | Action | Result |
| --- | --- | --- |
| Menu | "My Workspaces" | list |
| List | tap workspace | detail |
| List | "New Workspace" | name prompt (force_reply) → network → plan → detail |
| Detail | "Files" | file browser |
| Detail | "Run" | execution screen |
| Detail | "Runs" | run history |
| Detail | Suspend | confirm → paused detail |
| Detail | Resume | active detail |
| Detail | Stop All | confirm → active detail + counts |
| Detail | Delete | confirm → list |
| Detail | Log out of workspace | leaves the session → list (with an acknowledgement) |
| Detail | 📦 My workspaces (nav block) | list, staying inside the session |
| Any | Back | previous screen (inline edit; inner screens only — the home has no Back row) |
