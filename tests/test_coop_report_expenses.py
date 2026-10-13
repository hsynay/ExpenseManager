"""The expense side of the cooperative report.

The report used to read expenses.payment_method. Nothing fills that column, so
it always held the default 'nakit' and every expense counted as paid on its
expense date. The report now takes the expense side from the real payments:

* paid  = supplier_payments that are cash, or whose outgoing check is 'odendi',
          by payment_date (the same source and rule as project_overview)
* still to pay ("Kalan Taahhüt") = the plan (expense_schedule) minus what was
          really paid, as of the end of the report month

The scenario below uses only rows these tests create.
"""
from datetime import date
from decimal import Decimal

D = Decimal
MAY = date(2026, 5, 20)
JUNE = date(2026, 6, 15)
JULY = date(2026, 7, 10)


def build(factory):
    """A cooperative project, expense dates and payments around June 2026.

    expense   plan   expense_date   payments
    BIG       5000   2026-05-01     May 20  cash 400
                                    Jun 10  cash 1000
                                    Jun 12  check 'odendi' 500
                                    Jun 14  check 'verildi' 300  (not paid yet)
                                    Jun 16  check 'karsiliksiz' 200
    OPEN      2000   2026-06-05     none (and payment_method left at its
                                    default 'nakit', the old bug)
    petty cash 100 on June 15.
    """
    project_id = factory.project('koop gider', project_type='cooperative')
    supplier_id = factory.supplier(project_id)

    big = factory.expense(project_id, supplier_id, D(5000), title='BIG',
                          expense_date=date(2026, 5, 1))
    factory.expense_installment(big, date(2026, 7, 1), D(5000))
    factory.supplier_payment(big, supplier_id, D(400), 'nakit',
                             payment_date=MAY)
    factory.supplier_payment(big, supplier_id, D(1000), 'nakit',
                             payment_date=date(2026, 6, 10))
    for amount, status, day in ((D(500), 'odendi', 12),
                                (D(300), 'verildi', 14),
                                (D(200), 'karsiliksiz', 16)):
        check_id = factory.outgoing_check(supplier_id, amount, status=status)
        factory.supplier_payment(big, supplier_id, amount, 'çek',
                                 check_id=check_id,
                                 payment_date=date(2026, 6, day))

    open_expense = factory.expense(project_id, supplier_id, D(2000),
                                   title='OPEN',
                                   expense_date=date(2026, 6, 5))
    factory.expense_installment(open_expense, date(2026, 8, 1), D(2000))

    factory.petty_cash(project_id, D(100), expense_date=JUNE)
    factory.commit()
    return project_id


def report(client, render_context, project_id, year=2026, month=6):
    response = client.get('/reports/cooperative/%d/%d/%d'
                          % (project_id, year, month))
    assert response.status_code == 200
    return render_context.latest('coop_report.html')['report_data']


# --- what counts as paid -------------------------------------------------------

def test_an_unpaid_expense_is_not_shown_as_paid(client, factory,
                                                render_context):
    """The old bug: OPEN has no payment, yet its 2000 was counted as paid on
    its expense date because payment_method defaulted to 'nakit'."""
    project_id = factory.project('koop bos', project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D(2000),
                                 expense_date=JUNE)
    factory.expense_installment(expense_id, JULY, D(2000))
    factory.commit()

    data = report(client, render_context, project_id)

    assert data['current_expense'] == D(0)
    assert data['expense_paid'] == D(0)
    assert data['remaining_commitment'] == D(2000)
    # And it is still there: listed, with nothing paid.
    assert [(r['status_text'], r['amount']) for r in data['expense_details']] \
        == [('Ödenmedi', D(0))]


def test_only_cash_and_paid_checks_count(client, factory, render_context):
    data = report(client, render_context, build(factory))

    # cash 1000 + check 'odendi' 500 + petty cash 100. The check that was only
    # handed over (300) and the bounced one (200) are not money that left.
    assert data['current_expense'] == D(1600)
    assert data['expense_paid'] == D(1600)


