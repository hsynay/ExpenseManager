-- One off repair for dates whose year is out of range (e.g. 22026-01-01).
--
-- Why these rows are a problem:
--   Postgres stores a five digit year without complaining, but psycopg2
--   cannot convert it into a Python date (the limit is year 9999). One such
--   row makes every page that reads the table fail with a 500.
--
-- The source is closed in the application (parse_form_date plus a max bound
-- on the date inputs), so this file only cleans up rows written earlier.
--
-- HOW TO USE
--   1. Run STEP 1 and look at the result. It is read only.
--   2. If STEP 1 returns nothing, there is nothing to do. Stop here.
--   3. If it returns rows, note the ids. STEP 2 overwrites the bad date with
--      today's date, so the original value is lost. Fix the real dates by
--      hand afterwards using the list from STEP 1.
--
-- Dates are cast to text on purpose: a client that maps them to a native
-- date type would fail on exactly the rows we are looking for.


-- ============================ STEP 1: report ============================

SELECT 'petty_cash_expenses' AS tablo, 'expense_date' AS kolon, id, expense_date::text AS deger
FROM petty_cash_expenses WHERE EXTRACT(YEAR FROM expense_date) > 3000
UNION ALL
SELECT 'expense_schedule', 'due_date', id, due_date::text
FROM expense_schedule WHERE EXTRACT(YEAR FROM due_date) > 3000
UNION ALL
SELECT 'expenses', 'expense_date', id, expense_date::text
FROM expenses WHERE EXTRACT(YEAR FROM expense_date) > 3000
UNION ALL
SELECT 'payments', 'payment_date', id, payment_date::text
FROM payments WHERE EXTRACT(YEAR FROM payment_date) > 3000
UNION ALL
SELECT 'supplier_payments', 'payment_date', id, payment_date::text
FROM supplier_payments WHERE EXTRACT(YEAR FROM payment_date) > 3000
UNION ALL
SELECT 'outgoing_checks', 'due_date', id, due_date::text
FROM outgoing_checks WHERE EXTRACT(YEAR FROM due_date) > 3000
UNION ALL
SELECT 'checks', 'due_date', id, due_date::text
FROM checks WHERE EXTRACT(YEAR FROM due_date) > 3000
UNION ALL
SELECT 'installment_schedule', 'due_date', id, due_date::text
FROM installment_schedule WHERE EXTRACT(YEAR FROM due_date) > 3000
ORDER BY tablo, id;


-- ============================ STEP 2: repair ============================
-- Run this only after STEP 1 and only if it returned rows.
-- Remove the ROLLBACK and put COMMIT in its place once the counts look right.

BEGIN;

UPDATE petty_cash_expenses SET expense_date = CURRENT_DATE WHERE EXTRACT(YEAR FROM expense_date) > 3000;
UPDATE expense_schedule    SET due_date     = CURRENT_DATE WHERE EXTRACT(YEAR FROM due_date) > 3000;
UPDATE expenses            SET expense_date = CURRENT_DATE WHERE EXTRACT(YEAR FROM expense_date) > 3000;
UPDATE payments            SET payment_date = CURRENT_DATE WHERE EXTRACT(YEAR FROM payment_date) > 3000;
UPDATE supplier_payments   SET payment_date = CURRENT_DATE WHERE EXTRACT(YEAR FROM payment_date) > 3000;
UPDATE outgoing_checks     SET due_date     = CURRENT_DATE WHERE EXTRACT(YEAR FROM due_date) > 3000;
UPDATE checks              SET due_date     = CURRENT_DATE WHERE EXTRACT(YEAR FROM due_date) > 3000;
UPDATE installment_schedule SET due_date    = CURRENT_DATE WHERE EXTRACT(YEAR FROM due_date) > 3000;

ROLLBACK;
