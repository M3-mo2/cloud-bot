# cloud-bot — Documentation

**cloud-bot** is the operator-owned frontend for **Cloud Service** (`sandboxd`).
It is a **Telegram bot** written in **Python** with **aiogram 3.31.0**. It is the
only product that holds a Cloud Service bearer key; end users never see, hold, or
send one.

> Cloud Service is a headless sandbox engine: its clients are trusted backend
> services. cloud-bot is exactly that client, and it owns accounts, plans, quota,
> billing, and user isolation — none of which the engine implements.

The bot users see is small and calm. Underneath it is a strict layered
architecture, a bot-side ownership registry, a rendering layer that guarantees no
raw engine output ever reaches a user, and a first-class bilingual (Arabic +
English) presentation model.

---

## Read in this order

| # | Document | What it answers |
| --- | --- | --- |
| 1 | [`architecture/01-overview.md`](architecture/01-overview.md) | What the bot is, who uses it, the guiding principles. |
| 2 | [`architecture/02-layers.md`](architecture/02-layers.md) | The package tree, layer rules, dependency direction. |
| 3 | [`architecture/03-components.md`](architecture/03-components.md) | Responsibilities and public shape of every component. |
| 4 | [`architecture/04-data-flow.md`](architecture/04-data-flow.md) | End-to-end request lifecycles (create, run, upload, stream). |
| 5 | [`architecture/05-runtime-modes.md`](architecture/05-runtime-modes.md) | PRODUCTION / DEV / DEBUG — exactly what changes. |
| 6 | [`architecture/06-i18n.md`](architecture/06-i18n.md) | The bilingual model: locale flow, RTL, plurals, formatting. |
| 7 | [`api/cloud-service-client.md`](api/cloud-service-client.md) | The engine adapter: endpoints, retries, error translation. |
| 8 | [`api/persistence.md`](api/persistence.md) | SQLite schema, repositories, the swap to Postgres. |
| 9 | [`rendering/rich-messages.md`](rendering/rich-messages.md) | Rich-only contract, capabilities, limits, safety. |
| 10 | [`rendering/keyboards.md`](rendering/keyboards.md) | Button types, colors, and the intent→style convention. |
| 11 | [`ui/01-language-and-menu.md`](ui/01-language-and-menu.md) | Language picker, main menu. |
| 12 | [`ui/02-workspaces.md`](ui/02-workspaces.md) | List, detail, create; lifecycle actions. |
| 13 | [`ui/03-files.md`](ui/03-files.md) | Browser, stat, read, mkdir, delete, upload. |
| 14 | [`ui/04-execution.md`](ui/04-execution.md) | Exec, runs, live logs, history. |
| 15 | [`ui/05-errors-and-status.md`](ui/05-errors-and-status.md) | Exhaustive AR/EN mapping for every code and status. |
| 16 | [`ui/06-behavior.md`](ui/06-behavior.md) | Streaming, progress, busy states, dialogs, timers. |
| 17 | [`DECISIONS.md`](DECISIONS.md) | Every call we made and why. |
| 18 | [`ROADMAP.md`](ROADMAP.md) | Phase 0/1/1.5/2/3 scope and sequencing. |
| 19 | [`../docs-aiogram-updates/aiogram-rich-messages-html.md`](../docs-aiogram-updates/aiogram-rich-messages-html.md) | The rich-message API the whole rendering contract depends on. |
| 20 | [`../docs-aiogram-updates/keyboards-update-3.31.0.md`](../docs-aiogram-updates/keyboards-update-3.31.0.md) | What 3.31.0 added to keyboards (`disabled`, `force_reply`, `LINK`). |
| 21 | [`../docs-aiogram-updates/aiogram-colored-buttons.md`](../docs-aiogram-updates/aiogram-colored-buttons.md) | Colored buttons and the `ButtonStyle` enum. |
| 22 | [`../docs-cloud-service/REVIEW-sandboxd-v0.0.5.md`](../docs-cloud-service/REVIEW-sandboxd-v0.0.5.md) | Historical review of the engine at v0.0.5 (context for the decisions). |

**Precedence.** If any document here ever conflicts with
`docs-cloud-service/INTEGRATION-GUIDE.md`, the integration guide wins — it is the
authoritative usage contract for the engine (v0.0.20, API prefix `/v1`).

---

## The five rules that never bend

1. **Rich-only output.** Every user-facing message is sent or edited through
   `send_rich_message` / `edit_message_text(rich_message=...)` with an
   `InputRichMessage`. `send_message(parse_mode=...)` is never used.
2. **No raw engine output.** No JSON, and no raw English engine `message`, ever
   reaches a normal user. The translation layer maps every code and status to
   human words. DEBUG mode is the only exception, for admins only, collapsed.
3. **Users see human words.** Errors, statuses, sizes, durations and dates are
   all rendered by `presentation/`.
4. **Isolation is ours.** One Cloud Service key owns every workspace
   (`principal_id`); the engine cannot separate our users. The bot enforces
   `tg_id -> owned ws_...` itself, in the domain layer, on every call.
5. **Locale is structural.** Every string carries a `Locale`; Arabic is sent with
   `is_rtl=True`; numbers, dates and plurals are localized, not translated as an
   afterthought.

---

## AI agent quick-start

The rules an automated agent must follow live in [`../AGENTS.md`](../AGENTS.md).

- Stack: Python 3.12+, `aiogram==3.31.0`, `httpx`, `pydantic`/`pydantic-settings`,
  `aiosqlite`, `babel`, `structlog`.
- Entry: `python -m cloudbot`.
- Engine adapter is the **only** module that knows HTTP or Cloud Service DTOs.
- Rendering is the **only** module that knows rich HTML or keyboards.
- Domain services use ports (`Protocol`s); swap the engine or the DB without
  touching handlers.
- The exhaustive status/error vocabulary lives in
  [`ui/05-errors-and-status.md`](ui/05-errors-and-status.md); the AR/EN catalogs
  mirror it exactly.

---

## Verified aiogram 3.31.0 facts (measured, not assumed)

These were confirmed by introspecting the installed library, and the design
depends on them:

| Fact | Result | Consequence |
| --- | --- | --- |
| `Bot.send_rich_message` | **exists**; returns `Message` | Primary send path. |
| `send_rich_message(reply_markup=...)` | **accepted** (`ReplyMarkupUnion`) | Rich messages carry inline/reply keyboards. |
| `Bot.edit_rich_message` | **does not exist** | In-place updates use `edit_message_text(rich_message=InputRichMessage(...))`. |
| `edit_message_text` rich support | `rich_message: InputRichMessage \| None` | Supported in-place rich edit path. |
| `Bot.send_rich_message_draft` | **exists** | Streaming live run output (ephemeral 30 s, then persist). |
| `InputRichMessage` fields | `html, markdown, is_rtl, skip_entity_detection, blocks, media` | `is_rtl=True` for Arabic; exactly one of html/markdown/blocks. |
| `ButtonStyle` | `primary, success, danger, link` | Color convention. |
| `InlineKeyboardButton.disabled` | `DisabledButton \| None` | Inactive-but-visible buttons. |
| `InlineKeyboardMarkup.force_reply` / `ReplyKeyboardMarkup.force_reply` | present | Force the reply interface. |
| Structured blocks | `InputRichBlockTable`, `InputRichBlockDetails`, `InputRichBlockPreformatted`, `InputRichBlockList`, `InputRichBlockButtons`, … | Tables, collapsibles, code blocks. |
| `stopped_message_generation` | a top-level `Update` field | User can stop a draft stream. |