def test_a_handed_over_check_is_listed_but_not_counted(client, factory,
                                                       render_context):
    data = report(client, render_context, build(factory))

    assert data['expense_pending'] == D(300)
    rows = {r['status_text']: r for r in data['expense_details']}
    assert rows['Çek Verildi']['counts'] is False
    assert rows['Karşılıksız']['counts'] is False
    assert rows['Karşılıksız']['amount'] == D(200)


def test_rows_are_the_payments_of_the_month(client, factory, render_context):
    data = report(client, render_context, build(factory))

    rows = data['expense_details']
    # 4 payments in June (cash, 3 checks) + 1 petty cash + the OPEN expense,
    # which is dated in June and has nothing paid (listed with 0). The May
    # payment is not in the June list.
    assert len(rows) == 6
    assert sorted(r['amount'] for r in rows) == [D(0), D(100), D(200), D(300),
                                                  D(500), D(1000)]
    counted = sum(r['amount'] for r in rows if r['counts'])
    assert counted == data['current_expense']


def test_previous_months_payments_are_in_the_carried_balance(client, factory,
                                                             render_context):
    data = report(client, render_context, build(factory))

    # No income at all. The May cash payment (400) was paid by payment_date in
    # May, so it is carried, whatever the expense date says.
    assert data['previous_balance'] == D(-400)
    assert data['end_of_month_balance'] == D(-400) - D(1600)


def test_payment_date_decides_not_expense_date(client, factory, render_context):
    project_id = build(factory)

    may = report(client, render_context, project_id, month=5)

    # The expense is dated May 1 but only 400 was paid in May.
    assert may['current_expense'] == D(400)


# --- what is still to be paid --------------------------------------------------

def test_remaining_commitment_is_plan_minus_paid(client, factory,
                                                 render_context):
    data = report(client, render_context, build(factory))

    # plan 5000 + 2000, paid by June 30: 400 + 1000 + 500 (petty cash is not
    # part of the plan).
    assert data['planned_to_date'] == D(7000)
    assert data['paid_on_plan'] == D(1900)
    assert data['remaining_commitment'] == D(5100)


def test_handed_checks_are_inside_the_commitment_not_added(client, factory,
                                                           render_context):
    data = report(client, render_context, build(factory))

    assert data['handed_checks'] == D(300)
    # Still owed: 7000 - 1900. The 300 handed over is part of that 5100.
    assert data['remaining_commitment'] == D(5100)


def test_commitment_is_as_of_the_end_of_the_month(client, factory,
                                                  render_context):
    """An expense dated after the report month did not exist yet."""
    project_id = build(factory)
    supplier_id = factory.supplier(project_id, name='Gec')
    later = factory.expense(project_id, supplier_id, D(900), title='LATER',
                            expense_date=JULY)
    factory.expense_installment(later, date(2026, 9, 1), D(900))
    factory.commit()

    june = report(client, render_context, project_id)
    july = report(client, render_context, project_id, month=7)

    assert june['remaining_commitment'] == D(5100)
    assert july['remaining_commitment'] == D(5100) + D(900)


# --- agreement with project_overview -----------------------------------------------

def test_totals_match_project_overview(client, factory, render_context):
    project_id = build(factory)

    data = report(client, render_context, project_id)
    response = client.get('/project/%d/overview' % project_id)
    assert response.status_code == 200
    overview = render_context.latest('project_overview.html')

    # Paid before + paid this month is what project_overview calls paid
    # expense (nothing was paid after June). With no income, the carried
    # balance is minus the earlier expense.
    previous_expense = -data['previous_balance']
    assert previous_expense + data['current_expense'] == \
        overview['total_paid_expense'] == D(2000)
    # And what is still to pay: (plan + petty cash) - (paid + petty cash).
    assert data['remaining_commitment'] == \
        overview['total_unpaid_expense'] == D(5100)


