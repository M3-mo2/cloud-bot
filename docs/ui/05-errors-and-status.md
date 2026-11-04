# UI 05 — Errors & Statuses (the no-raw-output guarantee)

This is the **single source of truth** for turning engine values into human words.
Nothing else in the bot is allowed to produce an error or status string.

Two hard rules:

1. **Every** `error.code`, `run.status`, `workspace.status`, `summary.state`, and
   `session.status` that the engine can emit **must** appear in this document,
   with an Arabic and an English sentence.
2. A normal user must never see a raw engine token (e.g. `rate_limited`,
   `unprocessable`), a JSON brace, an HTTP status code, or the engine's English
   `message`. A test enforces this for every entry.

Sources of truth, in precedence order: `docs-cloud-service/INTEGRATION-GUIDE.md`
§9.3, then `SPEC.md` §2.8. When this document and those disagree, they win and
this document has a bug.

**Debug exception.** In DEBUG mode, for admins only, a collapsed `<details>` block
may append the raw payload below the human sentence. It never replaces it, and it
never appears outside DEBUG+admin.

All Arabic output is sent with **`is_rtl=True`**.

---

## 1. Engine error codes → user sentences

Rendered with a title, the human sentence, and the action buttons shown. `generic
retry` means a **🔄 Retry / 🔄 إعادة** primary button plus **⬅️ Back**.

### 1.1 400 / 405 — malformed request

| Field | Value |
| --- | --- |
| `code` | `validation` |
| HTTP | 400 (also **405** method-not-allowed carries `code:"validation"`) |
| EN | "Something in the request was invalid." |
| AR | "كان هناك خطأ في الطلب." |
| Notes | Usually the bot's own bug. Log `details`; user gets a soft message. |
| Buttons | Retry (primary), Back (primary) |

Rendered (EN):

```html
<h2>⚠️ Something went wrong</h2>
<p>Something in the request was invalid.</p>
<p><i>If this keeps happening, please try again later.</i></p>
```
Rendered (AR, `is_rtl=True`):

```html
<h2>⚠️ حدث خطأ</h2>
<p>كان هناك خطأ في الطلب.</p>
<p><i>إذا تكرر ذلك، يرجى المحاولة لاحقًا.</i></p>
```

### 1.2 401 — bad credentials (internal, never the user's fault)

| Field | Value |
| --- | --- |
| `code` | `unauthorized` |
| HTTP | 401 |
| EN | "The service is not configured correctly. The team has been notified." |
| AR | "الخدمة غير مهيأة بشكل صحيح. تم إبلاغ الفريق." |
| Notes | Operator config error. Log loudly; user sees a neutral message. |
| Buttons | Back (primary) |

### 1.3 403 — not yours

| Field | Value |
| --- | --- |
| `code` | `forbidden` |
| HTTP | 403 |
| EN | "That resource isn't available." |
| AR | "هذا المورد غير متاح." |
| Notes | Rendered **identically** to not-found (see §1.5) so ids cannot be probed. |
| Buttons | Back (primary) |

### 1.4 403 — subscription expired (locked)

| Field | Value |
| --- | --- |
| `code` | `subscription_expired` |
| HTTP | 403 |
| EN | "Your plan has expired. Renew to continue. Your files are safe." |
| AR | "انتهت صلاحية خطتك. جدّد للمتابعة. ملفاتك آمنة." |
| Buttons | 🔄 Renew (success, Phase 2), Back (primary) |

Rendered (EN):

```html
<h2>⏳ Plan expired</h2>
<blockquote>Your plan has expired. Renew to continue. Your files are safe.</blockquote>
```
Rendered (AR):

```html
<h2>⏳ انتهت الخطة</h2>
<blockquote>انتهت صلاحية خطتك. جدّد للمتابعة. ملفاتك آمنة.</blockquote>
```

### 1.5 404 — not found

