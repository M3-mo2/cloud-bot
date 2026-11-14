-- TASK forcejoin-v2: one row per gate decision, so the gate is measurable.
--
-- The gate used to decide and record nothing: "how many users did the gate refuse
-- this week", "which chat loses the most people", "is the bot still an admin
-- there" were unanswerable. This table is the answer. A row is written by the
-- service (never by a handler) for every membership decision — the middleware,
-- the «تحقّقت» button and the admin test all pass through it — plus one row for
-- each operator configuration change (outcome='config').
--
-- `outcome` is 'pass' | 'deny' | 'unknown' | 'error' | 'joined' | 'config'.
-- `reason` is a short stable code, never a Telegram sentence: 'subscribed',
-- 'absent', 'not_admin', 'network', 'rate_limited', 'cached_positive',
-- 'bad_request', 'api', and (for config rows) 'add' / 'remove' / 'enable' /
-- 'disable' / 'test' / 'gate_on' / 'gate_off'.
-- `cached=1` marks an answer that came from the positive cache (latency 0), so the
-- cache is visible in the numbers instead of silently saving calls.
-- Refusals are never cached and therefore always produce a fresh row.
-- `detail` is a short scrubbed error string, capped by the writer at 300 chars.

CREATE TABLE IF NOT EXISTS forcejoin_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    tg_id      INTEGER,
    chat_id    INTEGER,
    outcome    TEXT    NOT NULL
               CHECK (outcome IN ('pass', 'deny', 'unknown', 'error', 'joined', 'config')),
    reason     TEXT    NOT NULL DEFAULT '',
    latency_ms INTEGER,
    cached     INTEGER NOT NULL DEFAULT 0,
    detail     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fj_events_ts      ON forcejoin_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_fj_events_outcome ON forcejoin_events(outcome, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fj_events_chat    ON forcejoin_events(chat_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fj_events_user    ON forcejoin_events(tg_id, ts DESC);
