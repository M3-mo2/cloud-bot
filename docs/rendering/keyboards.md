# Keyboards and Buttons

The bot uses the full aiogram 3.31.0 button feature set — colored styles, disabled
buttons, forced reply, inline keyboards, and link-style buttons — under one
uniform convention so every screen feels the same. Screens declare an **intent**,
never a color; a single module maps intent → style.

**There is no reply keyboard.** Every user-facing button is an inline button. The
one exception is a single `ReplyKeyboardRemove` sent on `/start` to migrate a
legacy user off the old reply menu (§10).

---

## 1. Verified capabilities (aiogram 3.31.0)

| Object | Field | Allowed | Notes |
| --- | --- | --- | --- |
| `InlineKeyboardButton` | `style` | `primary`, `success`, `danger` | Blue / green / red (Bot API 9.4). |
| `InlineKeyboardButton` | `style` | `link` | Renders as a borderless link. |
| `InlineKeyboardButton` | `disabled` | `DisabledButton()` | Shown but inert. |
| `InlineKeyboardButton` | `url` | str | External link target. |
| `InlineKeyboardMarkup` | `force_reply` | bool | Shows the reply interface. |
| `Bot.send_rich_message` | `reply_markup` | `ReplyMarkupUnion` | **Rich send can carry any markup** (inline, reply, remove, force-reply). |
| `Bot.edit_message_text` | `reply_markup` | `InlineKeyboardMarkup \| None` | **Edits can only change inline keyboards.** |

### The one important constraint this creates

- **Navigation and in-place updates** always use **inline keyboards**, because
  edits can only update inline markups — and edits are how we avoid message spam.
- Nothing is persistent. Navigation travels as an inline block at the bottom of
  every top-level screen (§5.1); the only reply markup ever sent is the one-time
  `ReplyKeyboardRemove` on `/start` (§10).

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
| **Inline, `disabled=True`** | Actions not currently valid (incl. the current nav destination) | Shows the affordance without offering a dead end. |
| **Inline, `force_reply=True`** (markup) | Short input prompts | Focuses the reply box on the prompt. |

There is **no reply keyboard** and no per-item reply actions. One rule, applied
everywhere: every user-facing button is inline.

---

## 3a. The shared bottom navigation block

One helper (`presentation/screens/nav.py`) renders the same nav block at the
bottom of every top-level screen: Workspaces (`ws:list`), Settings
(`nav:settings`), Language (`nav:language`), Help (`nav:help`), plus Menu
(`nav:menu`) where the screen is not the menu. **At most two buttons per row,
at most three rows**; a screen's primary action (e.g. `➕ New Workspace`) sits full
width above it. The destination the user is already on is `MUTED` (disabled) —
visible, not tappable.

```
[ 📦 المساحات ] [ ⚙️ الإعدادات ]
[ 🌐 اللغة ]     [ ❓ مساعدة ]
[ 🏠 القائمة ]                      ← only where the screen is not the menu
```

```python
from cloudbot.presentation.screens import nav

rows  = [[inline("menu.new_workspace", "ws:new", intent=SUCCESS)]]
rows += nav.nav_rows(locale, "menu")     # current surface
```

The old per-screen **Back** buttons remain on *inner* screens (files, runs,
settings forms); the nav block is the top-level pattern. The **workspace home**
(task 11) is a session home, not an inner page: it carries the shared nav block
and takes its single exit from the explicit log-out — no `⬅️ Back` row (see
`../ui/02-workspaces.md` §2.5.1).

---

## 4. (retired) The global reply menu

The bot used to carry a persistent reply keyboard (`ReplyKeyboardMarkup`, four
buttons 2×2). It is **gone**: navigation is the inline block in §3a. A legacy user
who still has the old keyboard installed is migrated off it by a single
`ReplyKeyboardRemove` sent on `/start` (§10 and `docs/DECISIONS.md` CB-26).

---

## 5. Standard inline patterns (reused across screens)

### 5.1 Navigation

Top-level screens use the shared bottom block (§3a). Inner screens keep a
**Back** (primary) row:

```
[ ⬅️ رجوع | ⬅️ Back ]  (primary)
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
- **The current nav destination** on a top-level screen (§3a): the screen you are
  on is shown muted, so tapping it is not offered.
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
3. Every top-level screen ends with the shared inline nav block (§3a); inner
   screens carry a **Back** (primary) button. There is no reply keyboard.
4. Button text always comes from the catalog, in the user's locale — never a
   hardcoded English string.
5. Icons are part of the catalog string (`"🗑 حذف"` / `"🗑 Delete"`), so both
   languages carry the same icon.
6. Pagination uses the disabled-label pattern; no bespoke pager per screen.
7. A golden test renders every screen's keyboard for AR and EN and asserts:
   styles match the intent table, every `DANGER` button is behind a confirm, and
   all `callback_data` ≤ 64 bytes.
8. An architecture test forbids `ReplyKeyboardMarkup`/`KeyboardButton` anywhere in
   `cloudbot/`; the only permitted reply markup is `remove_reply` (§10).

---

## 10. Quick reference

| Screen element | Type | Intent | Flags |
| --- | --- | --- | --- |
| Nav destination (Workspaces/Settings/Language/Help/Menu) | inline | primary | current one `MUTED` |
| Back (inner screens) | inline | primary | — |
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
| Input prompt (name/path/command) | inline markup | — | `force_reply=True` |
| Legacy reply-keyboard removal | `ReplyKeyboardRemove` | — | once, on `/start` only |

## 11. The one-time `ReplyKeyboardRemove`

A legacy user with the old persistent reply keyboard installed cannot have it
removed by an inline keyboard. On `/start`, and only there, a returning user gets
exactly one extra message with `remove_reply()` (an invisible zero-width body)
before the menu. It never displaces the screen's inline keys and never fires from
another command or callback. See `docs/DECISIONS.md` CB-26.
