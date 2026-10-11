"""Expected money in and out, week by week. Read only: SELECT, never write.

Used by the /cashflow screen and by the daily summary e-mail.

Business rule (forecast exception to the check rule): a check that is still in
the portfolio (portfoyde / verildi) is money expected on its due date. A
bounced check (karsiliksiz) is never counted. This is a guess about the
future, it does not close any debt.

Double counting: a check that was handed over for an installment does NOT
reduce that installment's paid_amount until it is cleared. If both the open
installment and the check were counted, the same money would appear twice. So
for every flat the portfolio checks are taken off the flat's oldest open
installments (the same order reconcile_customer_payments uses when the check
clears) and the check itself is counted on its own due date. Expenses work the
same way with outgoing checks.
"""
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta

ZERO = Decimal(0)
MAX_MONTHS = 12
PERIOD_CHOICES = (3, 6, 12)

# What each source is. Incoming and outgoing are summed from these.
INCOMING_SOURCES = ('installment', 'check')
OUTGOING_SOURCES = ('expense', 'outgoing_check')

# One query for everything. Columns: due_date, source, amount (per day).
#
# installment        open installments of customers
#                    (amount - paid_amount) minus the portfolio checks the flat
#                    already holds, taken from its oldest installments first
# check              checks in the portfolio, on their due date
# expense            open installments of expenses, minus the outgoing checks
#                    already given for that expense, oldest installments first
# outgoing_check     outgoing checks not yet paid (verildi), on their due date
#
# With a project filter a check belongs to the project of the payment it was
# given for. A check with no payment row has no project, so it only shows when
# no project is picked (an outgoing check falls back to its supplier's
# project).
FLOWS_SQL = """
    WITH inst AS (
        SELECT s.flat_id, s.due_date,
               s.amount - COALESCE(s.paid_amount, 0) AS remaining,
               COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)) OVER (
                   PARTITION BY s.flat_id ORDER BY s.due_date, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS before_me
        FROM installment_schedule s
        JOIN flats f ON f.id = s.flat_id
        WHERE s.is_paid = FALSE
          AND (%(project_id)s IS NULL OR f.project_id = %(project_id)s)
    ),
    pending_in AS (
        SELECT p.flat_id, SUM(p.amount) AS pending
        FROM payments p
        JOIN checks c ON c.id = p.check_id
        WHERE c.status = 'portfoyde'
        GROUP BY p.flat_id
    ),
    exp AS (
        SELECT es.expense_id, es.due_date,
               es.amount - COALESCE(es.paid_amount, 0) AS remaining,
               COALESCE(SUM(es.amount - COALESCE(es.paid_amount, 0)) OVER (
                   PARTITION BY es.expense_id ORDER BY es.due_date, es.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS before_me
        FROM expense_schedule es
        JOIN expenses e ON e.id = es.expense_id
        WHERE es.is_paid = FALSE
          AND (%(project_id)s IS NULL OR e.project_id = %(project_id)s)
    ),
    pending_out AS (
        SELECT sp.expense_id, SUM(sp.amount) AS pending
        FROM supplier_payments sp
        JOIN outgoing_checks oc ON oc.id = sp.check_id
        WHERE oc.status = 'verildi'
        GROUP BY sp.expense_id
    )
    SELECT due_date, source, SUM(amount) AS amount
    FROM (
        SELECT i.due_date, 'installment' AS source,
               i.remaining - LEAST(i.remaining,
                   GREATEST(0, COALESCE(pi.pending, 0) - i.before_me)) AS amount
        FROM inst i
        LEFT JOIN pending_in pi ON pi.flat_id = i.flat_id
        WHERE i.due_date < %(end)s

        UNION ALL

        SELECT c.due_date, 'check', c.amount
        FROM checks c
        WHERE c.status = 'portfoyde' AND c.due_date < %(end)s
          AND (%(project_id)s IS NULL OR EXISTS (
                SELECT 1 FROM payments p JOIN flats f ON f.id = p.flat_id
                WHERE p.check_id = c.id AND f.project_id = %(project_id)s))

        UNION ALL

        SELECT x.due_date, 'expense',
               x.remaining - LEAST(x.remaining,
                   GREATEST(0, COALESCE(po.pending, 0) - x.before_me))
        FROM exp x
        LEFT JOIN pending_out po ON po.expense_id = x.expense_id
        WHERE x.due_date < %(end)s

        UNION ALL

        SELECT oc.due_date, 'outgoing_check', oc.amount
        FROM outgoing_checks oc
        WHERE oc.status = 'verildi' AND oc.due_date < %(end)s
          AND (%(project_id)s IS NULL OR EXISTS (
                SELECT 1 FROM supplier_payments sp JOIN expenses e ON e.id = sp.expense_id
                WHERE sp.check_id = oc.id AND e.project_id = %(project_id)s)
               OR (NOT EXISTS (SELECT 1 FROM supplier_payments sp WHERE sp.check_id = oc.id)
                   AND EXISTS (SELECT 1 FROM suppliers s
                               WHERE s.id = oc.supplier_id AND s.project_id = %(project_id)s)))
    ) t
    WHERE amount <> 0
    GROUP BY due_date, source
"""


