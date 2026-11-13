-- The Telegram message id of the last delivered artifact (TASK backup-repair).
--
-- ``destination_chat_id`` says where a scheduled backup goes; this records the
-- message it actually became, so "did it arrive?" is a stored fact rather than
-- an inference from a log line. NULL means the last run did not deliver (or
-- there was no destination); an empty string is never written.
ALTER TABLE backup_config ADD COLUMN IF NOT EXISTS last_message_id TEXT;
