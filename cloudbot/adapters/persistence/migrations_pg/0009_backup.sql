-- TASK backup: the admin panel's backup schedule and its persisted state.
--
-- One row (id = 1). `next_run_at` is stored, not derived at boot, so a restart
-- cannot drift the interval: the scheduler only advances it when a backup has
-- actually run. `destination_chat_id` NULL means "the admin who configured it".
-- `period_kind` is hours (every N hours) or daily (a wall-clock time in
-- `tz_name`, Africa/Cairo by default). The settings-only backup copies this row
-- too: the schedule is configuration.

CREATE TABLE IF NOT EXISTS backup_config (
    id                  BIGINT PRIMARY KEY CHECK (id = 1),
    enabled             BIGINT NOT NULL DEFAULT 0,
    period_kind         TEXT    NOT NULL DEFAULT 'hours'
                        CHECK (period_kind IN ('hours', 'daily')),
    period_hours        BIGINT NOT NULL DEFAULT 24,
    daily_time          TEXT    NOT NULL DEFAULT '03:00',
    tz_name             TEXT    NOT NULL DEFAULT 'Africa/Cairo',
    destination_chat_id BIGINT,
    retention_keep      BIGINT NOT NULL DEFAULT 7,
    next_run_at         TEXT,
    last_run_at         TEXT,
    last_error          TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    NOT NULL,
    updated_by          BIGINT NOT NULL DEFAULT 0
);

INSERT INTO backup_config (id, updated_at) VALUES (1, to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')) ON CONFLICT DO NOTHING;
