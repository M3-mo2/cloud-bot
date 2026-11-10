# AGENTS.md — rules for automated agents working in this repository

This repository is **cloud-bot**: the operator-owned Telegram frontend for the headless sandbox
engine **Cloud Service** (`sandboxd`). Read `docs/README.md` first, then the document it tells you to
read for the area you are touching.

## Read this first

**`MOHAMED.md`** in this repo describes how the owner works: the language of a reply, the shape of
one, what "done" means, and the UI taste this product is held to. It is not optional context — read
it alongside this file.

**`SCREENS.md`** is the recipe for building a screen: the document API, buttons, catalog keys,
formatters, the house look, and the traps already measured here. Read it **instead of** exploring
`rich/`, `keyboards/`, `i18n/` and other screens — every decision it states was made against this
code, and it names the file to copy for each common pattern.

## Hard rules

1. **This repository is documentation-first.** Implement what the docs specify. Where a doc is
   silent, propose the smallest change that satisfies the surrounding contract — do not invent a
   convention.
2. **Precedence.** `docs-cloud-service/INTEGRATION-GUIDE.md` is the authoritative usage contract for
   the engine (v0.0.20, prefix `/v1`). If another document conflicts with it, the guide wins.
3. **The engine is a separate repository and is out of scope.** Never edit anything outside this
   repository. Links into the engine's source point at
   `https://github.com/M3-mo2/cloud-service`.
4. **One service key.** The bot holds a single Cloud Service bearer key and enforces user isolation
   itself (`tg_id -> owned ws_…`) in the domain layer, on every workspace-scoped call. End users
   never hold a key and never reach the engine directly.
5. **Rich-only output.** Every user-facing message goes through the rich-message API. Never call
   `send_message(parse_mode=…)`.
6. **No raw engine output.** No JSON and no raw English engine message may reach a normal user; map
   every code and status through the presentation layer. DEBUG is for admins only.
7. **Never print, log, commit or echo a secret** — no bot token, no engine key, no `Bearer` value.
8. **Commit locally; never push, and never search for credentials.** The operator handles pushing.
9. **Do not start a Telegram long-poller here.** Only one process may poll a given bot token, and it
   is not necessarily you.

## Style

- Python 3.12+, `aiogram==3.31.0`, `httpx`, `pydantic`/`pydantic-settings`, `aiosqlite`, `babel`,
  `structlog`. Entry point: `python -m cloudbot`.
- Bilingual by construction: an **en** and an **ar** catalog, both complete, Arabic sent with
  `is_rtl=True`. Never hardcode a user-visible string outside a catalog.
- The engine adapter is the only module that knows HTTP; the rendering layer is the only module that
  knows rich HTML or keyboards.
- Commit messages: conventional commits, English, imperative.

## Measured aiogram 3.31.0 facts this repo depends on

Confirmed by introspecting the installed library — the class names and argument shapes here are
load-bearing:

- `Bot.send_rich_message` and `Bot.send_rich_message_draft` exist; **`Bot.edit_rich_message` does
  not** — in-place rich updates use `edit_message_text(rich_message=…)`.
- `InputRichMessage` fields: `html`, `markdown`, `is_rtl`, `skip_entity_detection`, `blocks`, `media`.
- **`ButtonStyle` lives in `aiogram.enums`** (members `DANGER`, `SUCCESS`, `PRIMARY`, `LINK`).
  `InlineKeyboardButton.style` is typed `str | None`, so a typo is not caught by the type system.
- **`InlineKeyboardButton(disabled=True)` raises `ValidationError`.** The field takes a
  `DisabledButton` instance (`from aiogram.types import DisabledButton`); the keyboard helper accepts
  a boolean and constructs it.
- A command run accepts `kind: "command"` with `argv`, or `kind: "file"` with `file`; the output is
  read from the logs endpoint by offset (`{data, offset, next, eof, truncated}`), never from the run
  object.
