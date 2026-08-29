-- ===========================================================================
-- 001_submission_tokens
-- ===========================================================================
-- Double submit protection.
--
-- Every form that creates a new record sends a one time submission_token.
-- The server inserts that token here inside the SAME transaction as the
-- record it creates. The primary key makes the insert atomic, so a second
-- request carrying the same token cannot create a second record, even when
-- both requests arrive at the same moment or land on different workers.
--
-- Only USED tokens are stored. Tokens are generated when a form is rendered
-- and are never registered in advance, so this table stays small.
--
-- Rows are deleted after 48 hours. That window is deliberately longer than
-- PERMANENT_SESSION_LIFETIME (12 hours): a form older than the session can
-- no longer be submitted, because its CSRF check fails first.
--
-- Safe to run more than once. Creates one new table and one index; changes
-- no existing table, column or row.
--
-- To undo:  DROP TABLE IF EXISTS submission_tokens;
-- ===========================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS submission_tokens (
    -- The token comes from the form. Python generates it (uuid4), so this
    -- column needs no default and no extension.
    token    uuid        PRIMARY KEY,
    -- Which route consumed the token. Kept for debugging only.
    endpoint text,
    used_at  timestamptz NOT NULL DEFAULT now()
);

-- Supports the periodic cleanup delete.
CREATE INDEX IF NOT EXISTS ix_submission_tokens_used_at
    ON submission_tokens (used_at);

COMMENT ON TABLE submission_tokens IS
    'Used one time form tokens. Blocks double submit. Rows older than 48 hours are deleted.';

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification: run this after the script and check the output.
-- Expected: 3 columns (token uuid NOT NULL, endpoint text, used_at timestamptz
-- NOT NULL), plus 2 indexes (primary key + ix_submission_tokens_used_at).
-- ---------------------------------------------------------------------------
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'submission_tokens'
ORDER BY ordinal_position;

SELECT indexname FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'submission_tokens'
ORDER BY indexname;
