# Developer tools (`/dev`)

An **admin-only**, in-bot tool that shows a section's real, live buttons and
proves they actually work. It exists to close the gap this project kept falling
into: *"the builder is fine"* vs *"the real button works"* (`style="link"`, the
callback registry, `term:resize` with no handler, the 30 s terminal stall). Every
one of those lived between a screen that rendered and a button that did not.

## The switch

The tool is **off by default** and has two gates:

1. **The identity gate** — `Settings.is_admin(tg_id)` (`CLOUDBOT_ADMIN_IDS`). The
   router is only attached when the switch is on, and even then
   `middleware/admin_gate.py` swallows the `/dev` command and every `dev:`
   callback for a non-admin, so its existence is never revealed.
2. **The explicit switch** — `CLOUDBOT_DEV_TOOLS=true`. The gate checks this
   *before* the identity check, so with the switch off the tool is invisible to
   everyone, admins included. It is documented in `.env.example`.

Keep it **off in production**. A tool that can render every screen and tap every
button is a footgun if it is ever reachable by a non-admin.

## What it does

* **Sections.** Every section of the screen catalog with a count of screens and
  buttons.
* **A section's screens.** Every screen in the section.
* **One screen, inspected.** The screen rendered exactly as the user sees it, and
  a **live button table**: for each button — index, text, `callback_data` (a URL
  is shown redacted), `style`, `disabled` — and its **verdict**:

  | Verdict | Meaning |
  | --- | --- |
  | `✅ handled` | the callback parses and a handler in the **real dispatcher** claims it |
  | `❌ no handler` | nothing claims it — a dead button |
  | `⚠️ stale` | it parses but needs key parts the payload no longer carries (the restart case) |
  | `❌ unparseable` | malformed callback data |
  | `🔗 link` | a URL button (no callback to run) |

  Each button has a **▶️ tap** control. A tap dispatches that callback through a
  **real dispatcher** and shows the resulting screen — so you see the real next
  screen, not a guess. **▶️ Run all checks** walks every screen in the section, and
  **the whole catalog**, and reports the totals plus every finding.

## The three hard guarantees

1. **Nothing reaches Telegram.** A tap drives a real dispatcher whose bot is the
   shared `FakeBot` double (`handlers/dev_sandbox.py`, also used by the contract
   tests). Only the tool's own summary screens are sent to the admin's chat, by
   the handler itself.
2. **Nothing reaches the engine.** The sandbox forces the `FakeEngine` and a
   `:memory:` database regardless of the production settings, so no HTTP client is
   ever built.
3. **Read-only.** The sandbox has its own in-memory world; the tool cannot mutate
   the live database, files, terminals or tunnels.

A screen that raises while rendering or tapping is **caught, reported as a
finding for that button, and the walk continues** — the tool never crashes the
handler.

## The screen catalog

Screens live in the **product**, not in a test:
`cloudbot/presentation/catalog.py` holds `SCREEN_BUILDERS` (name → builder), a
first-class `Section` grouping, the fixed fixtures the builders need, and the walk
that classifies every button.

* `sections()` — the ordered sections.
* `screens_in(section)` — a section's screens.
* `build(name, locale)` — render one screen.
* `walk_screen` / `walk_section` / `walk_catalog` — the verdict walk.

The golden test, the button contract tests, the keyboard tests and the `/dev`
tool all **consume** this one catalog, so they cannot drift. The verdict logic is
the **one** implementation shared by the tool and the contract tests.

### Adding a screen

1. Add a builder to `SCREEN_BUILDERS` in `cloudbot/presentation/catalog.py`.
2. Its section is derived from the name (`_section_of`); if the name does not fit
   any rule, add a clause there (or register with an explicit section).
3. Run `UPDATE_GOLDEN=1 python -m pytest tests/golden` to write its goldens.

That is the whole checklist. The new screen is then automatically covered by the
goldens, by the button walk, and by `/dev` — which is the extensibility the tool
was built for.
