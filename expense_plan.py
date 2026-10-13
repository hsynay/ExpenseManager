"""The plan of an expense: one rule, used by every screen that adds it up.

    plan of an expense = the sum of its installments (expense_schedule)
                         when it has any installments at all,
                         otherwise the amount of the expense itself.

Why the second half exists: an expense that was entered with no installments
has nothing in expense_schedule, so a plain SUM over the schedule skips it and
the debt it stands for disappears from "what is still to pay" (and the
forecast profit looks better than it is). The amount of the expense is the only
figure we have for it, so it is the plan.

Three screens add up the plan and they must agree:

* the cooperative report   (routes/reports.py, "Kalan Taahhüt")
* the profitability screen (profitability.py, "Taahhüt")
* project_overview         (routes/projects.py, unpaid expense)

tests/test_plan_rule.py runs the same scenario through all three and compares.

How to use the two fragments: the query must have the expenses table as `e`.

    SELECT SUM({PLAN_AMOUNT}) FROM expenses e {PLAN_JOIN} WHERE ...

Read only SQL text, nothing is executed here.
"""

# One row per expense that has installments, with the sum of them.
PLAN_JOIN = """
    LEFT JOIN (SELECT expense_id, SUM(amount) AS plan_total
               FROM expense_schedule
               GROUP BY expense_id) pl ON pl.expense_id = e.id
"""

# The plan of the expense `e`. Needs PLAN_JOIN (alias `pl`) in the same query.
PLAN_AMOUNT = "COALESCE(pl.plan_total, e.amount)"
