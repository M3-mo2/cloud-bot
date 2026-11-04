# Keyboards and Buttons

The bot uses the full aiogram 3.31.0 button feature set — colored styles, disabled
buttons, forced reply, inline and reply keyboards, and link-style buttons — under
one uniform convention so every screen feels the same. Screens declare an
**intent**, never a color; a single module maps intent → style.

---

## 1. Verified capabilities (aiogram 3.31.0)

| Object | Field | Allowed | Notes |
| --- | --- | --- | --- |
| `InlineKeyboardButton` | `style` | `primary`, `success`, `danger` | Blue / green / red (Bot API 9.4). |
| `InlineKeyboardButton` | `style` | `link` | Renders as a borderless link. |
| `InlineKeyboardButton` | `disabled` | `DisabledButton()` | Shown but inert. |
| `InlineKeyboardButton` | `url` | str | External link target. |
| `KeyboardButton` | `style` | `primary`, `success`, `danger` | Reply-keyboard coloring. |
| `InlineKeyboardMarkup` | `force_reply` | bool | Shows the reply interface. |
| `ReplyKeyboardMarkup` | `force_reply` | bool | Shows the reply interface. |
| `Bot.send_rich_message` | `reply_markup` | `ReplyMarkupUnion` | **Rich send can carry any markup** (inline, reply, remove, force-reply). |
| `Bot.edit_message_text` | `reply_markup` | `InlineKeyboardMarkup \| None` | **Edits can only change inline keyboards.** |

### The one important constraint this creates

- **Navigation and in-place updates** always use **inline keyboards**, because
  edits can only update inline markups — and edits are how we avoid message spam.
- **The persistent global menu** is a **reply keyboard**, attached when a message
  is *sent*; changing or removing it requires a new sent rich message carrying the
  new `ReplyKeyboardMarkup` (or `ReplyKeyboardRemove`) as `reply_markup`. That is
  allowed because `send_rich_message` accepts the full `ReplyMarkupUnion`.

---

## 2. The intent → style convention

```python
# presentation/keyboards/styles.py
class Intent(str, Enum):
    PRIMARY  = ButtonStyle.PRIMARY   # blue  — navigate / neutral / default
    SUCCESS  = ButtonStyle.SUCCESS   # green — forward / create / confirm / resume
    DANGER   = ButtonStyle.DANGER    # red   — delete / stop / destructive
    LINK     = ButtonStyle.LINK      # link  — external URL
    MUTED    = "muted"               # -> disabled=True (shown, inert)
```

| Intent | Meaning | Examples | Color |
| --- | --- | --- | --- |
| `PRIMARY` | Navigate, open, refresh, go back, neutral | Menu, Back, Open, Files, Refresh, Settings, Suspend | Blue |
| `SUCCESS` | Positive / forward / create / confirm | Create, Run, Resume, Upload, Save, Confirm, Yes | Green |
| `DANGER` | Destructive or stopping | Delete, Stop, Stop all, Purge | Red |
| `LINK` | External URL | Docs, tunnel URL, Cloudflare URL | Link |
| `MUTED` | Unavailable / in progress | "Run" while already running, empty-state placeholders | Disabled |

Rationale (recorded in `DECISIONS.md`):
- **Suspend = PRIMARY**, not danger: it is reversible and keeps all data; it
  pauses, it does not destroy.
- **Delete and Stop = DANGER**: irreversible / kills running work.
- **Resume = SUCCESS**: restores service.
- **LINK** is used only for real external URLs; internal navigation is never a
  link.

---

## 3. Button types and where each belongs

| Type | Where used | Why |
| --- | --- | --- |
| **Inline, callback** | All in-message actions, navigation, pagination, per-item actions, confirmations | Enables in-place `edit`, keeps chat clean, carries callback data. |
| **Inline, url + `LINK`** | External links (docs, published tunnel URL) | Opens directly, styled as a link. |
| **Inline, `disabled=True`** | Actions not currently valid | Shows the affordance without offering a dead end. |
| **Inline, `force_reply=True`** (markup) | Short input prompts | Focuses the reply box on the prompt. |
| **Reply keyboard** | Persistent global menu only | Always-available navigation, no scrolling to a message. |
| **Reply, `force_reply=True`** (markup) | Prompts where a reply keyboard is active | Same as inline force_reply but coexists with the menu. |

**Never** put the global menu in an inline keyboard, and **never** put per-item
actions in a reply keyboard. One rule, applied everywhere.

---

## 4. The global reply menu (persistent)

Shown after onboarding and reachable anytime. Reply keyboard, `resize_keyboard=True`,
`is_persistent=True`.

```
┌─────────────────────────────┐
│  📦 المساحات   📦 Workspaces │   primary / primary
│  ⚙️ الإعدادات  ⚙️ Settings   │   primary / primary
│  🌐 اللغة      🌐 Language    │   primary / primary
│  ❓ مساعدة     ❓ Help        │   primary / primary
└─────────────────────────────┘
```

In code:

```python
ReplyKeyboardMarkup(
    keyboard=[
        [reply("menu.workspaces", intent=PRIMARY), reply("menu.settings", intent=PRIMARY)],
        [reply("menu.language",   intent=PRIMARY), reply("menu.help",     intent=PRIMARY)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder=catalog.t("menu.placeholder", locale),
)
```

