# Persistence

The bot keeps its own state that the engine deliberately does not: **which
Telegram user owns which workspace**, each user's language, and (Phase 1.5)
terminal-session handles. Because a single Cloud Service key owns every
workspace, this registry **is** the isolation boundary and must be correct.

Phase 1 uses **SQLite via `aiosqlite`**. The repository is an interface, so
Postgres is a swap of one adapter, not a rewrite (see §7).

---

## 1. What we store and why

| Data | Why the engine can't hold it | Source of truth |
| --- | --- | --- |
| `tg_id -> locale` | Engine knows nothing about people. | Bot |
| `tg_id -> [ws_...]` | Engine `owner_id` is the bot's single principal; it cannot separate our users. | **Bot (isolation)** |
| `tg_id -> plan` | Engine has no billing/plans (SPEC §2.6.1). | Bot |
| session handles ↔ `ses_...` | Engine sessions are not addressable from Telegram. | Bot (Phase 1.5) |
| small UI state | FSM storage, in-flight prompts. | Bot |

The engine remains the source of truth for **workspace facts** (status, limits,
summary, runs, files). The bot stores only *ownership pointers* and *preferences*;
it never caches engine facts as authoritative (they can change server-side).

---

## 2. Schema (migration `0001_init.sql`)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    locale      TEXT    NOT NULL DEFAULT 'en',   -- 'ar' | 'en'
    plan        TEXT    NOT NULL DEFAULT 'free',
    created_at  TEXT    NOT NULL,                -- RFC3339 UTC
    last_seen_at TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS user_workspaces (
    tg_id       INTEGER NOT NULL,
    ws_id       TEXT    NOT NULL,                -- 'ws_...'
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (tg_id, ws_id),
    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_workspaces_ws ON user_workspaces(ws_id);

-- Phase 1.5
CREATE TABLE IF NOT EXISTS terminal_sessions (
    tg_id        INTEGER NOT NULL,
    ws_id        TEXT    NOT NULL,
    ses_id       TEXT    NOT NULL,              -- 'ses_...'
    cols         INTEGER NOT NULL DEFAULT 80,
    rows         INTEGER NOT NULL DEFAULT 24,
    out_offset   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL,
    PRIMARY KEY (tg_id, ses_id),
    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
```

Notes:
- **WAL mode** for concurrent readers with a single writer; good enough for a
  single-instance bot. Postgres removes the need (see §7).
- Timestamps are **UTC RFC3339 strings**; the domain uses `datetime` and
  `Clock`, the adapter handles encoding.
- `user_workspaces` has no FK to the engine — the engine is external. Orphans
  (a workspace deleted engine-side) are reconciled lazily: a `404 not_found`
  from the engine triggers `remove_workspace` and an updated screen.
- `terminal_sessions.out_offset` is the last-read byte cursor, per the engine's
  offset protocol (INTEGRATION-GUIDE §5.2).

---

## 3. Migrations

`adapters/persistence/database.py` runs an ordered list of numbered SQL files at
startup, recording applied versions in `schema_migrations`. Migrations are:

- **Forward-only** and idempotent (`IF NOT EXISTS`).
- **Small and reviewed**; each is a file in `migrations/`.
- **Tested** against a fresh in-memory DB and against the previous version to
  prove upgrade safety.

The bot refuses to start if a migration fails (fail fast; never run on a
half-migrated DB).

---

## 4. Repositories

### 4.1 `UserRepository` (SQLite)

```python
class SqliteUserRepository(UserRepository):
    async def get_or_create(self, tg_id, *, default_locale) -> User: ...
    async def get(self, tg_id) -> User | None: ...
    async def set_locale(self, tg_id, locale) -> None: ...
    async def add_workspace(self, tg_id, ws_id) -> None: ...
    async def remove_workspace(self, tg_id, ws_id) -> None: ...
    async def owned_workspaces(self, tg_id) -> list[WorkspaceId]: ...
    async def owns(self, tg_id, ws_id) -> bool: ...
```

`get_or_create` upserts the row (`INSERT ... ON CONFLICT DO NOTHING`) and then
`SELECT`s it, so concurrent first-messages cannot create duplicates or crash. The
shared connection runs in **autocommit** (`isolation_level=None`) precisely so two
overlapping statements from concurrently-processed updates cannot nest a
transaction on the one connection; each statement is individually atomic and every
multi-statement write is expressed as idempotent upserts. `owns` is a single
indexed `SELECT EXISTS`.

### 4.2 `SessionStore` (Phase 1.5)

`create`, `get`, `set_offset`, `update_size`, `list_for_user`,
`list_for_workspace`, `delete`.

`get`/`list` return the persisted handle with an `unknown` status; the engine
remains the source of truth for a session's live status, and `SessionsService`
merges the two. `update_size` records a PTY resize so the handle matches the
engine.

---

## 5. The isolation invariant

> `services/ownership.assert_owned(tg_id, ws_id)` consults `UserRepository.owns`.
> Every workspace-scoped service method calls it **before** touching the engine.
> A workspace not in the caller's set is treated as absent (identical to a real
> `not_found`) so ids cannot be probed.

This invariant is tested with a table-driven test enumerating **every**
workspace-scoped engine method and asserting that a foreign `tg_id` gets
`NotOwned` and that the engine is **never called** for it.

---

## 6. Consistency and lifecycle events

| Event | Bot action |
| --- | --- |
| Workspace created | `add_workspace(tg_id, ws.id)` after the engine returns the id. On DB failure, delete the engine workspace (compensate) and log. |
| Workspace deleted by user | `remove_workspace` after `DELETE` returns `204`. |
| Engine returns `404 not_found` for a workspace | `remove_workspace` (lazy reconcile), render "no longer exists". |
| Engine `403 forbidden` | Should never happen with a single key unless the id belongs to another principal; log as anomaly, treat as not-found to the user. |
| User blocked the bot / chat not found | Do not delete data; mark inactive on next failed send. |

The bot never deletes engine data on its own except compensating for a failed
registration.

---

## 7. The Postgres swap (later, without a rewrite)

The repositories are defined as **ports** in `domain/ports/users.py` and
`sessions.py`. To move:

1. Add `adapters/persistence/postgres_users.py` implementing the same Protocol.
2. Add migrations for Postgres (same logical schema).
3. Change one line in `bootstrap/container.py` to instantiate the Postgres
   repository from `settings.database_url`.

No service, screen, handler, or domain code changes. FSM storage similarly moves
from memory/SQLite to Redis by changing `bootstrap/app.py`. This is the reason the
infra answer was "SQLite now, abstracted" rather than "SQLite tied in".

---

## 8. Backup and operational notes

- The DB file lives under `data/` (configurable). WAL means a copy must include
  `-wal`/`-shm` or use `VACUUM INTO` for a consistent snapshot.
- The DB is small (ids + preferences); the engine holds the heavy data.
- Loss of the DB means loss of ownership mapping — the engine still holds the
  workspaces, but the bot cannot attribute them to users. Keep it backed up, and
  record this risk in `DECISIONS.md`. (An operator could re-adopt orphan
  workspaces with an admin tool; noted as a Phase 2 maintenance tool.)

### 8.1 The artifact is a dump of the database in use

`BackupService.make_backup("full")` writes one `.zip` carrying the database dump
and `manifest.json`. Which dump it is follows `DATABASE_URL`, never the
deployment's history:

| Deployment | Member | Producer |
| --- | --- | --- |
| Postgres (`postgresql://…`) | `database.sql` | `pg_dump --no-owner --no-privileges --clean --if-exists --format=plain --inserts` |
| SQLite (`sqlite+aiosqlite://…`) | `bot.db` | SQLite's own backup API |

The manifest carries `format` (`postgres-plain-sql` / `sqlite`),
`database_version`, both schema versions, and `counts` read from the **same
snapshot that produced the dump**. On Postgres the adapter pins one connection,
`pg_export_snapshot()`s it, starts the `pg_dump` child with `--snapshot=<id>`
and runs `select count(*)` on that same transaction — so the manifest can never
describe a later moment than its own bytes. `sha256` is over the dump bytes and
is verified when the artifact is read for a restore.

`pg_dump` runs as a child of the process that holds the DSN. Its environment is
built explicitly (`PGHOST`/`PGPORT`/`PGUSER`/`PGDATABASE`/`PGPASSWORD`/`PGSSLMODE`
from the DSN) because a scheduler's child is not guaranteed to inherit ours, and
the DSN itself is never placed on a command line (`ps` can see it) nor logged.
`CLOUDBOT_PG_DUMP_BIN` / `CLOUDBOT_PSQL_BIN` override the binaries; by default
the newest installed client at or above the server's major version is chosen,
because an older client refuses a newer server.

**Legacy SQLite archives are never produced again and never auto-deleted.** On a
Postgres deployment they cannot be restored (the restore path refuses them), so
retention counts only the current format: exactly `retention_keep` `database.sql`
artifacts survive, the newest is protected whatever the setting says, settings
JSONs are kept, and every other file in the directory is untouched. The admin
screen says this in words.