| Field | Value |
| --- | --- |
| `code` | `not_found` |
| HTTP | 404 |
| EN | "That item no longer exists." |
| AR | "هذا العنصر لم يعد موجودًا." |
| Notes | Also the screen for the bot's own `NotOwned` / `WorkspaceMissing`. |
| Buttons | Back (primary) |

### 1.6 404 — ports disabled

| Field | Value |
| --- | --- |
| `code` | `ports_disabled` |
| HTTP | 404 |
| EN | "Port browsing isn't available." |
| AR | "استعراض المنافذ غير متاح." |
| Notes | Phase 2 feature gate; normally the button is hidden and this never shows. |
| Buttons | Back (primary) |

### 1.7 409 — generic conflict

| Field | Value |
| --- | --- |
| `code` | `conflict` |
| HTTP | 409 |
| EN | "That isn't possible right now." |
| AR | "هذا غير ممكن الآن." |
| Notes | e.g. purging run history while a run is live; see §5.4 of UI 04. |
| Buttons | Retry (primary), Back (primary) |

### 1.8 409 — workspace suspended

| Field | Value |
| --- | --- |
| `code` | `workspace_suspended` |
| HTTP | 409 |
| EN | "This workspace is paused." |
| AR | "هذه المساحة متوقفة مؤقتًا." |
| Buttons | ▶️ Resume (success), Back (primary) |

Rendered (EN):

```html
<h2>⏸️ Workspace paused</h2>
<p>This workspace is paused.</p>
<p><i>Resume it to continue working.</i></p>
```
Rendered (AR, `is_rtl=True`):

```html
<h2>⏸️ المساحة متوقفة</h2>
<p>هذه المساحة متوقفة مؤقتًا.</p>
<p><i>استأنفها للمتابعة.</i></p>
```

### 1.9 409 — too many sessions

| Field | Value |
| --- | --- |
| `code` | `too_many_sessions` |
| HTTP | 409 |
| EN | "You have too many terminals open. Close one first." |
| AR | "لديك عدد كبير من الطرفيات المفتوحة. أغلق واحدة أولًا." |
| Notes | Phase 1.5. |
| Buttons | 🖥 My terminals (primary), Back (primary) |

### 1.10 409 — session ended

| Field | Value |
| --- | --- |
| `code` | `session_ended` |
| HTTP | 409 |
| EN | "That terminal has closed." |
| AR | "أُغلق هذا الطرفية." |
| Notes | Phase 1.5. |
| Buttons | ➕ New terminal (success), Back (primary) |

### 1.11 409 — archive entry exists

| Field | Value |
| --- | --- |
| `code` | `archive_entry_exists` |
| HTTP | 409 |
| EN | "A file already exists there. Overwrite it?" |
| AR | "يوجد ملف بهذا الاسم مسبقًا. هل تريد استبداله؟" |
| Buttons | ✅ Overwrite (danger), ✖️ Cancel (primary) |

### 1.12 413 — payload too large

| Field | Value |
| --- | --- |
| `code` | `payload_too_large` |
| HTTP | 413 |
| EN | "That file is too large." |
| AR | "هذا الملف كبير جدًا." |
| Buttons | Back (primary) |

### 1.13 413 — archive too large / too many entries / ratio exceeded

| Field | Value |
| --- | --- |
| `code` | `archive_too_large` / `archive_too_many_entries` / `archive_ratio_exceeded` |
| HTTP | 413 |
| EN | "This archive is too large to extract." |
| AR | "هذا الأرشيف كبير جدًا لاستخراجه." |
| Notes | Three codes, one user meaning (expansion-bomb guards). |
| Buttons | Back (primary) |

### 1.14 415 — unsupported archive

| Field | Value |
| --- | --- |
| `code` | `unsupported_archive` |
| HTTP | 415 |
| EN | "That isn't a zip or rar file." |
| AR | "هذا ليس ملف zip أو rar." |
| Buttons | Back (primary) |

### 1.15 422 — unprocessable

