# 01 — Overview

## 1. What cloud-bot is

cloud-bot is the **operator-owned frontend** for Cloud Service (`sandboxd`). It is
a Telegram bot that lets a person create an isolated sandbox workspace, put files
in it, and run code — without ever knowing that an engine called `sandboxd`
exists, and without ever seeing its wire format.

It sits exactly where the engine documentation says a frontend sits
(ARCHITECTURE §1, "Who consumes sandboxd"; SPEC §2.6.1):

```
Telegram user  ──▶  cloud-bot  ──HTTP/JSON, Bearer <service key>──▶  sandboxd
   (end user)      (this repo)                                      (engine)
```

The engine treats cloud-bot as a **trusted backend**. The bot owns everything the
engine deliberately does not: accounts, plans, per-user quota, rate limiting,
billing, credential issuance, and — most importantly — **user isolation**.

## 2. Who uses it

- **End users** — people in Telegram. They talk to the bot, pick a language,
  manage workspaces, upload files, and run code. They never see a service key.
- **Operators** — the people who deploy the bot. They hold the Cloud Service key,
  the Cloud Service base URL, the bot token, and admin ids. They use DEBUG mode
  for incident response.
- **Developers** — people extending the bot. They add domain services, adapters,
  screens, and translations following the layer rules in
  [`02-layers.md`](02-layers.md).

## 3. What the user experience is

A calm, fast, bilingual bot:

1. First `/start` — the user chooses **العربية** or **English**. The choice is
   stored and switchable forever after.
2. A **main menu** with their workspaces.
3. A **workspace** is a sandbox: they browse files, upload, create directories,
   run a file or a command, watch output, and manage the workspace's lifecycle
   (stop / suspend / resume / delete).
4. Everything is shown as **rich messages** — headings, tables, collapsible
   detail, code blocks — never as raw API output.
5. Every screen works identically and naturally in Arabic (right-to-left) and
   English (left-to-right).

## 4. The five guiding principles

### 4.1 Rich-only

Every user-facing message is a rich message. There is no legacy path, no
`parse_mode="HTML"`, no plain `send_message`. This is enforced by making the
renderer the *only* way to produce output; handlers physically cannot send text
any other way (see [`../rendering/rich-messages.md`](../rendering/rich-messages.md)).

### 4.2 No raw engine output, ever

The engine returns structured JSON and — on failure — an English `message` meant
for operators. None of that reaches a normal user. A translation layer maps every
`error.code`, every `run.status`, every `workspace.status` to human words in the
user's language. The complete mapping is
[`../ui/05-errors-and-status.md`](../ui/05-errors-and-status.md). DEBUG mode may
reveal raw payloads, but only to admins and only collapsed.

### 4.3 Users see human words

`status: "rate_limited"` is never shown as `rate_limited`. It is shown as
"🐌 وصل إلى حد سرعة الكتابة" / "Write-rate limit reached". Sizes are "2.3 MB of
256 MB (1%)", durations are "2 minutes ago", not ISO timestamps. All of this
lives in `presentation/`.

### 4.4 Isolation is ours

A single service key authenticates the bot to the engine as one `principal_id`.
The engine's ownership check (`workspace.owner_id == principal_id`) therefore
**cannot** distinguish user A from user B. The bot keeps its own registry —
`tg_id -> list of owned ws_...` — and enforces it in the **domain layer**, on
every workspace-scoped operation. A workspace id the caller does not own is
treated as nonexistent. See [`../api/persistence.md`](../api/persistence.md).

### 4.5 Locale is structural

Language is not a veneer applied at the end. Every rendered string is produced
by a catalog lookup keyed by `Locale`; the keyboard builder, the number/date
formatters, and the rich-message sender all receive the `Locale`. Arabic forces
`is_rtl=True`. Mixed-direction text (an English filename inside an Arabic
sentence) is wrapped deliberately rather than left to the client. See
[`06-i18n.md`](06-i18n.md).

## 5. Scope

**Phase 1 (core, this design fully specifies it):** language + menu, workspaces
(list/detail/create/lifecycle), files (browse/stat/read/mkdir/delete/upload),
exec, runs (create/list/logs/stop/purge).

**Phase 1.5 (sequenced right after):** interactive terminal **sessions** — the
engine's native PTY API; designed here, built next.

**Phase 2+:** tunnels, Python packages, port discovery, metrics stream,
subscriptions/renewal, charts. See [`../ROADMAP.md`](../ROADMAP.md).

## 6. Non-goals

- The bot is **not** a general Telegram framework; it is a product.
- The bot does **not** re-implement the engine's isolation or invent its own
  sandbox. It calls the engine and presents results.
- The bot does **not** expose the engine's raw API to users or to Telegram
  clients.
- The bot does **not** attempt to hide that it is a sandbox product; it simply
  does not leak engine internals.

## 7. Where to go next

- Structure: [`02-layers.md`](02-layers.md)
- Components: [`03-components.md`](03-components.md)
- Lifecycles: [`04-data-flow.md`](04-data-flow.md)
- Modes: [`05-runtime-modes.md`](05-runtime-modes.md)
- Language: [`06-i18n.md`](06-i18n.md)
