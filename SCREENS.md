# SCREENS.md — the recipe for building a screen

Read this **instead of** exploring `rich/`, `keyboards/`, `i18n/`, `catalog.py` and three other
screens. It is the actual API of this codebase, with the decisions already made. `MOHAMED.md` is the
*taste*; this is the *method*.

If something here contradicts the code, the code wins — fix this file in the same commit.

---

## 1. What a screen is

```python
# cloudbot/presentation/screens/<name>.py
def <name>_screen(state..., locale: Locale, *, extra...) -> Screen:
    """Pure function. No I/O, no awaits, no reads of the clock you cannot inject."""
    doc = RichDoc(locale)
    ...
    return Screen(doc=doc, markup=<name>_markup(...))
```

`Screen` (`screens/screen.py`) is `doc` + `markup` (+ `send_new`). Screens are **pure**: same inputs →
byte-identical output, so goldens can compare them.

**Two functions per screen, always**: the screen (content) and its markup (buttons). Handlers call
both; tests call the screen.

---

## 2. The document API (`presentation/rich/document.py`)

Pick **one** of two modes per screen and never mix:

| mode | how | use when |
|---|---|---|
| **html** (legacy) | `doc.h()`, `doc.p()`, `doc.kv_table()` | buttons live only in the keyboard below |
| **blocks** | `doc.heading()`, `doc.paragraph()`, `doc.divider()`, `doc.buttons()` | buttons must sit **inside** the body |

`InputRichMessage` accepts **either** `html` **or** `blocks` — never both. A screen that calls
`doc.buttons()` therefore must build entirely in blocks. (`admin/user_page.py` is the reference.)

### Blocks mode (the one you want for anything item-shaped)

| call | result |
|---|---|
| `doc.heading("✦ الموارد ⟸ مساحة العمل", size=2)` | section heading, size 1–3 |
| `doc.paragraph(text)` | plain paragraph (escapes nothing — pass `Html`/`bold()` for markup) |
| `doc.paragraph_rich(x)` | same, accepting a rich-text object |
| `doc.divider()` | the `─` separator |
| `doc.buttons([(label, cb, style), ...], align=None)` | **one row of 1–8 in-body buttons** |
| `doc.raw_buttons([[dict, ...], ...])` | pre-built rows |
| `doc.footer(text)` | small print at the bottom |
| `doc.kv_block([(label, value), ...])` | stacked label/value lines (not a table) |
| `doc.alert(kind, title, body)` | callout |

`doc.buttons` takes `(label, callback_data, style)` and **raises** if `callback_data` > 64 bytes.

### html mode helpers still used by older screens

`doc.h(level, text)` · `doc.p()` · `doc.bold()` · `doc.code()` · `doc.pre()` · `doc.div()`
`doc.blockquote()` · `doc.ul()` · `doc.details()` · `doc.table()` · `doc.kv()` · `doc.kv_table()`
`doc.raw(html)` · `doc.prepend_raw()`

**`kv_table` renders a bordered two-column `<table>`.** A table is right for a small fixed
label/value set and wrong for anything with a percentage, a bar, or more than ~4 rows — see §6.

### Inline markup tags (`presentation/rich/tags.py`)

`esc()` · `safe()` · `fragment()` · `code()` · `path()` · `pre()` · `bold()` · `italic()` ·
`blockquote()` · `neutralize_bidi()`

Each returns an `Html` object. `_raw(text) -> Html` is the local convention for "already-escaped
string" at the top of a screen module.

---

## 3. Buttons

```python
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent

rows = [
    [inline("🔄 تحديث", _cb("met:view", ws_id), intent=Intent.PRIMARY),
     inline("⚙️ العمليات", _cb("proc:list", ws_id), intent=Intent.PRIMARY)],
    [inline("⬅️ رجوع", _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
]
return inline_markup(rows)
```

- `inline(text, action, *, intent=Intent.PRIMARY, disabled=False, url=None)`
- `Intent.PRIMARY` navigate · `SUCCESS` confirm/start · `DANGER` destructive · `MUTED` inert ·
  `LINK` external URL — **a URL button carries no style**, Telegram rejects the message otherwise.
- Callback builders live in `presentation/keyboards/callbacks.py` (`_cb`, `_term`, `_pkg`, …).
  **Reuse them**; do not hand-format callback strings.
- A destructive action gets **its own row**, never beside the habitual button.

---

## 4. Copy and numbers