| Field | Value |
| --- | --- |
| `code` | `unprocessable` |
| HTTP | 422 |
| EN | "That request couldn't be accepted." (plus a tailored sub-line when `details` names a field) |
| AR | "تعذّر قبول هذا الطلب." (مع سطر مخصص عند تحديد الحقل) |
| Notes | Covers limits above maxima, bad ranges, unbounded runs not permitted, signals, etc. **The adapter re-reads `/v1/meta` on this code** because the cause is often a stale cap. |
| Buttons | Retry (primary), Back (primary) |

Sub-lines by known `details.field`:

| `details.field` | EN sub-line | AR sub-line |
| --- | --- | --- |
| limit above max | "That value is above the allowed maximum." | "هذه القيمة تتجاوز الحد الأقصى المسموح." |
| `no_timeout` | "This workspace doesn't allow unlimited runs." | "هذه المساحة لا تسمح بتشغيلات غير محدودة." |
| range params | "The requested range is invalid." | "النطاق المطلوب غير صالح." |
| signal | "That signal isn't supported." | "هذه الإشارة غير مدعومة." |
| default | "Read the request details." (logged, not shown raw) | "راجع تفاصيل الطلب." |

Rendered (EN):

```html
<h2>⚠️ Couldn't do that</h2>
<p>That value is above the allowed maximum.</p>
```
Rendered (AR):

```html
<h2>⚠️ تعذّر التنفيذ</h2>
<p>هذه القيمة تتجاوز الحد الأقصى المسموح.</p>
```

### 1.16 422 — unknown profile

| Field | Value |
| --- | --- |
| `code` | `unknown_profile` |
| HTTP | 422 |
| EN | "Please pick one of the offered runtimes." |
| AR | "يرجى اختيار إحدى بيئات التشغيل المتاحة." |
| Buttons | 🧰 Choose runtime (success), Back (primary) |

### 1.17 422 — not a directory

| Field | Value |
| --- | --- |
| `code` | `not_directory` |
| HTTP | 422 |
| EN | "That path isn't a folder." |
| AR | "هذا المسار ليس مجلدًا." |
| Notes | Used when listing a file or writing over a directory. |
| Buttons | Back (primary) |

### 1.18 422 — session needs interpreter

| Field | Value |
| --- | --- |
| `code` | `session_needs_interpreter` |
| HTTP | 422 |
| EN | "Terminals need the Python runtime. Change this workspace's runtime first." |
| AR | "تتطلب الطرفيات بيئة Python. غيّر بيئة تشغيل المساحة أولًا." |
| Notes | Phase 1.5. |
| Buttons | Back (primary) |

### 1.19 422 — subscription invalid / max duration / would shorten

| Field | Value |
| --- | --- |
| `code` | `subscription_invalid` / `max_duration_exceeded` / `subscription_would_shorten` |
| HTTP | 422 |
| EN | "That plan duration isn't valid." |
| AR | "مدة الخطة غير صالحة." |
| Notes | Phase 2. |
| Buttons | Back (primary) |

### 1.20 422 — tunnels disabled / too many tunnels

| Field | Value |
| --- | --- |
| `code` | `tunnels_disabled` / `too_many_tunnels` |
| HTTP | 422 |
| EN (disabled) | "Publishing isn't available for this workspace." | 
| AR (disabled) | "النشر غير متاح لهذه المساحة." |
| EN (too many) | "You've reached the limit for published links. Remove one first." |
| AR (too many) | "وصلت إلى الحد الأقصى للروابط المنشورة. احذف رابطًا أولًا." |
| Notes | Phase 2. |
| Buttons | Back (primary) |

### 1.21 422 — archive encrypted / multivolume / unsafe

