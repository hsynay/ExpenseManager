-- expenses_without_plan_check.sql
--
-- READ ONLY. One SELECT. It writes nothing, so it is safe on the live DB.
--
-- Why: the cooperative report builds the expense side from expense_schedule
-- (the plan) and supplier_payments (the payments). An expense that has no plan
-- rows is still listed and still counted as owed, but at expenses.amount
-- (a fallback). One whose plan does not add up to expenses.amount is counted at
-- the plan figure. Payments are always counted as paid. This query lists such
-- expenses so they can be looked at: for them the report shows a figure that
-- comes from a fallback or from a plan that disagrees with the amount.
--
-- Columns
--   gider_tutari      expenses.amount
--   plan_toplami      SUM of the installment amounts (0 when there are none)
--   fark              gider_tutari - plan_toplami
--   odeme_satiri      number of supplier_payments rows of the expense
--   gecerli_odeme     payments that count (cash, or check 'odendi')
--   eski_yontem       expenses.payment_method (an old column, not used by the
--                     report any more; shown only to recognise old rows)
--   eski_cek_id       expenses.outgoing_check_id (same: old column)
--   neden             'plan yok' or 'plan tutarla uyusmuyor'
--
-- Biggest difference first.

WITH plan AS (
    SELECT expense_id, COUNT(*) AS rows_count, SUM(amount) AS plan_total
    FROM expense_schedule
    GROUP BY expense_id
), pay AS (
    SELECT sp.expense_id,
           COUNT(*) AS pay_rows,
           COALESCE(SUM(sp.amount) FILTER (
               WHERE sp.payment_method = 'nakit' OR oc.status = 'odendi'), 0)
               AS valid_paid
    FROM supplier_payments sp
    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
    GROUP BY sp.expense_id
)
SELECT e.id AS gider_id,
       e.title AS baslik,
       p.name AS proje_adi,
       p.project_type AS proje_tipi,
       e.amount AS gider_tutari,
       COALESCE(pl.plan_total, 0) AS plan_toplami,
       e.amount - COALESCE(pl.plan_total, 0) AS fark,
       COALESCE(py.pay_rows, 0) AS odeme_satiri,
       COALESCE(py.valid_paid, 0) AS gecerli_odeme,
       e.payment_method AS eski_yontem,
       e.outgoing_check_id AS eski_cek_id,
       CASE WHEN pl.expense_id IS NULL THEN 'plan yok'
            ELSE 'plan tutarla uyusmuyor' END AS neden
FROM expenses e
LEFT JOIN projects p ON p.id = e.project_id
LEFT JOIN plan pl ON pl.expense_id = e.id
LEFT JOIN pay py ON py.expense_id = e.id
WHERE pl.expense_id IS NULL
   OR ABS(e.amount - pl.plan_total) > 0.005
ORDER BY ABS(e.amount - COALESCE(pl.plan_total, 0)) DESC, e.id;
