"""The 12 month charts on /reports, checked against sums worked out by hand.

The test database has no incoming checks due in the last 12 months, so the
before/after snapshot of /reports cannot tell whether those two lines are
right. These tests create their own rows and check every line of both charts.

They were written to pass on the old per-month queries and on the grouped
queries that replaced them, so they show the two give the same result.

One quirk is pinned on purpose: the check lines join checks to payments, so a
check linked to two payments is counted twice. The old code did that, and the
grouped query keeps it so no number on the page moves.
"""
from datetime import date
from decimal import Decimal

D = Decimal


def months_back(n):
    """First day of the month n months before the current one."""
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


THIS_MONTH = months_back(0)
THREE_AGO = months_back(3)
OUTSIDE = months_back(12)          # just before the 12 month window


def series(render_context, key, project_id):
    ctx = render_context.latest('reports.html')
    return ctx[key][project_id]


def at(values, month_start):
    """Value for a month; the last slot is the current month."""
    for back in range(12):
        if months_back(back) == month_start:
            return values[11 - back]
    raise AssertionError('month not in the window: %s' % month_start)


def get_reports(client):
    response = client.get('/reports')
    assert response.status_code == 200
    return response


def test_incoming_check_lines_count_portfolio_and_cleared(client, factory,
                                                          render_context):
    project_id = factory.project('rapor alinan cek')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)

    portfolio = factory.check(customer_id, D('1000'), status='portfoyde',
                              due_date=THIS_MONTH.replace(day=10))
    cleared = factory.check(customer_id, D('700'), status='tahsil_edildi',
                            due_date=THREE_AGO.replace(day=5))
    bounced = factory.check(customer_id, D('300'), status='karsiliksiz',
                            due_date=THIS_MONTH.replace(day=12))
    too_old = factory.check(customer_id, D('999'), status='portfoyde',
                            due_date=OUTSIDE.replace(day=15))
    for check_id, amount in ((portfolio, '1000'), (cleared, '700'),
                             (bounced, '300'), (too_old, '999')):
        factory.payment(flat_id, D(amount), 'çek', check_id=check_id)
    factory.commit()

    get_reports(client)
    checks = series(render_context, 'check_series', project_id)

    assert at(checks['incoming_portfolio'], THIS_MONTH) == 1000.0
    assert at(checks['incoming_cleared'], THREE_AGO) == 700.0
    # A bounced check is in neither line; one due before the window is
    # nowhere on the chart.
    assert sum(checks['incoming_portfolio']) == 1000.0
    assert sum(checks['incoming_cleared']) == 700.0


def test_a_check_behind_two_payments_is_counted_twice(client, factory,
                                                      render_context):
    """Pinned quirk: the join to payments repeats the check amount."""
    project_id = factory.project('rapor cift odeme')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    check_id = factory.check(customer_id, D('500'), status='portfoyde',
                             due_date=THIS_MONTH.replace(day=3))
    factory.payment(flat_id, D('250'), 'çek', check_id=check_id)
    factory.payment(flat_id, D('250'), 'çek', check_id=check_id)
    factory.commit()

    get_reports(client)
    checks = series(render_context, 'check_series', project_id)

    assert at(checks['incoming_portfolio'], THIS_MONTH) == 1000.0


def test_outgoing_check_lines_count_given_and_paid(client, factory,
                                                   render_context):
    project_id = factory.project('rapor verilen cek')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('5000'))
    given = factory.outgoing_check(supplier_id, D('400'), status='verildi',
                                   due_date=THIS_MONTH.replace(day=20))
    paid = factory.outgoing_check(supplier_id, D('600'), status='odendi',
                                  due_date=THREE_AGO.replace(day=8))
    bounced = factory.outgoing_check(supplier_id, D('50'),
                                     status='karsiliksiz',
                                     due_date=THIS_MONTH.replace(day=21))
    for check_id, amount in ((given, '400'), (paid, '600'), (bounced, '50')):
        factory.supplier_payment(expense_id, supplier_id, D(amount), 'çek',
                                 check_id=check_id)
    factory.commit()

    get_reports(client)
    checks = series(render_context, 'check_series', project_id)

    assert at(checks['outgoing_given'], THIS_MONTH) == 400.0
    assert at(checks['outgoing_paid'], THREE_AGO) == 600.0
    assert sum(checks['outgoing_given']) == 400.0
    assert sum(checks['outgoing_paid']) == 600.0


def test_income_and_expense_lines_follow_the_check_rule(client, factory,
                                                        render_context):
    project_id = factory.project('rapor gelir gider')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('9000'))

    # income this month: 100 cash + 200 cleared check; the portfolio check
    # does not count
    factory.payment(flat_id, D('100'), 'nakit',
                    payment_date=THIS_MONTH.replace(day=2))
    cleared = factory.check(customer_id, D('200'), status='tahsil_edildi')
    factory.payment(flat_id, D('200'), 'çek', check_id=cleared,
                    payment_date=THIS_MONTH.replace(day=3))
    portfolio = factory.check(customer_id, D('5000'), status='portfoyde')
    factory.payment(flat_id, D('5000'), 'çek', check_id=portfolio,
                    payment_date=THIS_MONTH.replace(day=4))

    # expense three months ago: 300 cash + 40 petty cash; the check only
    # handed over does not count
    factory.supplier_payment(expense_id, supplier_id, D('300'), 'nakit',
                             payment_date=THREE_AGO.replace(day=6))
    factory.petty_cash(project_id, D('40'),
                       expense_date=THREE_AGO.replace(day=7))
    given = factory.outgoing_check(supplier_id, D('8000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('8000'), 'çek',
                             check_id=given,
                             payment_date=THREE_AGO.replace(day=9))
    factory.commit()

    get_reports(client)
    monthly = series(render_context, 'monthly_series', project_id)

    assert at(monthly['income'], THIS_MONTH) == 300.0
    assert at(monthly['expense'], THREE_AGO) == 340.0
    assert at(monthly['net'], THIS_MONTH) == 300.0
    assert at(monthly['net'], THREE_AGO) == -340.0
    assert sum(monthly['income']) == 300.0
    assert sum(monthly['expense']) == 340.0


def test_the_month_labels_cover_the_last_twelve_months(client, factory,
                                                       render_context):
    project_id = factory.project('rapor etiket')
    factory.commit()

    get_reports(client)
    monthly = series(render_context, 'monthly_series', project_id)
    checks = series(render_context, 'check_series', project_id)

    expected = [months_back(n).strftime('%b %Y') for n in range(11, -1, -1)]
    assert monthly['labels'] == expected
    assert checks['labels'] == expected
