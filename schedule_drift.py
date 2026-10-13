"""What expense_schedule SHOULD say, worked out from the payments.

expense_schedule.paid_amount / is_paid is derived data. The truth is in
supplier_payments. reconcile_supplier_payments() rebuilds the schedule from
it, and every route that changes a payment or a check status calls it. When
something skips that step, the stored schedule goes stale and a page can show
a wrong "paid" figure.

This module only READS. It repeats the rule of reconcile in SQL:

* a payment is valid when it is cash, or its outgoing check is 'odendi'
* the valid total is spread over the installments of the expense, oldest
  due_date first (ties by id), and an installment never takes more than its
  own amount

tests/test_schedule_drift.py runs reconcile and this rule side by side, so the
two cannot drift apart without a test failing.

Read only: never call a reconcile function from here.
"""
from decimal import Decimal

# {scope} is a WHERE fragment on the expenses table, written in this file
# (never user input). Values always go in as %s parameters.
_EXPECTED_CTE = """
WITH scoped AS (
    SELECT e.id FROM expenses e WHERE {scope}
), valid AS (
    SELECT sp.expense_id, SUM(sp.amount) AS valid_paid
    FROM supplier_payments sp
    JOIN scoped s ON s.id = sp.expense_id
    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
    WHERE sp.payment_method = 'nakit' OR oc.status = 'odendi'
    GROUP BY sp.expense_id
), inst AS (
    SELECT es.id, es.expense_id, es.due_date, es.amount, es.paid_amount,
           COALESCE(es.is_paid, FALSE) AS is_paid,
           COALESCE(v.valid_paid, 0) AS valid_paid,
           SUM(es.amount) OVER (PARTITION BY es.expense_id
                                ORDER BY es.due_date, es.id) - es.amount
               AS before_amount
    FROM expense_schedule es
    JOIN scoped s ON s.id = es.expense_id
    LEFT JOIN valid v ON v.expense_id = es.expense_id
), expected AS (
    SELECT id, expense_id, due_date, amount, paid_amount, is_paid, valid_paid,
           LEAST(amount, GREATEST(0, valid_paid - before_amount))
               AS expected_paid,
           (valid_paid - before_amount > 0
            AND LEAST(amount, GREATEST(0, valid_paid - before_amount)) >= amount)
               AS expected_is_paid
    FROM inst
)
"""

_OPEN_INSTALLMENTS_SQL = _EXPECTED_CTE.format(
    scope="e.supplier_id = %s AND e.project_id = %s") + """
SELECT id, expense_id, due_date, amount - expected_paid AS remaining
FROM expected
WHERE amount - expected_paid > 0
ORDER BY due_date, id
"""


def open_installments(cur, supplier_id, project_id):
    """The installments of one supplier in one project that are still open.

    Returns [(installment_id, expense_id, due_date, remaining)], oldest due
    date first. "Open" is decided from the real payments, not from the stored
    paid_amount, so a stale schedule cannot hide a debt or invent one.
    """
    cur.execute(_OPEN_INSTALLMENTS_SQL, (supplier_id, project_id))
    return cur.fetchall()


def allocate_payment(open_rows, amount):
    """Spread a payment over open installments, in the order given.

    Returns ({expense_id: part}, surplus). The dict keeps the order in which
    each expense was first reached. surplus is what is left when the payment is
    bigger than all the open debt (it is 0 otherwise).
    """
    per_expense = {}
    left = Decimal(amount)
    for _installment_id, expense_id, _due_date, remaining in open_rows:
        if left <= 0:
            break
        part = min(left, remaining)
        per_expense[expense_id] = per_expense.get(expense_id, Decimal(0)) + part
        left -= part
    return per_expense, left


_DRIFT_SQL = _EXPECTED_CTE + """
SELECT x.expense_id, e.title, x.schedule_paid, x.real_paid, x.bad_installments
FROM (
    SELECT expense_id,
           SUM(paid_amount)     AS schedule_paid,
           SUM(expected_paid)   AS real_paid,
           COUNT(*) FILTER (WHERE paid_amount <> expected_paid
                               OR is_paid <> expected_is_paid)
               AS bad_installments
    FROM expected
    GROUP BY expense_id
) x
JOIN expenses e ON e.id = x.expense_id
WHERE x.bad_installments > 0
ORDER BY ABS(x.schedule_paid - x.real_paid) DESC, x.expense_id
"""


def find_schedule_drift(cur, project_id=None):
    """Expenses whose stored schedule disagrees with their payments.

    One SELECT, nothing is written. With project_id it looks at one project,
    without it at every project. Returns a list of dicts, the biggest
    difference first:

        expense_id, title,
        schedule_paid      what expense_schedule says was paid
        real_paid          what it should say (valid payments, capped at the plan)
        diff               schedule_paid - real_paid
        bad_installments   installments with a wrong paid_amount or is_paid

    diff can be 0 while bad_installments is not (right total, wrong place).
    """
    if project_id is None:
        sql, params = _DRIFT_SQL.format(scope="TRUE"), ()
    else:
        sql, params = _DRIFT_SQL.format(scope="e.project_id = %s"), (project_id,)
    cur.execute(sql, params)
    return [
        {'expense_id': expense_id, 'title': title,
         'schedule_paid': schedule_paid, 'real_paid': real_paid,
         'diff': schedule_paid - real_paid, 'bad_installments': bad}
        for expense_id, title, schedule_paid, real_paid, bad in cur.fetchall()
    ]
