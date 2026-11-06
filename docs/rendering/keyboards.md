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
| **Inline, `disabled=True`** | Actions not currently valid | Shows the affordance without offering a dead end. Never the current nav destination (§3a, R1). |
| **Inline, `force_reply=True`** (markup) | Short input prompts | Focuses the reply box on the prompt. |

There is **no reply keyboard** and no per-item reply actions. One rule, applied
everywhere: every user-facing button is inline.

---

## 3a. The shared bottom navigation block

One helper (`presentation/screens/nav.py`) renders the same nav block at the
bottom of every top-level screen. Its content depends on **whether the user is
inside a workspace** (the helper's `inside` argument):

**R1 — a nav entry never points at the screen you are already on: the current
destination is omitted, not muted.** This is the task-14 rule and it is
implemented **inside `nav_rows`**, keyed off the `current` argument, so every
screen agrees. A disabled entry was the old (task-09) answer; it is wrong because
a disabled button is still a button-shaped thing that wastes a slot in the
two-column grid and reads as a real destination. `Intent.MUTED` remains for
buttons that legitimately exist elsewhere (e.g. `▶️ Run` while a run is active) —
this rule is about the nav block only.

**Not inside a workspace** — the block is the *account's*: Workspaces
(`ws:list`), Settings (`nav:settings`), Language (`nav:language`), Help
(`nav:help`), plus Menu (`nav:menu`) where the screen is not the menu. **At most
two buttons per row, at most three rows**; a screen's primary action (e.g.
`➕ New Workspace`) sits full width above it. The screen's own destination is
omitted (R1).

**R2 — the workspaces list also drops Menu** (`nav:menu`), because the menu's
body is the same workspace table: for a user who owns workspaces it is a second
route to the screen the list already is. The list block becomes the three account
destinations that are not this screen:

```
[ ⚙️ الإعدادات ] [ 🌐 اللغة ]
[ ❓ مساعدة ]
```

A screen such as Help therefore renders its destinations minus itself:

```
[ 📦 مساحاتي ] [ ⚙️ الإعدادات ]
[ 🌐 اللغة ]     [ ☰ القائمة ]
```

**Inside a workspace** (task 13) — the block is the *session's*, **not the
account's**: up to two entries, the workspace home (`ws:home`) and Workspaces
(`ws:list`, to switch), on one row. R1/R5 also apply here: **on the workspace home
itself `ws:home` is omitted** (you are already there), leaving just `📦 مساحاتي`.

```
[ 🏠 demo ] [ 📦 مساحاتي ]        ← on any inside screen except the home
[ 📦 مساحاتي ]                    ← on the workspace home itself
```

The **account destinations — Settings, Language, Help and Menu — are removed
from the block whenever `inside` is set.** A screen the user has *entered* should
not re-offer the account's own surfaces; that was the reported clutter (the
top-level menu appearing at the bottom of the workspace home). They stay
reachable exactly as before: the `/settings` and `/help` commands, and leaving
the workspace. This is the one place the account block disappears, and it is
deliberate — do not "restore" it while fixing a later bug.

```python
from cloudbot.presentation.screens import nav

rows  = [[inline("menu.new_workspace", "ws:new", intent=SUCCESS)]]
rows += nav.nav_rows(locale, "menu")            # not inside: account block
rows += nav.nav_rows(locale, "workspace_home", inside=ws)  # inside: session block
```

The old per-screen **Back** buttons remain on *inner* screens (files, runs,
settings forms); the nav block is the top-level pattern. The **workspace home**
(task 11) is a session home, not an inner page: it carries the shared nav block
and takes its single exit from the explicit log-out — no `⬅️ Back` row (see
`../ui/02-workspaces.md` §2.5.1–2.5.2).

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
[ ⬅️ السابق ] (primary)  [ 2 / 5 ] (disabled)  [ التالي ➡️ ] (primary)
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

> Deliberately **not** in this list: the current nav destination. Since task 14
> (R1) a nav entry that points at the screen you are already on is **omitted**, not
> muted (§3a). Muting it kept a button-shaped no-op in a grid slot and still read
> as a destination.

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
   screens carry a **Back** (primary) button. There is no reply keyboard. A nav
   entry never points at the screen you are on — the current destination is
   omitted, not muted (R1); the workspaces list also drops Menu (R2). While
   inside a workspace the block is the session's, and the account destinations
   are not offered there (§3a).
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
| Nav destination (Workspaces/Settings/Language/Help/Menu) | inline | primary | current one **omitted** (R1); Menu also omitted on the list (R2); **not inside** only |
| Session nav (🏠 home / 📦 My workspaces) | inline | primary | current one **omitted** (R1/R5); **inside** only, up to two entries |
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
| Page footer (`Page n of m`) | body italic | — | — |
| Pager previous/next | inline | primary | the arrow at an end is **omitted** (R1), never muted |
| Docs / tunnel URL | inline (url) | link | — |
| Input prompt (name/path/command) | inline markup | — | `force_reply=True` |
| Legacy reply-keyboard removal | `ReplyKeyboardRemove` | — | once, on `/start` only |

## 11. The one-time `ReplyKeyboardRemove`

A legacy user with the old persistent reply keyboard installed cannot have it
removed by an inline keyboard. On `/start`, and only there, a returning user gets
exactly one extra message with `remove_reply()` (an invisible zero-width body)
before the menu. It never displaces the screen's inline keys and never fires from
another command or callback. See `docs/DECISIONS.md` CB-26.
