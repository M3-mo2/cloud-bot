-- TASK broadcast: an admin broadcast is a *reference*, not a copy of his text.
--
-- The engine of exactness is Telegram's own ``copy_message``: the row stores the
-- ``(source_chat_id, source_message_id)`` pair the admin composed in his own
-- Telegram and forwards to the bot. No text is copied here, so a resend can never
-- drift from what he wrote. Counters are stored (never derived at render time) so
-- a restart shows the truth instead of a lost progress bar, and ``status`` is the
-- in-flight guard: one broadcast may be ``sending`` at a time.
--
-- ``broadcast_targets`` is the frozen audience snapshot taken at preview, so the
-- number the admin confirmed is exactly the number that is attempted. A target is
-- ``pending`` / ``sent`` / ``failed``; a retry only re-attempts the failed rows.
-- ``error`` is a short stable code (``blocked`` / ``deactivated`` / ``other``),
-- never a raw Telegram sentence.

CREATE TABLE IF NOT EXISTS broadcasts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    created_by          INTEGER NOT NULL,
    source_chat_id      INTEGER NOT NULL,
    source_message_id   INTEGER NOT NULL,
    source_kind         TEXT    NOT NULL
                        CHECK (source_kind IN ('text', 'photo', 'document', 'video')),
    audience            TEXT    NOT NULL DEFAULT 'all'
                        CHECK (audience IN ('all', 'active', 'expired', 'no_plan')),
    total               INTEGER NOT NULL DEFAULT 0,
    sent                INTEGER NOT NULL DEFAULT 0,
    failed              INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'composing'
                        CHECK (status IN ('composing', 'sending', 'done', 'stopped', 'interrupted')),
    started_at          TEXT,
    finished_at         TEXT,
    stop_reason         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_created ON broadcasts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status);

CREATE TABLE IF NOT EXISTS broadcast_targets (
    broadcast_id    INTEGER NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    tg_id           INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'failed')),
    error           TEXT    NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT,
    PRIMARY KEY (broadcast_id, tg_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_targets_status
    ON broadcast_targets(broadcast_id, status);
