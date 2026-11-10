-- Task subs1: the subscription wallet and plan state.
--
-- ADDITIVE ONLY: no existing column is dropped and no table is rewritten. The
-- bot's plan is now owned by the domain (``cloudbot/domain/plans.py`` +
-- ``cloudbot/domain/subscription.py``) and persisted here; the legacy
-- ``users.plan`` text column is left untouched for the migration window and is no
-- longer read as a source of truth.
--
-- The wallet has **no** balance column: a balance is always ``SUM(amount)`` over
-- ``wallet_ledger``, so it cannot drift from its own history. A movement is
-- append-only. ``admin_set`` stores the signed delta it applied, which keeps the
-- sum identity true for every kind.

-- One current subscription per user. ``source`` is trial | stars | admin.
CREATE TABLE IF NOT EXISTS subscriptions (
    tg_id       BIGINT PRIMARY KEY,
    plan_code   TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,                -- RFC3339 UTC
    expires_at  TEXT    NOT NULL,                -- RFC3339 UTC
    source      TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

-- The trial flag that cannot be granted twice: presence means "already used".
CREATE TABLE IF NOT EXISTS subscription_trials (
    tg_id      BIGINT PRIMARY KEY,
    granted_at TEXT    NOT NULL
);

-- Append-only wallet history. ``idempotency_key`` is the caller's retry guard;
-- an empty key means "not deduplicated". The partial unique index is what makes
-- a replayed Telegram ``successful_payment`` a no-op instead of a second credit.
CREATE TABLE IF NOT EXISTS wallet_ledger (
    id              BIGSERIAL PRIMARY KEY,
    tg_id           BIGINT NOT NULL,
    kind            TEXT    NOT NULL,            -- credit | debit | admin_set | refund
    amount          BIGINT NOT NULL,            -- signed delta (see above)
    reason          TEXT    NOT NULL DEFAULT '',
    at              TEXT    NOT NULL,            -- RFC3339 UTC
    actor_tg_id     BIGINT NOT NULL,
    idempotency_key TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_tg ON wallet_ledger(tg_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_ledger_idem
    ON wallet_ledger(idempotency_key) WHERE idempotency_key <> '';

-- The audit trail for every subscription change (purchase, trial, admin grant /
-- extend / shorten / revoke), attributed to the actor and the reason.
CREATE TABLE IF NOT EXISTS subscription_events (
    id              BIGSERIAL PRIMARY KEY,
    tg_id           BIGINT NOT NULL,
    action          TEXT    NOT NULL,            -- purchase | trial | grant | shorten | revoke
    plan_code       TEXT    NOT NULL DEFAULT '',
    days_delta      BIGINT NOT NULL DEFAULT 0,
    actor_tg_id     BIGINT NOT NULL,
    reason          TEXT    NOT NULL DEFAULT '',
    at              TEXT    NOT NULL,
    idempotency_key TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subscription_events_tg ON subscription_events(tg_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_events_idem
    ON subscription_events(idempotency_key) WHERE idempotency_key <> '';