| Field | Value |
| --- | --- |
| EN (encrypted) | "That archive is password-protected." | AR | "هذا الأرشيف محمي بكلمة مرور." |
| EN (multivolume) | "Multi-part archives aren't supported." | AR | "الأرشيفات متعددة الأجزاء غير مدعومة." |
| EN (unsafe) | "That archive contains unsafe entries." | AR | "يحتوي هذا الأرشيف على مدخلات غير آمنة." |
| `code` | `archive_encrypted` / `archive_multivolume` / `archive_unsafe_entry` |
| HTTP | 422 |
| Buttons | Back (primary) |

### 1.22 429 — write-rate exceeded

| Field | Value |
| --- | --- |
| `code` | `write_rate_exceeded` |
| HTTP | 429 |
| EN | "You're writing too fast. Please try again shortly." |
| AR | "أنت تكتب بسرعة كبيرة. حاول مرة أخرى بعد قليل." |
| Buttons | Retry (primary), Back (primary) |

### 1.23 500 — internal

| Field | Value |
| --- | --- |
| `code` | `internal` |
| HTTP | 500 |
| EN | "Something went wrong. Please try again." |
| AR | "حدث خطأ ما. يرجى المحاولة مرة أخرى." |
| Buttons | Retry (primary), Back (primary) |

### 1.24 503 — unavailable (the sharpest edge)

| Field | Value |
| --- | --- |
| `code` | `unavailable` |
| HTTP | 503 |
| EN | "The sandbox is temporarily unavailable. Please try again." |
| AR | "البيئة المعزولة غير متاحة مؤقتًا. يرجى المحاولة مرة أخرى." |
| Notes | The engine's own `message` (e.g. "sandbox unavailable: nsjail could not set up...") names the **real** cause. It is logged and shown **only** in DEBUG+admin, collapsed. Users get the sentence above. |
| Buttons | 🔄 Retry (primary), Back (primary) |

Rendered (EN, normal user):

```html
<h2>⚙️ Service unavailable</h2>
<p>The sandbox is temporarily unavailable. Please try again.</p>
```
Rendered (AR):

```html
<h2>⚙️ الخدمة غير متاحة</h2>
<p>البيئة المعزولة غير متاحة مؤقتًا. يرجى المحاولة مرة أخرى.</p>
```
Appended **only** in DEBUG, admin-only, collapsed:

```html
<details><summary>🧪 Raw engine response (admin)</summary>
<pre><code class="language-json">{"error":{"code":"unavailable","message":"sandbox unavailable: nsjail could not set up or launch the sandbox","details":{"nsjail":"..."}}}</code></pre>
</details>
```

### 1.25 504 — tunnel URL unavailable

| Field | Value |
| --- | --- |
| `code` | `tunnel_url_unavailable` |
| HTTP | 504 |
| EN | "Couldn't publish a link. Please try again." |
| AR | "تعذّر نشر الرابط. يرجى المحاولة مرة أخرى." |
| Notes | Phase 2; includes Cloudflare rate-limiting. |
| Buttons | 🔄 Retry (primary), Back (primary) |

### 1.26 507 — insufficient storage

| Field | Value |
| --- | --- |
| `code` | `insufficient_storage` |
| HTTP | 507 |
| EN | "This workspace is out of disk space." |
| AR | "نفدت مساحة القرص في هذه المساحة." |
| Buttons | 📁 Manage files (primary), Back (primary) |

### 1.27 — `timeout` (defined, never emitted)

| Field | Value |
| --- | --- |
| `code` | `timeout` |
| HTTP | 504 |
| EN | "The request timed out. Please try again." |
| AR | "انتهت مهلة الطلب. يرجى المحاولة مرة أخرى." |
| Notes | Retained in the vocabulary; the adapter still parses it defensively. |
| Buttons | Retry (primary), Back (primary) |

---

## 2. Bot-internal/domain errors (not from the engine)

