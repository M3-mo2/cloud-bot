-- Phase 3 admin panel: the event log, per-admin notification subscriptions,
-- the per-admin delivery cursor, and the persisted "entered workspace" mode.
--
-- Forward-only. The runner records version 2 in schema_migrations and never
-- re-applies this file, so the ALTER below runs exactly once. Every other
-- statement is CREATE ... IF NOT EXISTS so a re-run (e.g. a manually partial
-- apply) is harmless.

-- The display name Telegram gave us (a username or full name); it is a reading
-- aid for the admin panel, never an identity.
ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '';

-- The event log: one row per thing that happened. This is both the audit trail
-- and the source the notifier reads. ``actor`` is one of ``user`` / ``system`` /
-- ``admin:<tg_id>`` and keeps the admin acting on someone else's workspace
-- separate from the user themselves.
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TEXT    NOT NULL,                 -- RFC3339 UTC
    category    TEXT    NOT NULL,                 -- a domain EventCategory value
    severity    TEXT    NOT NULL DEFAULT 'info',  -- info | warning | error
    tg_id       BIGINT,                          -- subject user, nullable
    ws_id       TEXT,                             -- subject workspace, nullable
    summary_key TEXT    NOT NULL,                 -- a catalog key
    params      TEXT    NOT NULL DEFAULT '{}',    -- JSON object of format args
    actor       TEXT    NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_tg ON events(tg_id);

-- Per-admin subscriptions: which categories an admin wants delivered.
CREATE TABLE IF NOT EXISTS event_subscriptions (
    admin_id  BIGINT NOT NULL,
    category  TEXT    NOT NULL,
    enabled   BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (admin_id, category)
);

-- Per-admin delivery state: the last event id delivered and the quiet switch.
-- Persisted so a restart never loses or duplicates a delivery.
CREATE TABLE IF NOT EXISTS admin_notify_state (
    admin_id                BIGINT PRIMARY KEY,
    last_delivered_event_id BIGINT NOT NULL DEFAULT 0,
    paused                  BIGINT NOT NULL DEFAULT 0,
    scope_tg_id             BIGINT,             -- when set, deliver only this user's events
    updated_at              TEXT    NOT NULL
);

-- The persisted "entered workspace" mode: an admin viewing another user's
-- workspace. The banner is generated from this row, so it survives a restart.
CREATE TABLE IF NOT EXISTS admin_mode (
    admin_id              BIGINT PRIMARY KEY,
    entered_ws_id         TEXT,
    entered_owner_tg_id   BIGINT,
    updated_at            TEXT NOT NULL
);
