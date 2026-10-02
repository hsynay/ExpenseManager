-- ===========================================================================
-- 002_indexes
-- ===========================================================================
-- Indexes on the foreign key columns the application actually filters and
-- joins on. Only audit_logs had any index before this.
--
-- Safe to run more than once (IF NOT EXISTS). Adds no column, changes no row.
-- Every statement is additive and can be undone with DROP INDEX; the drop
-- statements are at the bottom of this file.
--
-- ---------------------------------------------------------------------------
-- MEASUREMENT
-- ---------------------------------------------------------------------------
-- The test database holds a few hundred rows. At that size Postgres always
-- picks a sequential scan and no index can show a difference, so the numbers
-- below were taken on a synthetic data set inside a single transaction that
-- was rolled back afterwards: 4.000 flats, 60.000 payments, 120.000
-- installments, 40.000 expense installments, 40.000 supplier payments, 8.000
-- checks of each kind. Nothing was committed and no index was kept.
--
-- Each query was run twice before and twice after, keeping the faster run,
-- with ANALYZE in between. EXPLAIN (ANALYZE) execution time in milliseconds:
--
--   query                                      before    after    gain
--   ------------------------------------------------------------------
--   payments behind one check                    5.94     0.04   160x
--   installments of a flat                       8.53     0.09    97x
--   supplier payments behind one check           4.18     0.05    80x
--   payments of a flat                           4.75     0.08    61x
--   schedule of an expense                       2.87     0.05    55x
--   reconcile_customer_payments core             4.75     0.09    56x
--   reconcile_supplier_payments core             3.27     0.06    54x
--   checks of a customer                         0.68     0.04    19x
--   outgoing checks of a supplier                0.68     0.04    17x
--   expenses of a supplier                       0.70     0.07    10x
--   flats of a customer                          0.43     0.06     7x
--   overdue installments of a project           13.77     2.17     6x
--   petty cash of a project                      1.74     0.42     4x
--   flats by project                             0.37     0.11     3.5x
--   project income with the check rule          12.07     3.49     3.5x
--   expenses of a project                        0.50     0.16     3.1x
--   project expense paid with the check rule     9.30     3.10     3.0x
--   incoming checks due soon                     1.08     0.38     2.9x
--   outgoing checks due soon                     1.01     0.39     2.6x
--
-- The two reconcile queries are the ones that matter most: they run on every
-- payment, every check status change and every plan edit.
--
-- Live data is much smaller than the benchmark today, so the gain there will
-- be smaller. These indexes are cheap and the tables only grow.
--
-- ---------------------------------------------------------------------------
-- NOT INCLUDED, ON PURPOSE
-- ---------------------------------------------------------------------------
-- Six candidates were left out after measuring:
--
--   suppliers.project_id
--       Measured 1.6x, but on 0.08 ms. The table has one row per supplier per
--       project and stays small. Not worth the write cost.
--
--   supplier_payments.supplier_id
--       Measured 86x, but no query uses it. The column is only ever written,
--       never filtered or joined on. An index nothing reads is pure cost.
--       Revisit if a "payments by supplier" screen is ever added.
--
--   expenses.outgoing_check_id
--       The only query touching it (app.py:1664) asks IS NOT NULL together
--       with a project filter, which a plain index on this column cannot
--       serve. ix_expenses_project_id already covers that query.
--
--   cooperative_monthly_summary.project_id
--       Already covered. The existing unique constraint on
--       (project_id, month_year) indexes project_id as its first column.
--
--   installment_schedule (due_date) WHERE is_paid = FALSE
--       Measured in isolation and it changes nothing. The overdue query for
--       one project runs in 13.77 ms with no index and 2.17 ms with the two
--       foreign key indexes; adding this partial index on top gives 2.16 ms.
--       The whole gain comes from the foreign keys.
--
--   expense_schedule (due_date) WHERE is_paid = FALSE
--       Worse than no index: 7.47 ms before, 8.40 ms after. The dashboard
--       query has no project filter and about two thirds of the rows are
--       unpaid, so the partial index covers most of the table and a
--       sequential scan wins.
--
-- ---------------------------------------------------------------------------
-- RUNNING THIS
-- ---------------------------------------------------------------------------
-- Test database first, then a live backup, then live.
--
-- Plain CREATE INDEX takes a write lock on the table for as long as it runs.
-- On the current data volume that is milliseconds. If a table ever grows to
-- where that matters, run the same statements with CREATE INDEX CONCURRENTLY
-- instead, one at a time and outside a transaction block.
-- ===========================================================================


