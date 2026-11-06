# UI 03 — Files

File management: **browse**, **view**, **create directory**, **rename**,
**delete**, and **upload**. All paths are workspace-relative (root is `/`); the
engine exposes them at `/v1/workspaces/{id}/files*`. Every Arabic screen is sent
with **`is_rtl=True`**.

File names and paths are user-supplied and are always rendered inside `<code>` and
escaped, so mixed-direction names cannot corrupt a layout
([`../architecture/06-i18n.md`](../architecture/06-i18n.md) §5.1).

> **Scope note.** Phase 1 views **text** files in a rich code block. Previewing or
> delivering **binary/large** files is deferred; the screen shows metadata and a
> clear "preview not available" note rather than leaking bytes. The rationale is
> in [`../DECISIONS.md`](../DECISIONS.md) (■ file delivery vs. messages).

---

## 1. File browser (directory listing)

The engine returns entries already sorted **directories first, then files, each
byte-wise by name**, paged with `count`/`total`/`truncated` (SPEC §2.3). The bot
pages through them.

### 1.1 English — `/` root

```html
<h2>📁 Files</h2>
<p><code>/</code></p>
<hr>
<table is_bordered="true">
  <tr><th>Name</th><th>Type</th><th>Size</th></tr>
  <tr><td>📁 <code>src</code></td><td>folder</td><td>—</td></tr>
  <tr><td>📁 <code>data</code></td><td>folder</td><td>—</td></tr>
  <tr><td>🐍 <code>main.py</code></td><td>python · runnable</td><td>812 B</td></tr>
  <tr><td>📄 <code>README.md</code></td><td>file</td><td>1.2 KB</td></tr>
</table>
<p><i>4 items · page 1 of 1</i></p>
```

### 1.2 Arabic (`is_rtl=True`)

```html
<h2>📁 الملفات</h2>
<p><code>/</code></p>
<hr>
<table is_bordered="true">
  <tr><th>الاسم</th><th>النوع</th><th>الحجم</th></tr>
  <tr><td>📁 <code>src</code></td><td>مجلد</td><td>—</td></tr>
  <tr><td>📁 <code>data</code></td><td>مجلد</td><td>—</td></tr>
  <tr><td>🐍 <code>main.py</code></td><td>python · قابل للتشغيل</td><td>٨١٢ بايت</td></tr>
  <tr><td>📄 <code>README.md</code></td><td>ملف</td><td>١٫٢ كيلوبايت</td></tr>
</table>
<p><i>٤ عناصر · صفحة ١ من ١</i></p>
```

Notes:
- The `runnable` field from the engine drives the "python · runnable / قابل
  للتشغيل" label and a file icon; the bot **never guesses** runnability
  (INTEGRATION-GUIDE §3.2).
- `truncated:true` from the engine adds a row: "⚠️ Some items are hidden (listing
  truncated)." / "⚠️ بعض العناصر مخفية (القائمة مقطوعة)."

### 1.3 Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📁 src | 📁 src | inline | primary | opens directory |
| 📁 data | 📁 data | inline | primary | opens directory |
| 📄 main.py | 📄 main.py | inline | primary | opens file view |
| 📄 README.md | 📄 README.md | inline | primary | opens file view |
| 📤 رفع ملف | 📤 Upload | inline | success | — |
| ➕ مجلد جديد | ➕ New Folder | inline | primary | — |
| ➕ المزيد | ➕ Load more | inline | primary | if more items |
| 🔙 الأعلى | 🔙 Up | inline | primary | if path ≠ `/` |
| 📦 المساحة | 📦 Workspace | inline | primary | back to detail |

### 1.4 Breadcrumbs

Above the list, the current path renders as tappable segments. In a keyboard row:

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🏠 / | 🏠 / | inline | primary | navigate to the root |
| src | src | inline | primary | navigate to `/src` |

The current directory is **omitted**, not shown as a muted label (R1 — it points
at the screen you are already on), and each ancestor crumb navigates to *that*
directory (`file:cd`), not back to the root. This also removed the duplicate
`file:ls:<ws>:0` the empty root-level listing used to carry.

### 1.5 Empty directory

English:

```html
<h2>📁 Files</h2>
<p><code>/src</code></p>
<hr>
<p>This folder is empty.</p>
```

Arabic:

```html
<h2>📁 الملفات</h2>
<p><code>/src</code></p>
<hr>
<p>هذا المجلد فارغ.</p>
```

