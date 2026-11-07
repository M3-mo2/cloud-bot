-- TASK moderation: user bans and workspace bans.
--
-- A user ban is an identity decision that refuses the user at the middleware
-- gate everywhere, including /start. A NULL `expires_at` is a permanent ban;
-- an `expires_at` in the past is simply no longer active, so a temporary ban
-- needs no manual unban. Re-banning upserts the row (one ban per subject).
--
-- A workspace ban is an admin decision kept apart from the engine's `suspend`
-- (a user-facing pause). It has no expiry: only an admin can lift it, and every
-- execution path checks it at the service layer.

CREATE TABLE IF NOT EXISTS user_bans (
    tg_id       INTEGER PRIMARY KEY,
    reason      TEXT    NOT NULL DEFAULT '',
    banned_at   TEXT    NOT NULL,
    banned_by   INTEGER NOT NULL,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS workspace_bans (
    ws_id       TEXT    PRIMARY KEY,
    reason      TEXT    NOT NULL DEFAULT '',
    banned_at   TEXT    NOT NULL,
    banned_by   INTEGER NOT NULL
);
