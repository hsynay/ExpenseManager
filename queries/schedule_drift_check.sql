-- schedule_drift_check.sql
--
-- READ ONLY. One SELECT. It writes nothing, so it is safe on the live DB.
--
-- Question it answers: where does expense_schedule disagree with what the
-- payments really say?
--
-- expense_schedule.paid_amount / is_paid is a derived table. The truth is in
-- supplier_payments. reconcile_supplier_payments() rebuilds the schedule from
-- it with one rule:
--   * a payment is valid when it is cash, or its outgoing check is 'odendi'
--   * the valid total is spread over the installments, oldest due_date first
--     (ties by id), and an installment never takes more than its own amount
-- This query repeats the same rule and lists every expense whose stored
-- schedule is different from the result.
--
-- Columns
--   schedule_odenen    what the stored schedule says was paid (SUM paid_amount)
--   gercek_odenen      what the schedule SHOULD say (valid payments, capped at
--                      the plan, exactly what reconcile would write)
--   fark               schedule_odenen - gercek_odenen
--                      + : the schedule shows more paid than really was
--                      - : the schedule shows less paid than really was
--   uyusmayan_taksit   installments whose paid_amount or is_paid is wrong.
--                      fark can be 0 while this is not 0 (right total, wrong
--                      installments), so the expense is still listed.
--   plan_toplami       SUM of the installment amounts
--   gecerli_odeme      valid payments, NOT capped. If it is bigger than
--                      plan_toplami the expense is overpaid. Overpayment alone
--                      is not drift: the schedule is capped on purpose.
--
-- Sorted by the size of the difference, biggest first. The last row is the
-- total (absolute values, so + and - do not cancel each other out).

WITH valid AS (
    SELECT sp.expense_id, SUM(sp.amount) AS valid_paid
    FROM supplier_payments sp
    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
    WHERE sp.payment_method = 'nakit' OR oc.status = 'odendi'
    GROUP BY sp.expense_id
), inst AS (
    SELECT es.expense_id,
           es.amount,
           es.paid_amount,
           es.is_paid,
           COALESCE(v.valid_paid, 0) AS valid_paid,
           SUM(es.amount) OVER (PARTITION BY es.expense_id
                                ORDER BY es.due_date, es.id) - es.amount
               AS before_amount
    FROM expense_schedule es
    LEFT JOIN valid v ON v.expense_id = es.expense_id
), expected AS (
    SELECT expense_id,
           amount,
           paid_amount AS stored_paid,
           COALESCE(is_paid, FALSE) AS stored_is_paid,
           valid_paid,
           LEAST(amount, GREATEST(0, valid_paid - before_amount))
               AS expected_paid,
           (valid_paid - before_amount > 0
            AND LEAST(amount, GREATEST(0, valid_paid - before_amount)) >= amount)
               AS expected_is_paid
    FROM inst
), per_expense AS (
    SELECT expense_id,
           SUM(stored_paid)   AS schedule_paid,
           SUM(expected_paid) AS real_paid,
           SUM(amount)        AS plan_total,
           MAX(valid_paid)    AS valid_total,
           COUNT(*) FILTER (WHERE stored_paid <> expected_paid
                               OR stored_is_paid <> expected_is_paid)
               AS bad_installments
    FROM expected
    GROUP BY expense_id
), drift AS (
    SELECT e.id AS expense_id,
           e.title,
           p.name AS project_name,
           p.project_type,
           pe.schedule_paid,
           pe.real_paid,
           pe.schedule_paid - pe.real_paid AS diff,
           pe.bad_installments,
           pe.plan_total,
           pe.valid_total
    FROM per_expense pe
    JOIN expenses e ON e.id = pe.expense_id
    LEFT JOIN projects p ON p.id = e.project_id
    WHERE pe.bad_installments > 0
)
SELECT gider_id, baslik, proje_adi, proje_tipi,
       schedule_odenen, gercek_odenen, fark,
       uyusmayan_taksit, plan_toplami, gecerli_odeme
FROM (
    SELECT 0 AS sira,
           d.expense_id AS gider_id,
           d.title AS baslik,
           d.project_name AS proje_adi,
           d.project_type AS proje_tipi,
           d.schedule_paid AS schedule_odenen,
           d.real_paid AS gercek_odenen,
           d.diff AS fark,
           d.bad_installments AS uyusmayan_taksit,
           d.plan_total AS plan_toplami,
           d.valid_total AS gecerli_odeme
    FROM drift d
    UNION ALL
    SELECT 1,
           NULL,
           'TOPLAM SAPMA (' || COUNT(*) || ' gider, mutlak deger)',
           NULL,
           NULL,
           COALESCE(SUM(schedule_paid), 0),
           COALESCE(SUM(real_paid), 0),
           COALESCE(SUM(ABS(diff)), 0),
           COALESCE(SUM(bad_installments), 0),
           NULL,
           NULL
    FROM drift
) t
ORDER BY sira, ABS(fark) DESC NULLS LAST, gider_id;