-- --- income side ----------------------------------------------------------

CREATE INDEX IF NOT EXISTS ix_flats_project_id
    ON flats (project_id);

CREATE INDEX IF NOT EXISTS ix_flats_owner_id
    ON flats (owner_id);

CREATE INDEX IF NOT EXISTS ix_payments_flat_id
    ON payments (flat_id);

CREATE INDEX IF NOT EXISTS ix_payments_check_id
    ON payments (check_id);

CREATE INDEX IF NOT EXISTS ix_installment_schedule_flat_id
    ON installment_schedule (flat_id);

CREATE INDEX IF NOT EXISTS ix_checks_customer_id
    ON checks (customer_id);


-- --- expense side ---------------------------------------------------------

CREATE INDEX IF NOT EXISTS ix_expenses_project_id
    ON expenses (project_id);

CREATE INDEX IF NOT EXISTS ix_expenses_supplier_id
    ON expenses (supplier_id);

CREATE INDEX IF NOT EXISTS ix_expense_schedule_expense_id
    ON expense_schedule (expense_id);

CREATE INDEX IF NOT EXISTS ix_supplier_payments_expense_id
    ON supplier_payments (expense_id);

CREATE INDEX IF NOT EXISTS ix_supplier_payments_check_id
    ON supplier_payments (check_id);

CREATE INDEX IF NOT EXISTS ix_outgoing_checks_supplier_id
    ON outgoing_checks (supplier_id);

CREATE INDEX IF NOT EXISTS ix_petty_cash_expenses_project_id
    ON petty_cash_expenses (project_id);


-- --- lists the dashboard builds ------------------------------------------

-- "Which checks fall due in the next 30 days" filters on status first.
CREATE INDEX IF NOT EXISTS ix_checks_status_due_date
    ON checks (status, due_date);

CREATE INDEX IF NOT EXISTS ix_outgoing_checks_status_due_date
    ON outgoing_checks (status, due_date);


-- ===========================================================================
-- AFTERWARDS
-- ===========================================================================
-- Refresh the planner statistics so the new indexes are used right away:
--
--   ANALYZE;
--
-- To check months later which of these actually get used:
--
--   SELECT relname, indexrelname, idx_scan
--   FROM pg_stat_user_indexes
--   WHERE schemaname = 'public' AND indexrelname LIKE 'ix_%'
--   ORDER BY idx_scan;
--
-- An index still at idx_scan = 0 after real use can be dropped.
--
-- ===========================================================================
-- UNDO
-- ===========================================================================
-- DROP INDEX IF EXISTS ix_flats_project_id;
-- DROP INDEX IF EXISTS ix_flats_owner_id;
-- DROP INDEX IF EXISTS ix_payments_flat_id;
-- DROP INDEX IF EXISTS ix_payments_check_id;
-- DROP INDEX IF EXISTS ix_installment_schedule_flat_id;
-- DROP INDEX IF EXISTS ix_checks_customer_id;
-- DROP INDEX IF EXISTS ix_expenses_project_id;
-- DROP INDEX IF EXISTS ix_expenses_supplier_id;
-- DROP INDEX IF EXISTS ix_expense_schedule_expense_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_expense_id;
-- DROP INDEX IF EXISTS ix_supplier_payments_check_id;
-- DROP INDEX IF EXISTS ix_outgoing_checks_supplier_id;
-- DROP INDEX IF EXISTS ix_petty_cash_expenses_project_id;
-- DROP INDEX IF EXISTS ix_checks_status_due_date;
-- DROP INDEX IF EXISTS ix_outgoing_checks_status_due_date;