Keyboard keeps **📤 Upload** (success), **➕ New Folder** (primary), **🔙 Up**
(primary).

---

## 2. File view

### 2.1 Text file (`main.py`)

The engine returns the JSON wrapper when `meta=true`: `{path, content, encoding,
size, total_size, range, whole_file, sha256, content_type, truncated, binary}`
(INTEGRATION-GUIDE §3.1). The bot renders `content` in a code block.

English:

```html
<h2>🐍 main.py</h2>
<table is_bordered="true">
  <tr><th>Size</th><td>812 B</td></tr>
  <tr><th>Type</th><td>text/x-python</td></tr>
  <tr><th>Runnable</th><td>python3</td></tr>
</table>
<pre><code class="language-python">def greet(name):
    print(f"Hello, {name}")

if __name__ == "__main__":
    greet("world")
</code></pre>
<p><i>sha256 9f2c…a1 · lines 1–5 of 5</i></p>
```

### 2.2 Arabic (`is_rtl=True`)

```html
<h2>🐍 main.py</h2>
<table is_bordered="true">
  <tr><th>الحجم</th><td>٨١٢ بايت</td></tr>
  <tr><th>النوع</th><td>text/x-python</td></tr>
  <tr><th>قابل للتشغيل</th><td>python3</td></tr>
</table>
<pre><code class="language-python">def greet(name):
    print(f"Hello, {name}")

if __name__ == "__main__":
    greet("world")
</code></pre>
<p><i>sha256 9f2c…a1 · الأسطر ١–٥ من ٥</i></p>
```

> Code blocks render in logical (LTR) order regardless of `is_rtl`, so code is
> never visually mangled in the Arabic UI.

### 2.3 Truncated / large text

When the read is partial (`truncated:true` or `whole_file:false`), a notice is
prepended and a **Show more** action appears (requesting the next line range with
`start_line`/`end_line`):

English:

```html
<blockquote>⚠️ Showing the first part of this file.</blockquote>
```
Arabic:

```html
<blockquote>⚠️ يتم عرض الجزء الأول من هذا الملف.</blockquote>
```

### 2.4 Binary or unviewable file

The engine flags `binary:true` (bounded NUL/invalid-UTF-8 sample). No bytes are
rendered; metadata only, and a clear note. (Delivery as a document is Phase 2.)

English:

```html
<h2>📦 archive.zip</h2>
<table is_bordered="true">
  <tr><th>Size</th><td>4.1 MB</td></tr>
  <tr><th>Type</th><td>application/zip</td></tr>
</table>
<blockquote>👁 Preview isn't available for this file type.</blockquote>
```

Arabic (`is_rtl=True`):

```html
<h2>📦 archive.zip</h2>
<table is_bordered="true">
  <tr><th>الحجم</th><td>٤٫١ ميغابايت</td></tr>
  <tr><th>النوع</th><td>application/zip</td></tr>
</table>
<blockquote>👁 لا تتوفر معاينة لهذا النوع من الملفات.</blockquote>
```

### 2.5 File view keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ▶️ تشغيل | ▶️ Run | inline | success | only if `runnable`; else omitted |
| ✏️ إعادة تسمية | ✏️ Rename | inline | primary | — |
| 🗑 حذف | 🗑 Delete | inline | danger | confirm |
| ➡️ التالي | ➡️ Show more | inline | primary | only if truncated |
| 🔙 المجلد | 🔙 Folder | inline | primary | back to listing |
| 📦 المساحة | 📦 Workspace | inline | primary | — |

`Run` appears **only** when the engine reported a `runnable` interpreter — the bot
does not decide runnability itself.

---

## 3. Create a directory

Prompt uses `force_reply=True`.

English:

```html
<h2>➕ New Folder</h2>
<p>Send the folder path (for example <code>/src/utils</code>).</p>
```

Arabic:

```html
<h2>➕ مجلد جديد</h2>
<p>أرسل مسار المجلد (مثال <code>/src/utils</code>).</p>
```

Keyboard: **✖️ Cancel** (inline, primary), markup `force_reply=True`.

Result:

```html
<blockquote>✅ Folder <code>/src/utils</code> created.</blockquote>
```
```html
<blockquote>✅ تم إنشاء المجلد <code>/src/utils</code>.</blockquote>
```

---

## 4. Rename

Prompt uses `force_reply=True`, prefilled context shown as the current path.

English:

```html
<h2>✏️ Rename</h2>
<p>Current: <code>/old.txt</code></p>
<p>Send the new path (for example <code>/docs/new.txt</code>).</p>
```

