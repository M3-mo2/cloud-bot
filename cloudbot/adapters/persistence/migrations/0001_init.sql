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