def monday_of(day):
    return day - timedelta(days=day.weekday())


def horizon(today, months):
    """(first Monday shown, first Monday NOT shown) for a period in months."""
    if months not in PERIOD_CHOICES:
        months = PERIOD_CHOICES[0]
    start = monday_of(today)
    last_day = today + relativedelta(months=months)
    return start, monday_of(last_day) + timedelta(days=7)


def load_expected_flows(cur, end_exclusive, project_id=None):
    """Expected money per day and source, everything due before end_exclusive.

    Open items that are already late are included with their old dates; the
    caller puts them in a separate "late" row. One query, however many
    projects, weeks or months there are.
    """
    cur.execute(FLOWS_SQL, {'project_id': project_id, 'end': end_exclusive})
    return cur.fetchall()


def _zeros():
    return {source: ZERO for source in INCOMING_SOURCES + OUTGOING_SOURCES}


def _row(key, label, parts, start=None, end=None, late=False):
    incoming = sum((parts[s] for s in INCOMING_SOURCES), ZERO)
    outgoing = sum((parts[s] for s in OUTGOING_SOURCES), ZERO)
    return {
        'key': key, 'label': label, 'start': start, 'end': end, 'late': late,
        'parts': parts, 'incoming': incoming, 'outgoing': outgoing,
        'net': incoming - outgoing,
    }


def _add_running_total(rows, opening):
    running = opening
    for row in rows:
        running += row['net']
        row['cumulative'] = running
        row['negative'] = running < 0


def build_cashflow(flows, start_monday, end_exclusive, opening=ZERO):
    """Turn the per-day rows into weekly and monthly tables.

    The weekly and the monthly table always add up to the same totals: items
    from before this week's Monday go to one "late" row in both.
    """
    by_week = defaultdict(_zeros)       # Monday, or None for late
    by_month = defaultdict(_zeros)      # (year, month), or None for late
    for due_date, source, amount in flows:
        if due_date < start_monday:
            week, month = None, None
        else:
            week, month = monday_of(due_date), (due_date.year, due_date.month)
        by_week[week][source] += amount
        by_month[month][source] += amount

    weeks, months = [], []
    late_label = 'Önceki haftalardan gecikmiş'
    if any(by_week[None].values()):
        weeks.append(_row('late', late_label, by_week[None], late=True))
        months.append(_row('late', late_label, by_month[None], late=True))

    week = start_monday
    while week < end_exclusive:
        end = week + timedelta(days=6)
        weeks.append(_row(week.isoformat(), week_label(week, end),
                          by_week.get(week, _zeros()), week, end))
        week += timedelta(days=7)

    month = start_monday.replace(day=1)
    last_shown = end_exclusive - timedelta(days=1)
    while (month.year, month.month) <= (last_shown.year, last_shown.month):
        key = (month.year, month.month)
        months.append(_row('%d-%02d' % key, month_label(month),
                           by_month.get(key, _zeros())))
        month += relativedelta(months=1)

    _add_running_total(weeks, opening)
    _add_running_total(months, opening)

    total_in = sum((r['incoming'] for r in weeks), ZERO)
    total_out = sum((r['outgoing'] for r in weeks), ZERO)
    negative_weeks = [r for r in weeks if r['negative']]
    return {
        'weeks': weeks,
        'months': months,
        'opening': opening,
        'total_in': total_in,
        'total_out': total_out,
        'total_net': total_in - total_out,
        'closing': opening + total_in - total_out,
        'lowest': min(weeks, key=lambda r: r['cumulative']) if weeks else None,
        'first_negative': negative_weeks[0] if negative_weeks else None,
        'negative_count': len(negative_weeks),
    }


MONTH_NAMES = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz',
               'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def month_label(day):
    return '%s %d' % (MONTH_NAMES[day.month - 1], day.year)


def week_label(start, end):
    return '%s – %s' % (start.strftime('%d.%m.%Y'), end.strftime('%d.%m.%Y'))


def parse_money_arg(text):
    """A typed amount such as 1.500.000,50 or 1500000.5 or -250000.

    Dots are thousands separators when they are grouped in threes (the way
    this app prints numbers); a comma is the decimal separator. Raises
    ValueError for anything else, so the caller can fall back to zero.
    """
    s = (text or '').replace('₺', '').replace(' ', '').strip()
    if not s:
        return ZERO
    if re.fullmatch(r'-?\d{1,3}(\.\d{3})+(,\d+)?', s):
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s and '.' in s:
        s = s.replace(',', '')          # 1,500.25
    else:
        s = s.replace(',', '.')
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError('not a number')
    if not value.is_finite() or abs(value) >= Decimal('1e12'):
        raise ValueError('out of range')
    return value.quantize(Decimal('0.01'))
