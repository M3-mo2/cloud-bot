-- Task subs2: Telegram Stars payments.
--
-- ADDITIVE ONLY. A delivered ``successful_payment`` is recorded here exactly
-- once (``charge_id`` is unique), and the wallet credit uses the same charge id
-- as its idempotency key, so a Telegram re-delivery can neither record twice nor
-- credit twice. ``charge_id`` is kept because ``refundStarPayment`` needs it.
-- ``refunded_at`` marks a refunded payment; the wallet reversal is a ledger row
-- with its own idempotency key, so a retried refund is a no-op as well.

CREATE TABLE IF NOT EXISTS stars_payments (
    id                  BIGSERIAL PRIMARY KEY,
    tg_id               BIGINT NOT NULL,
    pack_code           TEXT    NOT NULL,
    stars               BIGINT NOT NULL,
    charge_id           TEXT    NOT NULL,
    provider_charge_id  TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL,        -- RFC3339 UTC
    refunded_at         TEXT                     -- NULL until refunded
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stars_payments_charge
    ON stars_payments(charge_id);
CREATE INDEX IF NOT EXISTS idx_stars_payments_tg ON stars_payments(tg_id);
