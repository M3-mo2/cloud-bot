# Rich Messages — The Only Output Path

This is a hard architectural contract, not a preference:

> **Every user-facing message the bot produces is a rich message**, sent through
> `Bot.send_rich_message(..., rich_message=InputRichMessage(html=...))` or edited
> through `Bot.edit_message_text(..., rich_message=InputRichMessage(...))`.
> `send_message(parse_mode=...)` is **never** used anywhere, for any reason.

Because this is absolute, all output goes through one module —
`presentation/rich/` — and handlers cannot produce output any other way.

---

## 1. Verified aiogram 3.31.0 API (measured, not assumed)

Introspected from the installed `aiogram==3.31.0`:

| Symbol | Status | Signature / fields |
| --- | --- | --- |
| `Bot.send_rich_message` | **exists** | `(chat_id, rich_message: InputRichMessage, ..., reply_markup: ReplyMarkupUnion \| None = None, ...) -> Message` |
| `Bot.send_rich_message_draft` | **exists** | `(chat_id, draft_id: int, rich_message, ..., can_stop, keep_on_stop) -> bool` |
| `Bot.edit_rich_message` | **does not exist** | — |
| `Bot.edit_message_text` | **exists, rich-capable** | `(text=None, ..., rich_message: InputRichMessage \| None = None, reply_markup: InlineKeyboardMarkup \| None = None, ...)` |
| `InputRichMessage` fields | — | `html`, `markdown`, `is_rtl`, `skip_entity_detection`, `blocks`, `media` |
| `ButtonStyle` | — | `primary`, `success`, `danger`, `link` |
| Structured blocks | — | `InputRichBlockTable`, `InputRichBlockDetails` (collapsible), `InputRichBlockPreformatted`, `InputRichBlockList`/`InputRichBlockListItem`, `InputRichBlockButtons`, `InputRichBlockBlockQuotation`, `InputRichBlockExpandableBlockQuotation`, `InputRichBlockPullQuotation`, `InputRichBlockDivider`, `InputRichBlockSectionHeading`, `InputRichBlockParagraph`, `InputRichBlockFooter`, and media blocks (photo/video/audio/animation/document/collage/slideshow/map) |
| `stopped_message_generation` | a field of `Update` | lets the user cancel a draft stream |

### 1.1 The `edit_rich_message` finding and our fallback

There is **no `edit_rich_message`** in aiogram 3.31.0. We need in-place updates
for live run logs, progress, and every menu navigation that reuses a message id.

**Fallback (chosen):** `Bot.edit_message_text(chat_id=..., message_id=...,
rich_message=InputRichMessage(html=..., is_rtl=...), reply_markup=...)`. The
parameter is accepted and documented as editing "text, rich and game messages",
so it is the supported rich edit path.

`RichSender.edit()` is the single wrapper:

```python
async def edit(self, chat_id, message_id, doc: RichDoc, *, markup=None) -> Message:
    return await self._bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        rich_message=doc.build(),          # InputRichMessage(html=..., is_rtl=...)
        reply_markup=markup,
    )
```

If a future aiogram release adds `edit_rich_message`, exactly this one method
changes — no screen, service, or handler is touched.

### 1.2 A required care point for edits

Telegram rejects an edit that does not change the message ("message is not
modified"). `RichSender` therefore caches the last-built HTML per `(chat_id,
message_id)` and short-circuits identical edits, and it never sends an empty
`rich_message`.

---

## 2. `InputRichMessage` construction

`RichDoc.build()` produces exactly one of `html`/`markdown`/`blocks`:

```python
def build(self) -> InputRichMessage:
    return InputRichMessage(
        html=self._render_html(),
        is_rtl=is_rtl(self._locale),        # True for Arabic
        # skip_entity_detection: left default; we control entities ourselves
    )
```

Rules:
- **`html`** is always the channel (we do not mix html and blocks in one message).
- **`is_rtl`** is derived from `Locale` — never passed per screen.
- **`skip_entity_detection`** stays default unless a screen embeds literal text
  that must not be auto-linked (rare; noted per-screen).

---

## 3. Supported elements and how we use them

From the aiogram rich-message guide and the Bot API rich formatting reference:

| Element | HTML | Where we use it |
| --- | --- | --- |
| Headings h1–h6 | `<h1>`…`<h6>` | Screen titles, section headers, section headings. |
| Paragraph | `<p>` | Body copy. |
| Bold / italic | `<b>`, `<i>` | Emphasis, labels. |
| Underline / strike | `<u>`, `<s>` | Rare; disabled explanations. |
| Inline code | `<code>` | Filenames, paths, identifiers, `ws_...` ids. |
| Code block | `<pre><code class="language-...">` | Run stdout/stderr, file content, JSON (DEBUG). |
| Unordered / ordered list | `<ul>/<ol><li>` | Options, steps. |
| Blockquote | `<blockquote>` | Notices, engine-reachability disclaimers. |
| Divider | `<hr>` | Between sections. |
| Table | `<table><tr><th><td>` | Workspace list, run history, usage, packages. |
| Collapsible | `<details><summary>…</summary>…` | Debug raw payloads, long stderr, help. |
| Media | `<img src>`, `tg://photo` | Later (avatars/screenshots); Phase 1 rarely. |
| LaTeX | `$…$`, `$$…$$` | Not used in Phase 1. |

