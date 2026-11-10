-- TASK forcejoin: a mandatory subscription gate managed from the admin panel.
--
-- `forcejoin_chats` holds every chat (channel or group) a user must join before
-- the bot will do anything. A row is kept when the chat is disabled: disabling is
-- a state, not a deletion, so a mistake is one tap from undone and the chat's
-- title/kind are not lost. `is_admin` is the last measured fact (does the bot
-- administer this chat there); a false value is shown as a red warning in the
-- panel and makes the gate allow users through instead of locking them out.
--
-- `runtime_flags` is the project's one key/value store for runtime switches. It
-- is deliberately generic so the next flag does not invent a second table. The
-- gate's global switch is the row key='forcejoin.enabled' (a missing row means
-- "on", so a fresh install behaves as the operator expects once a chat is added).

CREATE TABLE IF NOT EXISTS forcejoin_chats (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT NOT NULL UNIQUE,          -- Telegram chat id (negative for channels/groups)
    username   TEXT    NOT NULL DEFAULT '',      -- @username without the @, when known
    title      TEXT    NOT NULL DEFAULT '',      -- the chat's display title
    kind       TEXT    NOT NULL DEFAULT 'channel' CHECK (kind IN ('channel', 'group')),
    is_admin   BIGINT NOT NULL DEFAULT 0,       -- the bot is an administrator there
    enabled    BIGINT NOT NULL DEFAULT 1,       -- on/off without deleting the row
    added_at   TEXT    NOT NULL,                 -- RFC3339 UTC
    added_by   BIGINT NOT NULL                  -- the admin's tg_id
);
CREATE INDEX IF NOT EXISTS idx_forcejoin_enabled ON forcejoin_chats(enabled);

CREATE TABLE IF NOT EXISTS runtime_flags (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
