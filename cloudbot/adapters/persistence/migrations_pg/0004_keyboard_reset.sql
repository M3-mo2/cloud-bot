-- Task 11: the legacy-reply-keyboard migration runs AT MOST ONCE per user.
--
-- The `ReplyKeyboardRemove` carrier is a message whose body is a single zero-width
-- space, so sending it on every `/start` put a permanent blank message in the chat.
-- This column records that the one-time migration has already happened for a user,
-- so a restart cannot repeat it. Forward-only, applied once (matching 0002/0003).
ALTER TABLE users ADD COLUMN legacy_keyboard_cleared_at TEXT NULL;
