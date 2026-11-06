-- Task planws: a subscription belongs to a WORKSPACE, not to a user.
--
-- Before this migration ``subscriptions`` was keyed by ``tg_id`` (one plan per
-- user), so one plan covered every workspace a user owned. Here the table is
-- rebuilt keyed by ``ws_id`` with ``tg_id`` kept as a non-unique owner column:
-- one user may hold one subscription per workspace.
--
-- Legacy rows: this is pre-launch and no real Stars have been spent (verified:
-- ``SELECT COUNT(*) FROM wallet_ledger WHERE kind='credit'`` was 0). Each legacy
-- user-level row is moved onto that user's FIRST workspace (by ``created_at``,
-- then ``ws_id`` so the choice is deterministic). A legacy row whose owner has
-- no workspace at all is preserved in ``subscriptions_legacy_orphans`` instead of
-- being deleted, so no data is silently lost. ``subscription_trials`` stays keyed
-- by ``tg_id`` on purpose: the free trial is once per user, never per workspace.

CREATE TABLE IF NOT EXISTS subscriptions_new (
    ws_id       TEXT    PRIMARY KEY,                -- 'ws_...'
    tg_id       INTEGER NOT NULL,                   -- the owner (not unique)
    plan_code   TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,                   -- RFC3339 UTC
    expires_at  TEXT    NOT NULL,                   -- RFC3339 UTC
    source      TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

INSERT INTO subscriptions_new
    (ws_id, tg_id, plan_code, started_at, expires_at, source, updated_at)
SELECT
    (SELECT w.ws_id FROM user_workspaces w
      WHERE w.tg_id = s.tg_id
      ORDER BY w.created_at, w.ws_id LIMIT 1),
    s.tg_id, s.plan_code, s.started_at, s.expires_at, s.source, s.updated_at
FROM subscriptions s
WHERE EXISTS (SELECT 1 FROM user_workspaces w WHERE w.tg_id = s.tg_id);

CREATE TABLE IF NOT EXISTS subscriptions_legacy_orphans AS
SELECT * FROM subscriptions s
WHERE NOT EXISTS (SELECT 1 FROM user_workspaces w WHERE w.tg_id = s.tg_id);

DROP TABLE subscriptions;
ALTER TABLE subscriptions_new RENAME TO subscriptions;
CREATE INDEX IF NOT EXISTS idx_subscriptions_tg ON subscriptions(tg_id);

-- The audit trail gains the workspace the change applied to (additive).
ALTER TABLE subscription_events ADD COLUMN ws_id TEXT NOT NULL DEFAULT '';
