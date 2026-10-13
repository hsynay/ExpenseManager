"""Project profitability: what was sold, what came in, what was spent and what
is still to be paid.

Read only. Used by the /project/<id>/profitability screen. Three SELECT
queries, whatever the size of the project.

Rules (the same ones the money code uses everywhere else):

* Money counts only when it really moved. Cash counts. An incoming check
  counts when it is 'tahsil_edildi', an outgoing check when it is 'odendi'.
  A check in the portfolio ('portfoyde' / 'verildi') closes nothing, and a
  bounced one ('karsiliksiz') counts nowhere.
* Spent    = payments that really left (supplier_payments, the same rule as
  project_overview) + petty cash.
* Committed = everything the expense plan says we will pay, minus what was
  really paid. The plan of an expense is the sum of its installments, or the
  expense amount when it has no installments (expense_plan.py; the same rule
  as the cooperative report and project_overview). A check we handed over
  but that is not cashed yet is part of this number: it closed nothing, so
  it is still owed. It is shown as a
  separate line so the user sees how much of the commitment is already
  "on paper", but it must never be added on top of the commitment.
* Spent + Committed = the expense plan total + petty cash. The cooperative
  report shows the same two parts: "Bu ayki gider" is the paid part (same
  source and rule as Spent) and "Kalan Taahhüt" is the Committed part.

Why the reconcile functions are not called here: they UPDATE the schedule
tables, and this screen must only read. The validity rule inside them
(cash, or a check marked paid) is repeated in the SQL below, and the tests
run the real reconcile functions next to this module to keep the two in step.
The stored expense_schedule.paid_amount is not used on purpose: it caps a
payment at the plan, so an overpayment would disappear from it.
"""
from decimal import Decimal

from expense_plan import PLAN_AMOUNT, PLAN_JOIN

ZERO = Decimal(0)

PROJECT_SQL = "SELECT id, name, project_type FROM projects WHERE id = %s"

# One row. The flats of the project are read once per column, which is cheap
# at this size and keeps every number next to the rule that defines it.
INCOME_SQL = """
SELECT
    COUNT(*)                                            AS flat_count,
    COUNT(*) FILTER (WHERE f.owner_id IS NOT NULL)      AS sold_count,
    COALESCE(SUM(f.total_price)
             FILTER (WHERE f.owner_id IS NOT NULL), 0)  AS sold_total,
    (SELECT COALESCE(SUM(p.amount), 0)
       FROM payments p
       JOIN flats pf ON p.flat_id = pf.id
       LEFT JOIN checks c ON p.check_id = c.id
      WHERE pf.project_id = %s
        AND (p.payment_method = 'nakit' OR c.status = 'tahsil_edildi'))
                                                        AS collected
FROM flats f
WHERE f.project_id = %s
"""

# One row. `per_expense` has one row per expense, so an overpaid expense can
# be told apart from the rest.
EXPENSE_SQL = """
WITH paid AS (
    SELECT sp.expense_id,
           COALESCE(SUM(sp.amount)
                    FILTER (WHERE sp.payment_method = 'nakit'
                               OR oc.status = 'odendi'), 0) AS paid,
           COALESCE(SUM(sp.amount)
                    FILTER (WHERE oc.status = 'verildi'), 0) AS handed
    FROM supplier_payments sp
    JOIN expenses e ON sp.expense_id = e.id
    LEFT JOIN outgoing_checks oc ON sp.check_id = oc.id
    WHERE e.project_id = %s
    GROUP BY sp.expense_id
), per_expense AS (
    SELECT e.id,
           {plan_amount}           AS planned,
           COALESCE(pd.paid, 0)    AS paid,
           COALESCE(pd.handed, 0)  AS handed
    FROM expenses e
    {plan_join}
    LEFT JOIN paid pd ON pd.expense_id = e.id
    WHERE e.project_id = %s
)
SELECT
    COALESCE(SUM(planned), 0)                          AS planned_total,
    COALESCE(SUM(paid), 0)                             AS paid_total,
    COALESCE(SUM(handed), 0)                           AS handed_total,
    COALESCE(SUM(paid - planned) FILTER (WHERE paid > planned), 0)
                                                       AS overpaid_total,
    COUNT(*) FILTER (WHERE paid > planned)             AS overpaid_count,
    (SELECT COALESCE(SUM(amount), 0)
       FROM petty_cash_expenses WHERE project_id = %s) AS petty_total
FROM per_expense
""".format(plan_amount=PLAN_AMOUNT, plan_join=PLAN_JOIN)


def load_profitability(cur, project_id):
    """The profitability figures of one project, or None if it does not exist.

    Three queries: the project row, the income side, the expense side.
    """
    cur.execute(PROJECT_SQL, (project_id,))
    project = cur.fetchone()
    if project is None:
        return None

    cur.execute(INCOME_SQL, (project_id, project_id))
    flat_count, sold_count, sold_total, collected = cur.fetchone()

    # %s placeholders: paid, per_expense, petty total.
    cur.execute(EXPENSE_SQL, (project_id,) * 3)
    (planned_total, paid_total, handed_total, overpaid_total, overpaid_count,
     petty_total) = cur.fetchone()

    spent = paid_total + petty_total
    # Plan minus what really left. Handed over checks are inside this number.
    committed = planned_total - paid_total

    return {
        'project_id': project[0],
        'project_name': project[1],
        'project_type': project[2],
        # Income side
        'flat_count': flat_count,
        'sold_count': sold_count,
        'sold': sold_total,
        'collected': collected,
        'receivable': sold_total - collected,
        # Expense side
        'spent': spent,
        'spent_paid': paid_total,
        'spent_petty': petty_total,
        'committed': committed,
        'planned_total': planned_total,
        'handed_checks': handed_total,
        'overpaid_total': overpaid_total,
        'overpaid_count': overpaid_count,
        # Result
        'total_cost': spent + committed,
        'forecast_profit': sold_total - (spent + committed),
    }