We prefer **structured blocks** (`InputRichBlockTable`, `InputRichBlockDetails`,
`InputRichBlockPreformatted`) only if a screen already builds blocks; Phase 1
standardizes on **HTML strings** because they compose trivially and match the
`html=` field, and the table/details semantics are available as HTML tags.

---

## 4. Limits we design within (Bot API rich messages)

| Limit | Value | Our guard |
| --- | --- | --- |
| Text length | 32,768 UTF-8 chars | `RichDoc` tracks length; long output is chunked or collapsed. |
| Blocks | 500 (incl. nested) | Components keep documents small; large lists are paged. |
| Nesting | 16 levels | Table/details nesting kept shallow by design. |
| Media attachments | 50 | Phase 2 feature; counted if used. |
| Table columns | 20 | File table uses ≤5; usage ≤4. |

Consequences baked in:
- **Log output is bounded.** Tail loops cap accumulated output and show a
  "showing last N KB" note when trimmed; `truncated:true` from the engine is
  always surfaced.
- **Lists are paged**, never all-at-once, so we stay well under the block cap.
- **A "too long" helper** splits a single oversized code block at safe
  boundaries and puts later chunks in `<details>`.

---

## 5. Safety: escaping and injection

All user- and engine-derived values (workspace names, filenames, command output,
error details) are untrusted HTML input. `presentation/rich/tags.py` is the only
module allowed to assemble raw HTML:

```python
def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def code(text: str) -> str:      return f"<code>{esc(text)}</code>"
def pre(text: str, lang=None) -> str: ...
def path(text: str) -> str:      return code(text)   # paths are always code
```

Rules:
- Builders accept **already-escaped** fragment strings only where they are
  literals; any dynamic value passes through `esc`.
- A golden test feeds adversarial values (`<script>`, `</code>`, `&amp;`,
  RTL-override characters) through every component and asserts they render
  literally.
- **RTL override characters** (U+202E etc.) in filenames are neutralized so a
  malicious filename cannot visually reverse an Arabic sentence.

---

## 6. The sender — one door out

```python
class RichSender:
    async def send(self, chat_id, doc, *, markup=None, reply_to=None) -> Message: ...
    async def edit(self, chat_id, message_id, doc, *, markup=None) -> Message: ...
    async def draft(self, chat_id, draft_id, doc, *, can_stop=True,
                    keep_on_stop=False) -> bool: ...
    async def answer_callback(self, cb, *, text=None, alert=False) -> None: ...
```

- `send` → `send_rich_message`.
- `edit` → `edit_message_text(rich_message=...)` (the fallback).
- `draft` → `send_rich_message_draft` for live streaming; the final complete
  document is then `send`-ed to persist (drafts are ephemeral ~30 s).
- `answer_callback` is used for silent acknowledgements; it sends **no** message
  content (so it does not violate rich-only).

Nothing else in the codebase calls a `Bot.send_*` or `Bot.edit_*` method. This is
enforced by an architecture test that greps for `send_message`, `send_photo`,
`edit_message_text`, `send_rich_message` outside `presentation/rich/sender.py`.

---

## 7. Composing documents — examples

### 7.1 A workspace detail (English)

```
<h2>📦 demo</h2>
<p><b>Status:</b> Active</p>
<table>
  <tr><th>Runtime</th><td>python3</td></tr>
  <tr><th>CPU</th><td>1 core</td></tr>
  <tr><th>Memory</th><td>512 MB</td></tr>
  <tr><th>Disk</th><td>2.3 MB / 256 MB (1%)</td></tr>
  <tr><th>Runs</th><td>3 (1 active)</td></tr>
  <tr><th>Created</th><td>3 Nov 2026, 20:41</td></tr>
</table>
<hr>
<p><code>ws_01M6HJASCWM9Z1PF755GT89NK4</code></p>
```

### 7.2 The same screen (Arabic, `is_rtl=True`)

```
<h2>📦 demo</h2>
<p><b>الحالة:</b> نشِطة</p>
<table>
  <tr><th>بيئة التشغيل</th><td>python3</td></tr>
  <tr><th>المعالج</th><td>نواة واحدة</td></tr>
  <tr><th>الذاكرة</th><td>512 ميغابايت</td></tr>
  <tr><th>القرص</th><td>2.3 ميغابايت / 256 ميغابايت (1%)</td></tr>
  <tr><th>التشغيلات</th><td>3 (1 نشِط)</td></tr>
  <tr><th>أُنشئت</th><td>3 نوفمبر 2026، 20:41</td></tr>
</table>
<hr>
<p><code>ws_01M6HJASCWM9Z1PF755GT89NK4</code></p>
```

Note the id stays ASCII in `<code>`, the heading stays LTR-marked by being a
heading, and the table labels come from the catalog.

### 7.3 Run output (code block + collapsible stderr)

```
<h3>▶️ Finished (exit 0)</h3>
<pre><code class="language-text">hello from main.py
args: ['x', 'y']
</code></pre>
<details><summary>stderr (empty)</summary><pre><code></code></pre></details>
<p><i>12 ms</i></p>
```

---

## 8. Testing rich rendering

- **Golden snapshots** per screen × locale (AR asserts `is_rtl=True`).
- **Escaping tests** with adversarial input (see §5).
- **Limit tests**: 33 KB output → chunked/collapsed; 600 items → paged.
- **Architecture test**: no send/edit call outside the sender.
- **No-raw test**: no rendered document (outside DEBUG-admin) contains an engine
  code token, JSON braces, or the engine's English `message`.