| Domain error | EN | AR | Buttons |
| --- | --- | --- | --- |
| `NotOwned` | "That item no longer exists." | "هذا العنصر لم يعد موجودًا." | Back |
| `WorkspaceMissing` | "That workspace no longer exists." | "لم تعد مساحة العمل موجودة." | Back |
| `InvalidInput(field)` | "Please check the value and try again." | "يرجى التحقق من القيمة والمحاولة مجددًا." | Retry, Back |
| `RateLimited` (bot throttle) | "You're going a bit fast — one moment." | "أنت سريع قليلًا — لحظة من فضلك." | Back |
| `EngineUnavailable(retryable=False)` | "The service is not configured correctly." | "الخدمة غير مهيأة بشكل صحيح." | Back |

---

## 3. `run.status` → heading + label

Complete enumeration of `Run.status` (SPEC §2.5, INTEGRATION-GUIDE §4.2). Every
value maps to an emoji + heading + short label, AR and EN.

| `run.status` | EN heading | EN short | AR heading | AR short |
| --- | --- | --- | --- | --- |
| `queued` | ⏳ Queued | Queued | ⏳ في الانتظار | في الانتظار |
| `running` | ⏳ Running | Running | ⏳ قيد التشغيل | قيد التشغيل |
| `exited` (exit 0) | ✅ Finished (exit 0) | Finished | ✅ انتهى بنجاح (الرمز ٠) | انتهى |
| `exited` (exit ≠ 0) | ❌ Failed (exit N) | Failed | ❌ فشل (الرمز N) | فشل |
| `killed` | ⏹ Stopped | Stopped | ⏹ أُوقف | أُوقف |
| `timeout` | ⏱️ Timed out | Timed out | ⏱️ انتهت المهلة | انتهت المهلة |
| `failed` | ⚠️ Could not start | Failed to start | ⚠️ تعذّر البدء | تعذّر البدء |
| `interrupted` | 🔌 Interrupted | Interrupted | 🔌 انقطع | انقطع |
| `oom` | 🧠 Out of memory | Out of memory | 🧠 الذاكرة غير كافية | الذاكرة غير كافية |
| `rate_limited` | 🐌 Write-rate limit | Rate limited | 🐌 بلوغ حد السرعة | بلغ حد السرعة |

Notes:
- `interrupted` means the server restarted mid-run (SPEC §2.5.2): the short label
  is "Interrupted / انقطع", with a tooltip line "The server was restarted."
  / "أُعيد تشغيل الخادم."
- `exit N` renders N as a locale number in Arabic (`الرمز ١`), but the underlying
  number is never digit-localized in `<code>` ids.
- A run's `timeout_sec` being `null` (with `unlimited:true`) never yields "0";
  the UI says "Unlimited / بلا حد" and shows elapsed, not a percentage.

---

## 4. `workspace.status` and `summary.state` → label

`workspace.status` (SPEC §2.2) is one of `active`, `suspended`, `locked`
(`deleted` exists in the type vocabulary but is never persisted). `summary.state`
is a derived, more expressive value: `active`, `expiring_soon`, `locked`,
`unlimited`.

| Value | Kind | EN | AR | Emoji |
| --- | --- | --- | --- | --- |
| `active` | status / state | Active | نشِطة | 🟢 |
| `suspended` | status | Paused | متوقفة مؤقتًا | 🟠 |
| `locked` | status / state | Locked | مقفلة | 🔴 |
| `unlimited` | state | No expiry | بلا انتهاء | 🔵 |
| `expiring_soon` | state | Expiring soon | تنتهي قريبًا | 🟡 |
| `deleted` | (never persisted) | Deleted | محذوفة | — |

Notes:
- Chat copy prefers `summary.state` when available (it distinguishes
  `expiring_soon` and `unlimited`), and falls back to `status`.
- `locked` banners carry the renewal message from §1.4.
- `expiring_soon` adds a line: "Your plan ends in {time}." / "تنتهي خطتك خلال {time}."
  (formatted via `format_duration`), where the threshold is the engine's
  `min(10% of term, 24h)`.

---

## 5. `session.status` → label (Phase 1.5)

