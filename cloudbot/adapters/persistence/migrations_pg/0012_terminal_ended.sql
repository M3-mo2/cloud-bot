-- The bot-side clock for a terminal session's grace window (TASK terminal-direct).
--
-- ``ended_at`` records when the bot first observed the session in a terminal
-- state. It is the bot's own hygiene clock, not the engine's status: the engine
-- stays the source of truth for whether a session is running. A finished handle
-- older than the grace window is forgotten so a revisit cannot resurrect a corpse
-- list. NULL means "never observed terminal yet" (a live session, or a handle
-- written by an older version, stamped on the next sweep).
ALTER TABLE terminal_sessions ADD COLUMN ended_at TEXT;
