# Devtools — the architecture doctor and the bug reproducer

Two offline instruments that find problems the test suite does not, and shorten
the first hour of a bug fix. They read the source tree and the screen catalogue;
they never start a bot, build a dispatcher or open a socket (a test asserts this).

```bash
python -m cloudbot.devtools.doctor            # human table, exit 1 on any error
python -m cloudbot.devtools.doctor --json     # the same report, machine-readable
python -m cloudbot.devtools.reproduce "the tunnel list shows no URL"
```

Both are also reachable from the in-bot `/dev` screen: **🩺 Run the architecture
doctor** runs the doctor and renders the findings grouped by severity (paged with
the shared pager), reusing the same job mechanism as *run all checks*.

---

## `doctor` — what it checks

Severity is `error` (exit code 1 — a pre-commit gate), `warning` or `info`.

| # | Check | What it finds |
| --- | --- | --- |
| 1 | `layer.import_direction` | `presentation` importing `adapters`/`infrastructure` (error) or `aiogram` (warning — see below). |
| 2 | `layer.handler_builds_screen` | a handler assembling `RichDoc`/`Screen` inline instead of calling a screen in `presentation/screens/`. |
| 3 | `layer.domain_impure` | `domain` importing `aiogram`/`httpx`/SQL, reading `os.environ`, or executing SQL. |
| 4 | `layer.hardcoded_text` | Arabic/English user-facing sentences outside the catalogs; engine reason codes interpolated into a screen. |
| 5 | `i18n.missing_key` | a key referenced in code that exists in neither catalog. |
| 6 | `i18n.key_set` | `ar` and `en` do not have identical key sets (both directions). |
| 7 | `i18n.dead_key` | a key defined in both catalogs but referenced nowhere. |
| 8 | `i18n.placeholder_drift` | a key whose `{placeholders}` differ between `ar`/`en`, or a caller passing a placeholder the string does not use. |
| 9 | `callbacks.no_handler` | a screen emits a callback no handler routes (the contract test's rule, single implementation). |
| 10 | `callbacks.unreachable_handler` | a `callback_data` prefix/route no screen ever emits (a dead route). |
| 11 | `callbacks.duplicate_destination` | two buttons on one screen going to the same callback. |
| 12 | `callbacks.label_conflict` | one destination wearing several static labels across the catalogue. |
| 13 | `design.row_width` | a keyboard row with more than two buttons. |
| 14 | `design.nav_rows` | a nav block longer than three rows. |
| 15 | `design.table_width` | a table wider than four columns. |
| 16 | `design.no_exit` / `design.no_pager` | a screen with no way out; a list that grows past a page with no pager. |
| 17 | `screens.orphan` | a screen in `SCREEN_BUILDERS` reachable from nowhere and used by no handler. |
| 18 | `design.emoji_collision` / `design.emoji_meaning` | two buttons sharing an emoji on one screen; one emoji meaning three or more things. |
| 19 | `screens.text_size` | the five largest rendered bodies (a warning near the 32 KB rich cap). |

**One implementation, two callers.** The button contract test, the pager audit
(`test_pagination`), the duplicate guard (`test_screens_b`) and the label guard
(`test_menu`) call the helpers in `cloudbot/devtools/screens.py`; the doctor calls
the same helpers. There is no second copy of any of these rules.

### What it deliberately does not do

* **No network, no Telegram, no engine.** It imports the presentation catalogue
  (to render screens) but never a bot or dispatcher. A test patches `socket.socket`
  to raise and still expects a full run.
* **It does not "fix" design calls.** A wide admin table, a nav block over three
  rows on the wallet and a three-button action row are **reported**, not rewritten:
  whether the screen or the rule is wrong is the maintainer's call.
* **`aiogram` in `presentation` is a warning, not an error.** The docs say
  `presentation` owns rich HTML and keyboards, but the only rich-message and
  keyboard types are aiogram's (`InputRichMessage`, `InlineKeyboardButton`) — a
  contradiction in the docs, reported rather than silently passed. Presentation
  importing `adapters` **is** an error.
* **Arabic outside the catalogue** is only checked for the script range and a
  self-script language label is exempted (`LOCALE_LABELS`).
* **A dynamically built key** (`f"plan.{code}"`) is credited to its literal
  prefix; the doctor cannot prove every runtime value resolves to a key.

---

## `reproduce` — what it does and its honest limits

```
python -m cloudbot.devtools.reproduce "publishing a tunnel shows no URL" [--path FILE] [--write FILE]
```

It is **keyword matching, not diagnosis**. It scores every screen against the
report's words using:

1. the **screen name** (`publishing` → `tunnel_publishing`),
2. the **button labels** on that screen,
3. the **callback prefixes** (`tnl:publish`),
4. the **catalogue entries** whose key or value contains the word, and then — the
   important part — only the screens that actually **render** that value,
5. an optional quoted phrase (matched verbatim),
6. an optional `--path` whose file stem resembles a screen.

It prints the matched needle and what it hit for every point, so a wrong guess is
**visibly** wrong instead of confidently wrong:

```
evidence (keyword → match):
  tunnel_publishing (10.8)
    screen: 'publishing' → 'tunnel_publishing'
    catalog: 'tunnel' → "tunnel.publish = '➕ Publish a port'"
    …
```

It then emits a stub test that renders the best-guess screen with the existing
fixtures and asserts the reported behaviour is absent, so it **fails now** and
turns green when the bug is fixed. When the report names a literal placeholder or
quotes on-screen text, the assertion is concrete (`'{days}' in html`); otherwise it
is a clearly marked sentinel (`return True  # replace with the exact symptom`) so
the stub always starts red.

**Limits, stated plainly:** it narrows the search to one screen and its buttons;
it does **not** know why the bug happens, which handler ran, or what the fix is.
Confirm the evidence before trusting the screen; replace the sentinel with the
concrete symptom. No model call, no network.

---

## Running the doctor on this tree

```bash
$ python -m cloudbot.devtools.doctor
doctor: 170 finding(s) — 16 error, 34 warning, 120 info
```

It exits non-zero because the 16 errors are real design-rule findings. They are
listed below, split into what was fixed in passing and what is left for a human.

### Fixed in passing (cheap and safe)

* **A missing key is a bug.** (None were missing by the catalog's own object; the
  six `count.*` reports were plural keys the checker's key-set did not include —
  the detector was corrected, not the catalogue.)
* **Placeholder drift** `ws.cpu.cores` in `ar` was missing `{n}` — but it is a
  deliberate singular ("one core", never a number). Left as a documented exception
  rather than printing "١ نواة"; the report explains it.
* **A user-facing string outside the catalogue**: the throttle's "going a bit fast"
  now uses `common.too_fast` (both locales).
* **A floating docstring** in `domain/ports/subscriptions.py` (after `...`) became
  the method's real docstring.
* **`locale.py`'s self-script labels** are exempted by a documented note, not
  flagged.

### Left for the maintainer to judge

* `design.row_width` on `admin_audit`, `admin_user`, `file_view`: three action
  buttons on one row. The rule says two; changing the screens is a product call.
* `design.nav_rows` on the wallet screens: four rows of two. The nav block grew
  when the wallet became a top-level destination; three rows would drop a
  destination.
* `design.table_width` on `port_list`: five columns. Trimming it drops information.
* `callbacks.unreachable_handler`: `term:refresh`, `term:resize`, `term:rsz*`,
  `file:more`, `ws:less`, `arc:result` are routed but never emitted by a screen.
  Several are compatibility routes for buttons drawn by an older build; deleting
  them needs a decision. `dev:` is emitted only by the dev tool outside the
  catalogue, so it is exempt by design.
* `layer.handler_builds_screen`: the admin panel and a few small handlers build
  their own screens. A warning, not an error — a candidate refactor, one screen
  module per report.
* `layer.import_direction` (aiogram, warning): see the documentation contradiction
  above.
* 53 `i18n.dead_key` notes: keys for features or copy that no longer has a caller.
  Removing them is safe but should be a deliberate sweep.
* 7 `screens.orphan` notes: screens reached only through the catalog, `/dev`, or
  the error-mapping helper (the `err_*` boards) rather than a named handler. They
  are not dead, but the report names them so the distinction is visible.