| `session.status` | EN | AR | Emoji |
| --- | --- | --- | --- |
| `running` | Running | قيد التشغيل | 🟢 |
| `exited` | Exited | انتهت | ⚪ |
| `killed` | Closed | أُغلقت | ⏹ |
| `timed_out` | Timed out | انتهت المهلة | ⏱️ |
| `failed` | Could not start | تعذّر البدء | ⚠️ |

Session end `reason` values (shown as an italic line, never as the raw token):

| `reason` | EN | AR |
| --- | --- | --- |
| `workspace_stopped` | "The workspace was stopped." | "أُوقفت مساحة العمل." |
| `workspace_deleted` | "The workspace was deleted." | "حُذفت مساحة العمل." |
| `idle_timeout` | "Closed after inactivity." | "أُغلقت بعد فترة خمول." |
| `lifetime` | "Reached its maximum lifetime." | "بلغت مدتها القصوى." |
| `shell_exit` | "The shell exited." | "خرجت الصدفة." |
| (other) | "The terminal closed." | "أُغلقت الطرفية." |

A `failed` session's `diagnostic` is **never** shown to the user; it is logged and
shown collapsed in DEBUG+admin only. The user sees: "The terminal couldn't start."
/ "تعذّر بدء الطرفية."

---

## 6. `lifecycle` response fields → humanized

These are not errors but raw counts the UI must humanize (INTEGRATION-GUIDE §8.1):

| Field(s) | EN template | AR template |
| --- | --- | --- |
| `stopped_run_count` | "Stopped {n} run(s)." | "تم إيقاف {n} تشغيل." |
| `stopped_session_count` | "Closed {n} terminal(s)." | "تم إغلاق {n} طرفية." |
| `stopped_process_count` | "Stopped {n} background process(es)." | "تم إيقاف {n} عملية خلفية." |
| all zero | "Nothing was running." | "لا شيء كان يعمل." |
| `changed:false` | "Already done." | "تم بالفعل." |

Counts use the plural rules in
[`../architecture/06-i18n.md`](../architecture/06-i18n.md) §7 (Arabic six forms).

---

## 7. Resource figures → humanized honesty

| Engine field | EN | AR |
| --- | --- | --- |
| `disk_percent` | "{p}% used" | "{p}٪ مستخدَم" |
| `disk_complete:false` | "≥ {used} used" (lower bound) | "≥ {used} مستخدَم" (حد أدنى) |
| `ram_percent` | "{p}% of {limit}" | "{p}٪ من {limit}" |
| `ram_used_bytes:0` (no workload) | "0 B (idle)" | "٠ بايت (خامل)" |
| `truncated:true` (a list) | "⚠️ Some items are hidden." | "⚠️ بعض العناصر مخفية." |
| `output_truncated:true` | "⚠️ Older output was dropped." | "⚠️ تم حذف المخرجات الأقدم." |
| run `unlimited:true` | "Unlimited" | "بلا حد" |

---

## 8. The exhaustiveness test (why nothing leaks)

A unit test in `tests/unit/test_vocabulary.py`:

1. Enumerates **every** `code` from SPEC §2.8 and INTEGRATION-GUIDE §9.3.
2. Enumerates every `RunStatus`, `WorkspaceStatus`, `SessionStatus`, and every
   `summary.state`.
3. Asserts each has an entry here, and therefore a catalog key in **both** `en`
   and `ar`.
4. Renders each through `presentation.mapping` and asserts the output contains:
   - no engine token (the code string itself),
   - no `{` or `}` (JSON),
   - no HTTP status text,
   - no ASCII-only failure sentence when `locale=AR`.

If the engine adds a code in a future version and the bot has not mapped it, the
adapter maps it to a **safe generic** (`EngineRefused("unknown")` → "That request
couldn't be completed." / "تعذّر إكمال الطلب."), logs a warning naming the unknown
code, and the test fails in CI so a human adds a proper sentence. Nothing raw ever
reaches the user in the meantime.