Arabic:

```html
<h2>✏️ إعادة تسمية</h2>
<p>الحالي: <code>/old.txt</code></p>
<p>أرسل المسار الجديد (مثال <code>/docs/new.txt</code>).</p>
```

Keyboard: **✖️ Cancel** (primary), `force_reply=True`, plus a quick **📁 Same
folder** helper (primary) that renames within the current directory.

Result:

```html
<blockquote>✅ Renamed to <code>/docs/new.txt</code>.</blockquote>
```
```html
<blockquote>✅ تمت إعادة التسمية إلى <code>/docs/new.txt</code>.</blockquote>
```

---

## 5. Delete a file

Confirmation, destructive, `danger`.

English:

```html
<h2>🗑 Delete file?</h2>
<p><code>/main.py</code> will be permanently deleted.</p>
<blockquote>⚠️ This cannot be undone.</blockquote>
```

Arabic:

```html
<h2>🗑 حذف الملف؟</h2>
<p>سيُحذف <code>/main.py</code> نهائيًا.</p>
<blockquote>⚠️ لا يمكن التراجع عن هذا الإجراء.</blockquote>
```

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ✅ حذف | ✅ Delete | inline | danger | — |
| ✖️ إلغاء | ✖️ Cancel | inline | primary | — |

Result: `<blockquote>🗑 Deleted <code>/main.py</code>.</blockquote>` /
`<blockquote>🗑 تم حذف <code>/main.py</code>.</blockquote>`, then back to the
listing.

---

## 6. Upload a file

`PUT`/multipart is a POST with `path` + `file` (INTEGRATION-GUIDE §3). The prompt
asks the user to send the file; the caption states the size cap derived from
`meta.archives.limits.upload_mb` and the user's plan.

### 6.1 Prompt (`force_reply=True`)

English:

```html
<h2>📤 Upload</h2>
<p>Send me the file to upload into <code>/src</code>.</p>
<p><i>Maximum size: 25 MB.</i></p>
```

Arabic:

```html
<h2>📤 رفع ملف</h2>
<p>أرسل لي الملف لرفعه إلى <code>/src</code>.</p>
<p><i>الحجم الأقصى: ٢٥ ميغابايت.</i></p>
```

Keyboard: **✖️ Cancel** (primary), **📁 Copy to /  نسخ إلى /** (primary, optional
target-folder picker).

### 6.2 Over-large file (pre-check)

The bot checks size **before** uploading. English:

```html
<h2>📤 Upload</h2>
<blockquote>⚠️ That file is too large (48 MB). The limit is 25 MB.</blockquote>
```
Arabic:

```html
<h2>📤 رفع ملف</h2>
<blockquote>⚠️ هذا الملف كبير جدًا (٤٨ ميغابايت). الحد هو ٢٥ ميغابايت.</blockquote>
```

### 6.3 Progress

A rich progress line updates in place while streaming:

English:

```html
<h2>📤 Uploading…</h2>
<p><b>report.pdf</b> · 12.4 MB / 25 MB</p>
<pre><code>[█████░░░░░░░░] 50%</code></pre>
```
Arabic (`is_rtl=True`):

```html
<h2>📤 جارٍ الرفع…</h2>
<p><b>report.pdf</b> · ١٢٫٤ ميغابايت / ٢٥ ميغابايت</p>
<pre><code>[█████░░░░░░░░] ٥٠٪</code></pre>
```

### 6.4 Result

```html
<blockquote>✅ <b>report.pdf</b> uploaded to <code>/src</code> (1.2 MB).</blockquote>
```
```html
<blockquote>✅ تم رفع <b>report.pdf</b> إلى <code>/src</code> (١٫٢ ميغابايت).</blockquote>
```

Then the listing screen for `/src`.

---

## 7. Transitions

| From | Action | Result |
| --- | --- | --- |
| Workspace detail | "Files" | listing `/` |
| Listing | tap folder | listing of folder |
| Listing | tap file | file view |
| Listing | breadcrumb segment | listing of segment, current = disabled |
| Listing | "Up" | parent listing |
| Listing | "New Folder" | prompt → listing |
| Listing | "Upload" | prompt → (user sends file) → listing |
| File view | "Run" (runnable only) | execution → run |
| File view | "Rename" | prompt → file view |
| File view | "Delete" | confirm → listing |
| File view | "Show more" | next line range |
| Any | Back | previous screen |
