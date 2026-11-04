-- Task 10: a persisted workspace session.
--
-- Opening a workspace is "entering" it, like a login; ``/start`` puts the user
-- back inside; a "log out of workspace" button leaves and switches.
--
-- The session is a column on ``users``, never aiogram FSM state, because it must
-- survive a restart, a redeploy and a brand-new process. Forward-only: the
-- runner records version 3 in ``schema_migrations`` and never re-applies this
-- file, so the ALTER below runs exactly once (matching 0002's ALTER).
ALTER TABLE users ADD COLUMN current_ws_id TEXT NULL;
