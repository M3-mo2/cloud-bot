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
  <tr><td><code>demo</code></td><td>نشِطة</td><td>١ نشِط</td><td>٢٫٣ ميغابايت / ٢٥٦ ميغابايت</td></tr>
  <tr><td><code>scraper</code></td><td>متوقفة مؤقتًا</td><td>—</td><td>١٢ ميغابايت / ٥١٢ ميغابايت</td></tr>
</table>
<p><i>صفحة ١ من ١</i></p>
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

**The nav block depends on whether the user is inside a workspace.** Two task-14
rules shape it here. **R1 — a nav entry never points at the screen you are already
on: its own destination is omitted, not muted.** So the list does not carry
`📦 Workspaces` (`ws:list`): that is this screen, and its label and icon are
almost identical to the screen's own title `📦 مساحاتي` (the "two buttons named
مساحة/مساحاتي" that was reported). **R2 — the list also drops `🏠 Menu`**
(`nav:menu`): the menu's body is the same workspace table, so for a user who owns
workspaces it is a second route to the list itself. Outside a session the block
is therefore exactly the three account destinations that are *not* this screen:

```
[ ⚙️ الإعدادات ] [ 🌐 اللغة ]
[ ❓ مساعدة ]
```

When the list is shown *inside* a session the block is the session's (§2.5.2, and
R1 again): `📦 Workspaces` is this screen, so only `🏠 <workspace>` remains.

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

Keyboard: **➕ New Workspace** (inline, success), **🏠 Menu** (inline, primary).

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
  <tr><th>الذاكرة</th><td>٥١٢ ميغابايت</td></tr>
  <tr><th>القرص</th><td>٢٫٣ ميغابايت / ٢٥٦ ميغابايت (١٪)</td></tr>
  <tr><th>التشغيلات</th><td>٣ إجمالًا، ١ نشِط</td></tr>
  <tr><th>آخر نشاط</th><td>قبل دقيقتين</td></tr>
  <tr><th>أُنشئت</th><td>٣ نوفمبر ٢٠٢٦، ٢٠:٤١</td></tr>
</table>
<hr>
<p><b>المعرّف:</b> <code>ws_01M6HJASCWM9Z1PF755GT89NK4</code></p>
```

> `python3`, `python` and the id stay ASCII; the id is in `<code>` and never
> digit-localized (see [`../architecture/06-i18n.md`](../architecture/06-i18n.md) §6).

### 2.3 Keyboard — `active` workspace

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📁 الملفات | 📁 Files | inline | primary | — |
| ▶️ تشغيل | ▶️ Run | inline | success | `disabled` if a run is active |
| 📜 التشغيلات | 📜 Runs | inline | primary | — |
| ⏸️ إيقاف مؤقت | ⏸️ Suspend | inline | primary | — |
| 🚪 تسجيل الخروج من مساحة العمل | 🚪 Log out of workspace | inline | primary | leaves the session |
| 🛑 إيقاف الكل | 🛑 Stop All | inline | danger | confirm |
| 🗑 حذف | 🗑 Delete | inline | danger | confirm |
| … | … | inline | — | the shared nav block (see below) |

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
* **`📦 Workspaces`** in the shared nav block (`ws:list`) *browses/switches* to
  another workspace **without** leaving the session first.

The two labels already read differently (`ws.logout` "تسجيل الخروج من مساحة العمل /
Log out of workspace" vs `menu.workspaces` "📦 المساحات / 📦 Workspaces"), so no
new wording is needed. The nav block sits **last** (below the destructive row),
following the shared bottom-placement rule (§0 of `01-language-and-menu.md`) and
its ≤2-columns, ≤3-rows limit.

> **Only the workspace home changes.** Every *inner* screen — a file browser, a
> run history, a settings form, a confirm — keeps its own `⬅️ رجوع`/`🏠 القائمة`;
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
**up to two entries**, subject to R1 (§1.3):

| Label (AR) | Label (EN) | Callback | Notes |
| --- | --- | --- | --- |
| 🏠 demo | 🏠 demo | `ws:home` | the workspace home; **omitted** on the home itself (R1/R5) |
| 📦 المساحات | 📦 Workspaces | `ws:list` | browse / **switch** to another workspace; omitted on the list itself (R1) |

So on any inside screen that is *not* the workspace home or the list the block is
the two entries `🏠 demo` + `📦 المساحات`; on the workspace home itself only
`📦 المساحات` remains (you are already home); and on the inside list only `🏠 demo`
remains (you are already on the list).

The **account destinations — Settings, Language, Help and Menu — are removed
from the block whenever the user is inside a workspace.** A place the user has
*entered* should not re-offer the account's own screens; that is what made the
workspace home feel cluttered (reported live: the top-level menu appeared at the
very bottom of the session home). They remain reachable exactly as before — the
`/settings` and `/help` commands, and by leaving the workspace. Note this means
the docs must not rely on the inside block for the account menu; that is the
point of the change.

This took the inside block from six entries (home + four destinations + menu) to
two, and the workspace home from **20 buttons to 16** (11 rows to 9 in the AR/EN
keyboard grids), comfortably inside the ≤2-columns, ≤3-rows layout rule.

### 2.6 "Run" disabled while a run is active

When the workspace already has an active run, the Run button is `MUTED`
(`disabled=True`) and a caption explains why:

English: `<p><i>A run is already in progress.</i></p>`
Arabic: `<p><i>هناك تشغيل جارٍ بالفعل.</i></p>`

---

## 3. Create a workspace

Creation is a small wizard. Step 1 uses a **`force_reply=True`** prompt to collect
the name; then, when the engine advertises profiles, a profile choice follows.

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
<p><i>حتى ١٢٨ حرفًا. هذا مجرد اسم — يمكنك تغييره لاحقًا.</i></p>
```

