-- ===========================================================================
-- 003_constraints_and_precision
-- ===========================================================================
-- Two small pieces of schema tightening, kept in one file because both are
-- quick and both are about the same thing: stopping a value from entering
-- the database in a shape the money code cannot handle.
--
--   PART 1  CHECK constraints on the two check status columns
--   PART 2  fixed precision on the last two loose numeric columns
--
-- Each part has its own undo block at the bottom. Running the file twice is
-- safe: part 1 drops the constraint before adding it, part 2 is a type change
-- to what the column may already be.
--
-- ---------------------------------------------------------------------------
-- DATA WAS CHECKED FIRST — EVERYTHING CONFORMS
-- ---------------------------------------------------------------------------
-- Run on the test database before writing this file:
--
--   checks.status            tahsil_edildi 3, karsiliksiz 3, portfoyde 2
--                            NULL: 0, unexpected values: 0
--   outgoing_checks.status   odendi 21, verildi 12
--                            NULL: 0, unexpected values: 0
--
--   flats.total_price            39 rows, 20 NULL, largest 3.700.000,
--                                no value with more than 2 decimals
--   installment_schedule.amount  86 rows, 0 NULL, largest 1.850.000,
--                                no value with more than 2 decimals
--
-- So no row has to be repaired first and the type change loses nothing.
--
-- The same two SELECTs are at the bottom of this file under VERIFY BEFORE
-- RUNNING. Run them on live before applying, because live has rows the test
-- database does not.
--
-- ===========================================================================
-- PART 1 — check status constraints
-- ===========================================================================
-- Why this matters: update_check_status (app.py:2039) takes new_status
-- straight from the form and writes it with no whitelist on the server side.
-- A typo or a tampered request stores a status no query recognises, and the
-- check then silently counts for nothing anywhere: not as paid, not as
-- portfolio, not as bounced. The money simply disappears from every total
-- without an error.
--
-- The templates only ever offer the valid values, so normal use is not
-- affected. With this constraint a bad value raises instead, the route's
-- error branch catches it and the user gets the "could not update" message.

ALTER TABLE checks
    DROP CONSTRAINT IF EXISTS checks_status_check;

ALTER TABLE checks
    ADD CONSTRAINT checks_status_check
    CHECK (status IN ('portfoyde', 'tahsil_edildi', 'karsiliksiz'));


ALTER TABLE outgoing_checks
    DROP CONSTRAINT IF EXISTS outgoing_checks_status_check;

ALTER TABLE outgoing_checks
    ADD CONSTRAINT outgoing_checks_status_check
    CHECK (status IN ('verildi', 'odendi', 'karsiliksiz'));


-- ===========================================================================
-- PART 2 — precision
-- ===========================================================================
-- Every money column is already NUMERIC(12,2) except these two, which are
-- plain numeric with no limit. Unlimited numeric accepts any number of
-- decimals, so a value like 1000.005 can be stored and then shows up rounded
-- in one place and unrounded in another.
--
-- NOTE: CLAUDE.md item 14 also lists expense_schedule.amount. It is already
-- NUMERIC(12,2); the note is out of date. Only these two need changing.
--
-- ALTER TYPE rewrites the table and holds an exclusive lock while it runs.
-- Both tables are small, so this is a matter of milliseconds.

ALTER TABLE flats
    ALTER COLUMN total_price TYPE NUMERIC(12,2);

ALTER TABLE installment_schedule
    ALTER COLUMN amount TYPE NUMERIC(12,2);


-- ===========================================================================
-- VERIFY BEFORE RUNNING (on live)
-- ===========================================================================
-- Both of these must return zero rows. If either returns anything, stop and
-- fix those rows first.
--
-- 1) Status values the constraints would reject:
--
--   SELECT 'checks' AS tablo, id, status FROM checks
--   WHERE status IS NULL
--      OR status NOT IN ('portfoyde', 'tahsil_edildi', 'karsiliksiz')
--   UNION ALL
--   SELECT 'outgoing_checks', id, status FROM outgoing_checks
--   WHERE status IS NULL
--      OR status NOT IN ('verildi', 'odendi', 'karsiliksiz');
--
-- 2) Amounts that would not survive NUMERIC(12,2):
--
--   SELECT 'flats.total_price' AS kolon, id, total_price::text FROM flats
--   WHERE total_price IS NOT NULL
--     AND (ABS(total_price) >= 10000000000 OR scale(total_price) > 2)
--   UNION ALL
--   SELECT 'installment_schedule.amount', id, amount::text
--   FROM installment_schedule
--   WHERE ABS(amount) >= 10000000000 OR scale(amount) > 2;
--
-- A row with more than two decimals is not blocked by the change; it would
-- be rounded. The query above is there so you see it happen instead of
-- finding out later.
--
-- ===========================================================================
-- AFTER RUNNING
-- ===========================================================================
-- Smoke test the app, then check that both constraints are present:
--
--   SELECT conrelid::regclass AS tablo, conname
--   FROM pg_constraint
--   WHERE contype = 'c' AND conname LIKE '%status_check%';
--
-- ===========================================================================
-- UNDO
-- ===========================================================================
-- Part 1:
--   ALTER TABLE checks DROP CONSTRAINT IF EXISTS checks_status_check;
--   ALTER TABLE outgoing_checks
--       DROP CONSTRAINT IF EXISTS outgoing_checks_status_check;
--
-- Part 2 (back to unlimited numeric):
--   ALTER TABLE flats ALTER COLUMN total_price TYPE numeric;
--   ALTER TABLE installment_schedule ALTER COLUMN amount TYPE numeric;
--
-- ===========================================================================
-- WHY ONLY THESE TWO COLUMNS
-- ===========================================================================
-- The other value columns are already protected. The database has five CHECK
-- constraints today:
--
--   payments.payment_method            IN ('nakit', 'çek')
--   supplier_payments.payment_method   IN ('nakit', 'çek')
--   expenses.payment_method            IN ('nakit', 'çek')
--   projects.project_type              IN ('normal', 'cooperative')
--   flats.type                         IN ('residential', 'commercial')
--
-- checks.status and outgoing_checks.status were the only two left without
-- one, which is exactly the pair the check rule depends on. After this file
-- there are seven, and every column the money code branches on is covered.
