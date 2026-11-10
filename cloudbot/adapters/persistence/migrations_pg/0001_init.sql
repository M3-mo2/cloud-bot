-- Postgres flavour of migration 0001. Forward-only, same version numbers and the
-- same ``schema_migrations`` bookkeeping as the SQLite set in ../migrations.
--
-- Type decisions, held consistently across all twelve files:
--   * Every timestamp column stays TEXT (RFC3339 UTC), not TIMESTAMPTZ. The
--     domain reads timestamps as strings (``parse_dt``) and several queries
--     compare them as text (``started_at``, ``created_at`` ordering); TEXT keeps
--     both working with zero call-site changes, and the facade renders
--     ``datetime('now')`` as the same UTC RFC3339 text.
--   * Every ``INTEGER`` becomes ``BIGINT``. SQLite's INTEGER is 64-bit and
--     Postgres's is 32-bit: a Telegram id (6508129575) overflowed the 32-bit
--     type during the live run, so the wider type is required, not a preference.
--   * Every boolean stays BIGINT 0/1: the stores bind 0/1 explicitly and read
--     with ``bool(...)``, so a real BOOLEAN column would only add a mismatch.
--   * JSON stays TEXT (the events ``params`` column is dumped/loaded in Python).
--   * ``INTEGER PRIMARY KEY AUTOINCREMENT`` becomes ``BIGSERIAL PRIMARY KEY``.
--   * ``INTEGER PRIMARY KEY`` (a rowid alias in SQLite) becomes
--     ``BIGINT PRIMARY KEY``: no AUTOINCREMENT existed, so no sequence is added.
--   * ``CREATE INDEX IF NOT EXISTS`` names are identical to the SQLite set so a
--     plan comparison is possible.
--   * ``PRAGMA`` statements are SQLite-only and are kept above as comments: the
--     foreign-key intent is always enforced by Postgres and WAL does not exist.

-- PRAGMA journal_mode = WAL;  (SQLite only: Postgres always enforces foreign keys)
-- PRAGMA foreign_keys = ON;  (SQLite only: Postgres always enforces foreign keys)

CREATE TABLE IF NOT EXISTS users (
    tg_id       BIGINT PRIMARY KEY,
    locale      TEXT    NOT NULL DEFAULT 'en',   -- 'ar' | 'en'
    plan        TEXT    NOT NULL DEFAULT 'free',
    created_at  TEXT    NOT NULL,                -- RFC3339 UTC
    last_seen_at TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS user_workspaces (
    tg_id       BIGINT NOT NULL,
    ws_id       TEXT    NOT NULL,                -- 'ws_...'
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (tg_id, ws_id),
    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_workspaces_ws ON user_workspaces(ws_id);

-- Phase 1.5
CREATE TABLE IF NOT EXISTS terminal_sessions (
    tg_id        BIGINT NOT NULL,
    ws_id        TEXT    NOT NULL,
    ses_id       TEXT    NOT NULL,              -- 'ses_...'
    cols         BIGINT NOT NULL DEFAULT 80,
    rows         BIGINT NOT NULL DEFAULT 24,
    out_offset   BIGINT NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL,
    PRIMARY KEY (tg_id, ses_id),
    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     BIGINT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