Keyboard (markup has `force_reply=True`):

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

### 3.2 Profile choice (only if `meta.profiles.profiles` has >1 entry)

English:

```html
<h2>🧰 Choose a runtime</h2>
<p>Pick the toolchain this workspace will use.</p>
```

Arabic:

```html
<h2>🧰 اختر بيئة التشغيل</h2>
<p>اختر مجموعة الأدوات التي ستستخدمها مساحة العمل.</p>
```

Keyboard (one button per `profiles[].name`, default marked):

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🐍 Python ✓ | 🐍 Python ✓ | inline | success | default profile |
| 📦 أخرى | 📦 Other | inline | primary | — |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

Profile descriptions come from `meta.profiles.profiles[].description`, shown as a
`<details>` block per profile if the user taps info. Only names the engine offers
are ever shown (the engine refuses unknown profiles).

### 3.3 Limits (optional, advanced)

Hidden by default. An "Advanced ⚙️" inline button (primary) opens a screen whose
sliders are **bounded by `meta.limits_defaults` and `meta.limits_maxima`** — the
bot never offers a value the engine would refuse with `422`.

English:

```html
<h2>⚙️ Advanced</h2>
<table is_bordered="true">
  <tr><th>CPU</th><td>1 core <i>(max 4)</i></td></tr>
  <tr><th>Memory</th><td>512 MB <i>(max 4096 MB)</i></td></tr>
  <tr><th>Disk</th><td>256 MB <i>(max 10240 MB)</i></td></tr>
  <tr><th>Network</th><td>Off</td></tr>
</table>
<p><i>Limits apply to the next run.</i></p>
```

Arabic (`is_rtl=True`):

```html
<h2>⚙️ إعدادات متقدمة</h2>
<table is_bordered="true">
  <tr><th>المعالج</th><td>نواة واحدة <i>(الأقصى ٤)</i></td></tr>
  <tr><th>الذاكرة</th><td>٥١٢ ميغابايت <i>(الأقصى ٤٠٩٦ ميغابايت)</i></td></tr>
  <tr><th>القرص</th><td>٢٥٦ ميغابايت <i>(الأقصى ١٠٢٤٠ ميغابايت)</i></td></tr>
  <tr><th>الشبكة</th><td>مُعطّلة</td></tr>
</table>
<p><i>تُطبّق الحدود على التشغيل التالي.</i></p>
```

Keyboard: **✅ Create** (success, confirm), **✖️ Cancel** (primary).

### 3.4 Success

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
| List | "New Workspace" | name prompt (force_reply) → profile → create → detail |
| Detail | "Files" | file browser |
| Detail | "Run" | execution screen |
| Detail | "Runs" | run history |
| Detail | Suspend | confirm → paused detail |
| Detail | Resume | active detail |
| Detail | Stop All | confirm → active detail + counts |
| Detail | Delete | confirm → list |
| Detail | Log out of workspace | leaves the session → list (with an acknowledgement) |
| Detail | 📦 Workspaces (nav block) | list, staying inside the session |
| Any | Back | previous screen (inline edit; inner screens only — the home has no Back row) |