# --- an expense never disappears -----------------------------------------------------

def unpaid_rows(data):
    return [r for r in data['expense_details'] if r['status_text'] == 'Ödenmedi']


def test_an_expense_with_no_plan_and_no_payment_is_listed_with_zero(
        client, factory, render_context):
    """The shape of the old style rows in the sample cooperative project: an
    expense with an amount, but no installments and no payment rows. It used
    to vanish from the report altogether."""
    project_id = factory.project('koop plansiz', project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(3000), title='PLANSIZ',
                    expense_date=JUNE)
    factory.commit()

    data = report(client, render_context, project_id)

    rows = unpaid_rows(data)
    assert len(rows) == 1
    assert rows[0]['title'] == 'PYTEST PLANSIZ'
    assert rows[0]['amount'] == D(0)            # nothing paid
    assert rows[0]['expense_amount'] == D(3000)  # but the expense is shown
    assert rows[0]['counts'] is False


def test_such_an_expense_adds_nothing_to_paid_but_is_in_the_totals(
        client, factory, render_context):
    project_id = factory.project('koop plansiz toplam',
                                 project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(3000), title='PLANSIZ',
                    expense_date=JUNE)
    factory.petty_cash(project_id, D(50), expense_date=JUNE)
    factory.commit()

    data = report(client, render_context, project_id)

    assert data['current_expense'] == D(50)          # only the petty cash
    assert data['expense_paid'] == D(50)
    assert data['month_recorded_expenses'] == D(3000)  # recorded, paid or not
    assert data['remaining_commitment'] == D(3000)     # and still owed


def test_an_expense_with_a_plan_and_no_payment_is_listed(client, factory,
                                                         render_context):
    data = report(client, render_context, build(factory))

    rows = unpaid_rows(data)
    assert [r['title'] for r in rows] == ['PYTEST OPEN']
    assert rows[0]['amount'] == D(0)
    assert rows[0]['expense_amount'] == D(2000)


def test_recorded_total_covers_the_expenses_dated_in_the_month(client, factory,
                                                               render_context):
    data = report(client, render_context, build(factory))

    # OPEN (2000) is dated in June. BIG (5000) is dated in May.
    assert data['month_recorded_expenses'] == D(2000)


def test_an_expense_paid_this_month_is_not_listed_as_unpaid_too(
        client, factory, render_context):
    project_id = factory.project('koop cift', project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D(1000),
                                 title='PAID', expense_date=JUNE)
    factory.expense_installment(expense_id, JULY, D(1000))
    factory.supplier_payment(expense_id, supplier_id, D(400), 'nakit',
                             payment_date=JUNE)
    factory.commit()

    data = report(client, render_context, project_id)

    assert unpaid_rows(data) == []
    assert [r['amount'] for r in data['expense_details']] == [D(400)]
    # The expense amount is still visible on the payment row.
    assert data['expense_details'][0]['expense_amount'] == D(1000)


def test_an_expense_of_another_month_is_listed_in_its_own_month(
        client, factory, render_context):
    project_id = factory.project('koop ay', project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(900), title='JULYEXP',
                    expense_date=JULY)
    factory.commit()

    june = report(client, render_context, project_id)
    july = report(client, render_context, project_id, month=7)

    assert unpaid_rows(june) == []
    assert [r['title'] for r in unpaid_rows(july)] == ['PYTEST JULYEXP']


def test_the_page_shows_the_unpaid_expense(client, factory):
    project_id = factory.project('koop sayfa', project_type='cooperative')
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(3000), title='GORUNUR',
                    expense_date=JUNE)
    factory.commit()

    response = client.get('/reports/cooperative/%d/2026/6' % project_id)

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'PYTEST GORUNUR' in html
    assert 'Ödenmedi' in html
    assert 'Gider tutarı' in html
