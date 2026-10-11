"""Aging of what customers owe late: open installments and portfolio checks
whose due date has passed, in 0-30 / 31-60 / 61-90 / 90+ day buckets.

Read only. Used by the /overdue screen.

Check rule: a check in the portfolio does not close a debt. So the open
installment stays in the installment table at its full remaining amount, and
the same check also shows in the check table. The two tables are two views of
the same late money and must never be added together.
"""
from decimal import Decimal

ZERO = Decimal(0)

BUCKETS = (
    ('b0_30', '0-30 gün'),
    ('b31_60', '31-60 gün'),
    ('b61_90', '61-90 gün'),
    ('b90_plus', '90+ gün'),
)
BUCKET_KEYS = tuple(key for key, _ in BUCKETS)

# `days` is how many days late. Everything is grouped in SQL, one row per
# customer, so the number of customers does not change the number of queries.
# The first bucket starts at 0 although a row is only listed from 1 day late.
_AGING_COLUMNS = """
       COALESCE(SUM(x.rem) FILTER (WHERE x.days BETWEEN 0 AND 30), 0) AS b0_30,
       COALESCE(SUM(x.rem) FILTER (WHERE x.days BETWEEN 31 AND 60), 0) AS b31_60,
       COALESCE(SUM(x.rem) FILTER (WHERE x.days BETWEEN 61 AND 90), 0) AS b61_90,
       COALESCE(SUM(x.rem) FILTER (WHERE x.days > 90), 0) AS b90_plus,
       SUM(x.rem) AS total,
       MAX(x.days) AS oldest_days,
       COUNT(*) AS items
"""

_NAME = """COALESCE(NULLIF(TRIM(COALESCE(cus.first_name, '') || ' ' ||
                              COALESCE(cus.last_name, '')), ''),
                    'Müşteri belirtilmemiş')"""

INSTALLMENTS_SQL = """
    SELECT cus.id AS customer_id, {name} AS name,
           {columns},
           COUNT(DISTINCT x.flat_id) AS flat_count,
           MIN(x.flat_id) AS flat_id,
           MIN(x.project_id) AS project_id
    FROM (
        SELECT s.flat_id, f.project_id, f.owner_id,
               s.amount - COALESCE(s.paid_amount, 0) AS rem,
               (%(today)s::date - s.due_date) AS days
        FROM installment_schedule s
        JOIN flats f ON f.id = s.flat_id
        WHERE s.is_paid = FALSE
          AND s.due_date < %(today)s
          AND s.amount - COALESCE(s.paid_amount, 0) > 0
    ) x
    JOIN customers cus ON cus.id = x.owner_id
    GROUP BY cus.id, name
    ORDER BY oldest_days DESC, total DESC, name
""".format(name=_NAME, columns=_AGING_COLUMNS)

CHECKS_SQL = """
    SELECT cus.id AS customer_id, {name} AS name,
           {columns}
    FROM (
        SELECT c.customer_id, c.amount AS rem,
               (%(today)s::date - c.due_date) AS days
        FROM checks c
        WHERE c.status = 'portfoyde' AND c.due_date < %(today)s
    ) x
    LEFT JOIN customers cus ON cus.id = x.customer_id
    GROUP BY cus.id, name
    ORDER BY oldest_days DESC, total DESC, name
""".format(name=_NAME, columns=_AGING_COLUMNS)


def _rows(cur, sql, today):
    cur.execute(sql, {'today': today})
    names = [col[0] for col in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def load_overdue_installments(cur, today):
    """Late installments only, one row per customer, most late first."""
    return _rows(cur, INSTALLMENTS_SQL, today)


def load_overdue(cur, today):
    """(installment rows, check rows), each one row per customer, most late
    first. Two queries in all."""
    return load_overdue_installments(cur, today), _rows(cur, CHECKS_SQL, today)


def bucket_totals(rows):
    """Column totals of a table: each bucket and the grand total."""
    totals = {key: sum((r[key] for r in rows), ZERO) for key in BUCKET_KEYS}
    totals['total'] = sum((r['total'] for r in rows), ZERO)
    return totals