There is no `disabled` for reply buttons, so unavailable menu entries are simply
omitted.

---

## 5. Standard inline patterns (reused across screens)

### 5.1 Navigation row

```
[ ⬅️ رجوع | ⬅️ Back ]  (primary)     [ 🏠 القائمة | 🏠 Menu ] (primary)
```

### 5.2 Confirm / cancel (destructive)

```
[ ✅ حذف | ✅ Delete ] (danger)      [ ✖️ إلغاء | ✖️ Cancel ] (primary)
```

### 5.3 Confirm / cancel (positive)

```
[ ✅ إنشاء | ✅ Create ] (success)   [ ✖️ إلغاء | ✖️ Cancel ] (primary)
```

### 5.4 Pagination

```
[ ⬅️ السابق ] (primary)  [ ٢ / ٥ ] (disabled)  [ التالي ➡️ ] (primary)
```

The page indicator is a `disabled=True` button used purely as a label — one of the
new-feature showcases.

### 5.5 Per-item row (list screens)

```
[ 📦 demo ] (primary)   [ 🗑 ] (danger, icon-only delete)
```

### 5.6 External link

```
[ 📖 التوثيق | 📖 Docs ] (link)
```

---

## 6. `force_reply` prompts

When the bot needs a short typed value (workspace name, file path, command, file
content), it sends a prompt rich message whose markup sets `force_reply=True`, so
Telegram focuses the reply box on that message.

```python
inline_kb = InlineKeyboardMarkup(inline_keyboard=[[
    inline("common.cancel", "nav:cancel", intent=PRIMARY),
]], force_reply=True)

await sender.send(chat_id, screen.prompt("ws.ask_name", locale), markup=inline_kb)
```

Because `edit_message_text` cannot carry `force_reply`, prompts are **sent**, not
edited.

---

## 7. `disabled=True` usage

- **Pagination current-page indicator** (a label, §5.4).
- **An action that is already happening**: "Run" while a run is active shows
  `MUTED` (disabled) so the user sees why.
- **Empty states**: e.g. "No files" as a disabled button inside an otherwise
  actionable keyboard is avoided; instead the body text says it and the button is
  omitted. Disabled is used only where the affordance should remain visible.

`disabled=True` sets `disabled=DisabledButton()` on `InlineKeyboardButton`.

---

## 8. Callback data — the 64-byte budget

Telegram caps `callback_data` at **64 bytes**. A workspace id (`ws_` + 26-char
ULID ≈ 29 bytes) plus a run id pushes a naive `run:logs:<ws>:<run>:stdout:0`
past the limit. We therefore **do not embed full entity ids in callbacks**.

**Strategy:** keep the user's *current context* in FSM storage (current workspace,
run, path, page, stream, offset) and let callbacks carry a compact action plus a
short token:

```
callback_data = f"{action}:{token}:{extra}"     # e.g. "run:logs:7:out"
```

- `token` is a small integer indexing the user's context stack.
- `handlers/callbacks/codec.py` encodes/decodes and **asserts ≤ 64 bytes at build
  time** (raises in DEV/DEBUG, logs + truncates safely in PRODUCTION).

Action namespaces:

| Prefix | Meaning |
| --- | --- |
| `nav:` | back, menu, cancel, page |
| `ws:` | workspace actions (open, create, suspend, resume, stop, delete) |
| `file:` | file actions (ls, view, mkdir, delete, upload, cd) |
| `run:` | run actions (exec, start, logs, stop, refresh, purge) |
| `lang:` | language selection |
| `cfg:` | settings |

---

## 9. Consistency rules (enforced by review + tests)

1. Colors come **only** from `Intent`; a screen never names a `ButtonStyle`.
2. Every destructive action is `DANGER` **and** requires an explicit confirm.
3. Every screen has a **Back** (primary) and the main menu is always one tap away
   via the reply keyboard.
4. Button text always comes from the catalog, in the user's locale — never a
   hardcoded English string.
5. Icons are part of the catalog string (`"🗑 حذف"` / `"🗑 Delete"`), so both
   languages carry the same icon.
6. Pagination uses the disabled-label pattern; no bespoke pager per screen.
7. A golden test renders every screen's keyboard for AR and EN and asserts:
   styles match the intent table, every `DANGER` button is behind a confirm, and
   all `callback_data` ≤ 64 bytes.

---

## 10. Quick reference

| Screen element | Type | Intent | Flags |
| --- | --- | --- | --- |
| Menu → Workspaces | reply | primary | — |
| Back / Menu | inline | primary | — |
| Open workspace | inline | primary | — |
| Create workspace | inline | success | — |
| Run / Exec | inline | success | `MUTED` if running |
| Upload | inline | success | — |
| Suspend | inline | primary | — |
| Resume | inline | success | — |
| Stop (kill all) | inline | danger | confirm |
| Delete workspace | inline | danger | confirm |
| Purge runs | inline | danger | confirm |
| Page indicator | inline | — | `disabled=True` |
| Docs / tunnel URL | inline (url) | link | — |
| Input prompt (name/path/command) | inline/reply markup | — | `force_reply=True` |