```python
from cloudbot.presentation.i18n.catalog import catalog
catalog.t("metrics.title", locale)                        # plain
catalog.t("wallet.packs.balance", locale, stars="4")      # formatted
```

- Add keys to **both** `i18n/catalogs/ar.py` and `en.py`. A missing key returns the key itself — that
  is a visible bug, not a crash.
- **ASCII digits only**, always: use `localize_digits()`/`format_int()` from
  `presentation/i18n/formatters.py`, never a raw f-string number in Arabic copy.
- Formatters available: `format_bytes`, `format_bytes_pair`, `format_percent`, `format_ms`,
  `format_duration`, `format_relative`, `format_datetime`, `format_count` (plural-aware),
  `pluralize(key, n, locale)`.
- **Arabic plurals**: `pluralize`/`format_count` pick the right form for 0/1/2/3–10/11+. Do not
  hardcode «نجمة» next to a number.

---

## 5. House look — non-negotiable (`MOHAMED.md`)

```
▬▬▭▭▬▬▭▭▬▬▭▭                       ← doc.paragraph("...") at the top and under the title
✦ label ⟸ value                     ← doc.heading(size=2); the ⟸ only on a value that earns it
```

- Bold the important words and the dividers.
- **No table with 3+ columns** in RTL — numbers drift from their headers. One block per subject, or
  one sentence.
- Event order: **what happened → category → when**. Never the timestamp first.
- An item's buttons sit **beside that item, in the body**.
- When he says "fix it", he means **the words and their order**, not decoration.

---

## 6. Patterns that already exist — copy one, do not invent

| need | copy from |
|---|---|
| per-item block with its own in-body buttons | `screens/admin/user_page.py` |
| a list of N things, paginated | `presentation/pagination.py` (`window`, `pager_rows`, `footer`) |
| capacity bars with headroom tones | `screens/metrics.py` |
| product cards (price → what it buys) | `screens/wallet.py::packs_screen` |
| a screen that offers a fix to a failure | `screens/packages.py::missing_section` |
| a live view with offer rows on top | `screens/terminal.py::terminal_view_markup(extra_rows=...)` |

**Bars** — 20 cells, `█` filled / `░` empty, filled = round(percent/5). Never draw a bar from an
incomplete reading: an honesty flag (`*_complete == False`) means **no bar and no percentage**, only
the provisional marker. A bar from a lower bound is a confident lie.

---

## 7. Adding a screen (the checklist)

1. `screens/<name>.py` — `<name>_screen()` + `<name>_markup()`.
2. Handler in `handlers/<area>.py`; register the callback prefix where its siblings are registered.
3. **`presentation/catalog.py`** — one entry in `SCREEN_BUILDERS` with a fixture. This is what makes
   the golden test, the button walk and `/dev` cover it automatically. **A screen not in the catalog
   is not covered.**
4. Keys in `ar.py` **and** `en.py`.
5. `UPDATE_GOLDEN=1 python -m pytest tests/golden` to write the snapshots, then look at the diff.
6. `python scripts/map_flow.py` to regenerate the flow map.
7. Tests: the bite (fails on the old behaviour), the edge states, and the callback set (assert
   `callback_data`, never the label text).

---

## 8. Traps measured in this codebase

- `InputRichMessage` takes **html XOR blocks**. Mixing silently drops one.
- `<tg-button callback_data=...>` in **html** fails with `BUTTON_URL_INVALID` — in-body buttons only
  work in **blocks** mode via `InputRichBlockButtons` (`doc.buttons`).
- `callback_data` is capped at **64 bytes**; `doc.buttons` raises, `inline()` validates.
- **A URL button must not carry a style.**
- `doc.kv_table` produces a **bordered table** — fine for 2×4 label/value, wrong for anything visual.
- A missing catalog key renders the key string itself — always add both locales.
- `ReplyKeyboardMarkup` is banned in this product; inline or in-body only.
- Screens are pure: an injected `now` (not `datetime.now()` inside) is what makes goldens stable.
- The 3-column RTL table is the single most common review rejection. Do not.

---

## 9. Fast verification (do this, not the whole suite)

```bash
cd /home/zerops/projects/cloud-bot-real
.venv/bin/python -m pytest -q tests/unit/test_<your_area>.py     # your tests
UPDATE_GOLDEN=1 .venv/bin/python -m pytest -q tests/golden       # snapshots
.venv/bin/python scripts/map_flow.py                             # flow map
```

The full suite is slow; **the orchestrator runs it once at the end**. Report your area's tally and any
change in the golden screen/button counts — never claim a number you did not read.
