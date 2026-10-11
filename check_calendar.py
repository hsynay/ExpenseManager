"""Incoming and outgoing checks in one list, sorted by due date.

Read only. Used by the /check-calendar screen and the daily summary e-mail.

A check that is already cleared (tahsil_edildi / odendi) is finished and is
not listed. Checks still waiting (portfoyde / verildi) are expected money on
their due date. Bounced checks (karsiliksiz) are listed apart and are never
added to any total.
"""
import calendar
from datetime import timedelta
from decimal import Decimal

ZERO = Decimal(0)
WAITING = ('portfoyde', 'verildi')

COLUMNS = ('kind', 'id', 'due_date', 'amount', 'status', 'party', 'bank_name',
           'check_number', 'project_name')

# kind is 'in' (received from a customer) or 'out' (given to a supplier).
# The project of a received check is the project of the payment it was given
# for. An outgoing check uses the project of the expense it paid, or the
# supplier's own project when it has no payment yet. A check with neither has
# no project (shown as a dash). The subqueries run inside this one statement.
CALENDAR_SQL = """
    SELECT 'in' AS kind, c.id, c.due_date, c.amount, c.status,
           TRIM(COALESCE(cus.first_name, '') || ' ' || COALESCE(cus.last_name, '')) AS party,
           c.bank_name, c.check_number,
           (SELECT pr.name
            FROM payments p
            JOIN flats f ON f.id = p.flat_id
            JOIN projects pr ON pr.id = f.project_id
            WHERE p.check_id = c.id
            ORDER BY p.id LIMIT 1) AS project_name
    FROM checks c
    LEFT JOIN customers cus ON cus.id = c.customer_id
    WHERE c.status IN ('portfoyde', 'karsiliksiz')

    UNION ALL

    SELECT 'out', oc.id, oc.due_date, oc.amount, oc.status,
           COALESCE(s.name, ''),
           oc.bank_name, oc.check_number,
           COALESCE(
               (SELECT pr.name
                FROM supplier_payments sp
                JOIN expenses e ON e.id = sp.expense_id
                JOIN projects pr ON pr.id = e.project_id
                WHERE sp.check_id = oc.id
                ORDER BY sp.id LIMIT 1),
               (SELECT pr.name FROM projects pr WHERE pr.id = s.project_id))
    FROM outgoing_checks oc
    LEFT JOIN suppliers s ON s.id = oc.supplier_id
    WHERE oc.status IN ('verildi', 'karsiliksiz')

    ORDER BY due_date, kind, id
"""


def load_calendar_checks(cur):
    """Every waiting or bounced check as a dict, in due date order. One query."""
    cur.execute(CALENDAR_SQL)
    return [dict(zip(COLUMNS, row)) for row in cur.fetchall()]


def group_checks(checks, today):
    """Split the list into the calendar sections and the four box totals.

    Sections (for waiting checks only):
      overdue  due before today
      week     today up to this Sunday
      month    after this Sunday up to the end of this month
      later    everything after both
    Bounced checks go to `bounced` and are left out of every total.
    """
    week_end = today + timedelta(days=6 - today.weekday())
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    sections = {'overdue': [], 'week': [], 'month': [], 'later': []}
    bounced = []
    totals = {'overdue_in': ZERO, 'overdue_out': ZERO,
              'week_in': ZERO, 'week_out': ZERO}

    for check in checks:
        if check['status'] not in WAITING:
            bounced.append(check)
            continue
        due = check['due_date']
        if due < today:
            name = 'overdue'
        elif due <= week_end:
            name = 'week'
        elif due <= month_end:          # already after this Sunday
            name = 'month'
        else:
            name = 'later'
        sections[name].append(check)
        side = 'in' if check['kind'] == 'in' else 'out'
        if name in ('overdue', 'week'):
            totals['%s_%s' % (name, side)] += check['amount']

    return {'sections': sections, 'bounced': bounced, 'totals': totals,
            'week_end': week_end, 'month_end': month_end}
