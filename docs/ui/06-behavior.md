# UI 06 — Behavior

Interaction rules that make the bot feel calm and consistent: streaming,
progress, busy states, dialogs, long operations, and the small courtesies. These
are cross-cutting; specific screens live in UI 01–04.

---

## 1. One message, edited in place

The bot is **edit-first**, send-second:

- Tapping a callback that changes the screen **edits the existing message**
  (`RichSender.edit`) rather than sending a new one. The chat stays clean and the
  history reads like navigation, not a log.
- A new rich message is sent only when:
  - a **flow starts** (a prompt that needs `force_reply`, which edits cannot
    carry),
  - a **result should be kept** (a run's final output, an exec result),
  - a legacy user is migrated off the old reply keyboard (the one-time
    `ReplyKeyboardRemove` on `/start`; see [`01-language-and-menu.md`](01-language-and-menu.md) §7),
  - the previous message is too old or was deleted (fallback).
- Identical edits are suppressed (`RichSender` caches the last HTML per message),
  because Telegram rejects "message not modified" edits.

---

## 2. Streaming and drafts

Live run logs use the engine's offset protocol and the Bot API's rich draft
streaming:

1. **Start a draft** with `send_rich_message_draft(chat_id, draft_id, doc,
   can_stop=True)`. Content animates as it updates, and the user gets a stop
   button.
2. On each poll tick (default **1.5 s**, backoff to 3 s on slow runs), rebuild the
   document and stream it again with the same `draft_id`.
3. When the run reaches a terminal status (or `eof` and not running), **persist**
   the final complete document with `send_rich_message` — drafts are ephemeral
   (~30 s) and must be finalized.
4. If the user presses the draft's stop button, a `stopped_message_generation`
   update arrives; the tail loop cancels and the last full document is persisted
   with a note ("Live view stopped." / "تم إيقاف العرض المباشر."). The **run is
   not stopped** by this — stopping the run is always the explicit **⏹ Stop**
   action.

Drafts are used **only** for live logs; ordinary navigation never drafts.

---

## 3. Progress

Where the engine exposes enough information, the bot shows real progress, never a
fake spinner:

| Operation | Progress shown | Source |
| --- | --- | --- |
| Run with `timeout_sec` | `progress {p}% · {elapsed} / {limit}` | elapsed vs `timeout_sec` |
| Unlimited run (`timeout_sec:null`) | `running · {elapsed}` (no %) | elapsed only |
| Upload | byte progress bar | local stream position vs plan cap |
| Package install (Phase 2) | "installing…" + live `output` | install response stream |

Elapsed and remaining times are formatted per locale
([`../architecture/06-i18n.md`](../architecture/06-i18n.md) §6). A progress bar is
rendered as a `[████░░░░] 50%` line inside `<code>` (visual, language-neutral).

---

## 4. Busy states

- **A run is active** → the workspace's **Run** button becomes `MUTED`
  (`disabled=True`) with a caption "A run is already in progress." /
  "هناك تشغيل جارٍ بالفعل."
- **An action is in flight** → the tapped button is replaced (in the same edit)
  by a `disabled=True` "⏳ Working…" / "⏳ جارٍ التنفيذ…" label so double-taps do
  nothing and the user sees the work is happening.
- **Long engine calls** (create, suspend, delete) acknowledge the callback
  immediately and show the busy state before the network call; the result replaces
  it.
- **Uploads** show the progress screen (§3) and a **Cancel** button that aborts
  the stream.

---

## 5. Confirmations and dangerous actions

- **Every** destructive action confirms on its own screen, and the confirm button
  is `DANGER` ([`../rendering/keyboards.md`](../rendering/keyboards.md) §9).
- The confirmation states the **consequence** in plain words ("All files inside it
  will be removed."), not just "Are you sure?".
- Suspend confirms but its button is `PRIMARY` (reversible; data kept).
- Delete workspace requires tapping **Delete permanently**; there is no
  double-confirm (the screen itself is the confirmation) and no accidental
  long-press path.

---

## 6. Answering callbacks

- Navigation callbacks are acknowledged silently (`answer_callback` with no text)
  and the screen is edited.
- When an action cannot proceed, the callback is acknowledged with a short toast
  (`answer_callback(text=..., alert=False)`) **and** the screen carries the full
  explanation. The toast is a hint, never the only feedback.
- Only genuinely blocking problems use `alert=True` (a modal), and sparingly.

---

## 7. Throttling and pacing

- Per-user token bucket (PRODUCTION): normal tapping is unaffected; a rapid burst
  is gently refused with "You're going a bit fast — one moment." and no engine
  call.
- The engine has **no rate limiting**, so the bot is the only guard against a user
  hammering it; the throttle is real, not decorative.
- **Log polling** is bounded: increasing interval for long runs, stop at terminal
  status, and stop when the user navigates away (the stream is tied to the message
  it streams into).

---

## 8. Long output

- **Code/output blocks** are capped (~12 KB shown in a message); the newest part
  is kept and a note says so. Earlier content is reachable in run history via line
  ranges.
- **Lists** page (10–20 rows) with a **Load more** button and a page indicator
  (a `disabled` label).
- A single oversized value (a huge filename list, a giant line) is split at safe
  boundaries; later chunks go into a collapsed `<details>` block.
- The engine's own truncation flags (`truncated`, `disk_complete:false`,
  `output_truncated`) are always surfaced as honesty notes
  ([`05-errors-and-status.md`](05-errors-and-status.md) §7).

---

## 9. Error surfacing (never raw)

- All failures render through `presentation.mapping` → a localized sentence +
  appropriate action buttons ([`05-errors-and-status.md`](05-errors-and-status.md)).
- The raw engine `message`/payload is logged always, and shown to **admins in
  DEBUG only**, collapsed. It never appears in PRODUCTION or DEV, for anyone.
- A failure **edits** the current screen into the error screen (with Back/Retry),
  so the user is never stranded or left with a stale button.

---

## 10. Timers and expiry awareness

- Screens that can go stale (a run's live view, a workspace detail) carry a
  relative "updated X ago" line, refreshed on each edit.
- A workspace `expiring_soon` shows a reminder line; `locked` shows the renewal
  banner ([`05-errors-and-status.md`](05-errors-and-status.md) §4).
- Session idle/lifetime timeouts (Phase 1.5) are surfaced before they happen
  ("This terminal closes after 15 minutes idle.") rather than only after it closes.

---

## 11. Locale behavior in flows

- Switching language mid-flow re-renders the **current** message in the new
  locale (same message id) and persists the choice.
- Prompts (which are sent, not edited) are re-sent in the new locale if the user
  switches before answering; the FSM stores only state and raw user input, never
  rendered text.
- There is no persistent reply menu to re-send: the inline nav block is part of
  each screen and is edited in place with it.

---

## 12. Accessibility and clarity

- Buttons carry both an icon and a word; an icon is never the only label for a
  consequential action.
- The primary action on every screen is visually distinct (the `success`/`primary`
  it earned by intent), so the "obvious next step" is obvious in both languages.
- Copy avoids jargon: "run", "file", "folder", "pause", "delete" — the engine's
  vocabulary (`exec`, `session`, `tunnel`, `oom`) never reaches a user except in
  the DEBUG admin block.

---

## 13. Summary of the interaction contract

| Rule | Guarantee |
| --- | --- |
| Edit-first | Chat stays clean; navigation replaces content. |
| Send-only-when-needed | Prompts, kept results, reply-keyboard changes. |
| Draft streaming | Live logs animate without spamming. |
| Real progress | Percent only when the engine supports it. |
| Busy states | Disabled buttons while work is in flight. |
| Confirm destructive | Danger button on its own screen. |
| No raw output | Human sentences only; raw collapsed in DEBUG+admin. |
| Honest truncation | Every dropped byte is disclosed. |

---

## 14. Tunnels / ports — every refusal explains itself (task 15)

Publishing a public link is the sharpest path in the bot: a tunnel create is slow
(cloudflared waits up to `tunnels.url_timeout_sec`), it is capped per workspace
(`tunnels.max_per_workspace`), and a link only works once something is LISTENING
on the port. The rules that keep it honest:

- **No silent branch.** Every engine refusal on the publish path maps to a
  rendered sentence. `too_many_tunnels` renders a dedicated *Link limit reached*
  screen that names the **actual** limit and offers **Remove a link** (the
  list/removal path); `tunnel_url_unavailable` renders a *Couldn't get a link*
  screen that explains the usual cause (no egress / `allow_net`) and offers
  **Retry {port}** on the same port; `tunnels_disabled` re-renders the tunnels
  screen with the off switch named. An **unmapped** code is re-raised to the
  error screen — it is never swallowed into a redraw.
- **The ceiling is reported, never hardcoded.** The tunnels screen shows
  «الروابط المنشورة: N من M» / "Links published: N of M", where M is
  `tunnels.max_per_workspace` read from `/v1/meta`. At the cap the publish entry
  is a **disabled** button whose own label says the limit was reached — a
  button-shaped no-op reads better than an action that silently fails.
- **A non-listening port is a warning, not a block.** The publish screen lists
  the detected ports as one-tap buttons; a typed port that is **not** detected
  first shows a confirm screen carrying one sentence — the link will only work
  once something listens on that port. Starting a server *after* publishing is
  legitimate, so it is never refused. When no port is detected at all, the screen
  says so and adds one line naming what to do (start the server, or type its
  port).
- **Never a dead end.** The list, the publish picker, the confirm, the refusal
  and the timeout screens each keep a back path (`⬅️ Back`), and the tunnels
  list always returns to the workspace home.
- **Still ephemeral.** The URL continues to appear only as a `LINK` button on the
  live screen; it is never persisted and never rendered as text.

