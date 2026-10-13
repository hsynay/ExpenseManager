"""/project/<id>/profitability: the figures, the check rule and the tie to the
cooperative report.

The central rule: "spent" must equal what the cooperative report shows as paid
this month, and "committed" must equal its "remaining commitment" line. The
checks below run the real report page on a cooperative project and compare the
numbers. (The report used to show the plan total as one "expense" figure; it
now shows the paid part and the commitment part separately, and the two add up
to that same total.)
"""
from decimal import Decimal

import pytest

import app as app_module
from profitability import load_profitability
from reconcile import reconcile_customer_payments, reconcile_supplier_payments

D = Decimal


def build_scenario(factory):
    """One cooperative project with every kind of row the rule talks about.

    Income side
        sold flat 1,000,000 (owner set) and an unsold flat 500,000 (no owner)
        paid:   cash 200,000, check cashed 100,000
        not:    check in portfolio 50,000, bounced check 25,000
    Expense side
        expense A 400,000 (two installments), expense B 100,000
        on A: cash 150,000, check 'odendi' 100,000,
              check 'verildi' 80,000, check 'karsiliksiz' 30,000
        petty cash 20,000
    """
    project_id = factory.project('kar', 'cooperative')
    customer_id = factory.customer(first='Kar')
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=D('1000000'), flat_no=1)
    factory.flat(project_id, owner_id=None, total_price=D('500000'), flat_no=2)
    for _ in range(10):
        factory.installment(flat_id, '2026-07-01', D('100000'))

    factory.payment(flat_id, D('200000'), payment_method='nakit')
    cashed = factory.check(customer_id, D('100000'), status='tahsil_edildi')
    factory.payment(flat_id, D('100000'), payment_method='çek', check_id=cashed)
    waiting = factory.check(customer_id, D('50000'), status='portfoyde')
    factory.payment(flat_id, D('50000'), payment_method='çek', check_id=waiting)
    bounced = factory.check(customer_id, D('25000'), status='karsiliksiz')
    factory.payment(flat_id, D('25000'), payment_method='çek', check_id=bounced)

    supplier_id = factory.supplier(project_id)
    expense_a = factory.expense(project_id, supplier_id, D('400000'), title='A')
    factory.expense_installment(expense_a, '2026-07-01', D('200000'))
    factory.expense_installment(expense_a, '2026-08-01', D('200000'))
    expense_b = factory.expense(project_id, supplier_id, D('100000'), title='B')
    factory.expense_installment(expense_b, '2026-07-01', D('100000'))

    factory.supplier_payment(expense_a, supplier_id, D('150000'))
    for amount, status in ((D('100000'), 'odendi'), (D('80000'), 'verildi'),
                           (D('30000'), 'karsiliksiz')):
        check_id = factory.outgoing_check(supplier_id, amount, status=status)
        factory.supplier_payment(expense_a, supplier_id, amount,
                                 payment_method='çek', check_id=check_id)
    factory.petty_cash(project_id, D('20000'))

    return {'project_id': project_id, 'flat_id': flat_id,
            'expenses': (expense_a, expense_b)}


# --- the figures -------------------------------------------------------------

def test_sold_counts_only_flats_with_an_owner(factory):
    scenario = build_scenario(factory)

    f = load_profitability(factory.cur, scenario['project_id'])

    assert f['sold'] == D('1000000')
    assert (f['sold_count'], f['flat_count']) == (1, 2)


def test_collected_follows_the_check_rule(factory):
    scenario = build_scenario(factory)

    f = load_profitability(factory.cur, scenario['project_id'])

    # cash 200,000 + cashed check 100,000. The portfolio check and the
    # bounced check close nothing.
    assert f['collected'] == D('300000')
    assert f['receivable'] == D('700000')


def test_spent_counts_only_money_that_really_left(factory):
    scenario = build_scenario(factory)

    f = load_profitability(factory.cur, scenario['project_id'])

    # cash 150,000 + check 'odendi' 100,000 + petty 20,000. The handed over
    # check (verildi) and the bounced one are not spent.
    assert f['spent_paid'] == D('250000')
    assert f['spent_petty'] == D('20000')
    assert f['spent'] == D('270000')


def test_committed_is_plan_minus_paid_and_includes_handed_checks(factory):
    scenario = build_scenario(factory)

    f = load_profitability(factory.cur, scenario['project_id'])

    assert f['planned_total'] == D('500000')
    assert f['committed'] == D('250000')
    # 80,000 was handed over as a check. It is inside the 250,000, so adding
    # it again would count it twice.
    assert f['handed_checks'] == D('80000')
    assert f['total_cost'] == f['spent'] + f['committed'] == D('520000')


def test_forecast_profit(factory):
    scenario = build_scenario(factory)

    f = load_profitability(factory.cur, scenario['project_id'])

    assert f['forecast_profit'] == D('1000000') - D('520000')


def test_unknown_project_returns_none(factory):
    assert load_profitability(factory.cur, 0) is None


# --- the tie to the cooperative report ---------------------------------------

def report_expense(client, render_context, project_id):
    """What the June 2026 report says: (paid this month, remaining commitment).

    The report shows money that really left (payments by payment date) and,
    separately, what is still to be paid. All dates in these scenarios are in
    June, so the month holds everything.
    """
    response = client.get('/reports/cooperative/%d/2026/6' % project_id)
    assert response.status_code == 200
    data = render_context.latest('coop_report.html')['report_data']
    return data['current_expense'], data['remaining_commitment']


def test_report_matches_spent_and_committed(factory, client, render_context):
    scenario = build_scenario(factory)
    factory.commit()

    f = load_profitability(factory.cur, scenario['project_id'])
    paid, commitment = report_expense(client, render_context,
                                      scenario['project_id'])

    assert paid == f['spent'] == D('270000')
    assert commitment == f['committed'] == D('250000')
    # The plan plus petty cash: the same total the report used to show as
    # "expense" before it was split into paid and still owed.
    assert paid + commitment == f['total_cost'] == D('520000')


def test_tie_holds_when_a_payment_exceeds_its_plan(factory, client,
                                                   render_context):
    # Expense C is paid 150,000 over its plan. A per expense floor would show
    # a bigger commitment than the report; the net figure keeps the tie.
    project_id = factory.project('fazla', 'cooperative')
    supplier_id = factory.supplier(project_id)
    expense_c = factory.expense(project_id, supplier_id, D('100000'), title='C')
    factory.expense_installment(expense_c, '2026-07-01', D('100000'))
    factory.supplier_payment(expense_c, supplier_id, D('250000'))
    expense_d = factory.expense(project_id, supplier_id, D('200000'), title='D')
    factory.expense_installment(expense_d, '2026-07-01', D('200000'))
    factory.supplier_payment(expense_d, supplier_id, D('20000'))
    factory.commit()

    f = load_profitability(factory.cur, project_id)
    paid, commitment = report_expense(client, render_context, project_id)

    assert (f['overpaid_count'], f['overpaid_total']) == (1, D('150000'))
    assert f['spent'] == D('270000') and f['committed'] == D('30000')
    assert (paid, commitment) == (f['spent'], f['committed'])
    assert paid + commitment == D('300000')


def test_the_page_warns_about_overpayment(factory, client, render_context):
    project_id = factory.project('uyari', 'cooperative')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D('100000'))
    factory.expense_installment(expense_id, '2026-07-01', D('100000'))
    factory.supplier_payment(expense_id, supplier_id, D('200000'))
    factory.commit()

    response = client.get('/project/%d/profitability' % project_id)

    assert response.status_code == 200
    assert 'plan tutarını aşıyor' in response.get_data(as_text=True)


# --- agreement with the reconcile functions ----------------------------------

def test_figures_agree_with_what_reconcile_stores(factory):
    """With no overpayment, the stored schedule says the same thing."""
    scenario = build_scenario(factory)
    cur = factory.cur

    reconcile_customer_payments(cur, scenario['flat_id'])
    for expense_id in scenario['expenses']:
        reconcile_supplier_payments(cur, expense_id)
    f = load_profitability(cur, scenario['project_id'])

    cur.execute("SELECT COALESCE(SUM(paid_amount), 0) FROM installment_schedule "
                "WHERE flat_id = %s", (scenario['flat_id'],))
    assert cur.fetchone()[0] == f['collected']

    cur.execute("""SELECT COALESCE(SUM(es.amount - es.paid_amount), 0)
                   FROM expense_schedule es JOIN expenses e ON es.expense_id = e.id
                   WHERE e.project_id = %s""", (scenario['project_id'],))
    assert cur.fetchone()[0] == f['committed']


# --- the real copy of live data ----------------------------------------------

def test_project_32_matches_the_checked_figures(factory):
    cur = factory.cur
    cur.execute("SELECT name FROM projects WHERE id = 32")
    row = cur.fetchone()
    if row is None or row[0] != 'Hayriye Mahallesi':
        pytest.skip('project 32 (copy of live data) is not in this database')

    f = load_profitability(cur, 32)

    assert f['collected'] == D('33583000')
    assert f['receivable'] == D('9757000')
    assert f['spent'] == D('28857068')
    assert f['committed'] == D('2180910')
    # 28,857,068 + 2,180,910 = 31,037,978: the expense plan plus petty cash.
    assert f['total_cost'] == D('31037978')


# --- the screen --------------------------------------------------------------

def test_page_renders_with_every_figure(factory, client, render_context):
    scenario = build_scenario(factory)
    factory.commit()

    response = client.get('/project/%d/profitability' % scenario['project_id'])

    assert response.status_code == 200
    f = render_context.latest('project_profitability.html')['f']
    assert f['collected'] == D('300000') and f['committed'] == D('250000')
    html = response.get_data(as_text=True)
    for label in ('Satılan toplam', 'Tahsil edilen', 'Kalan alacak',
                  'Harcanan', 'Taahhüt', 'Öngörülen kâr'):
        assert label in html
    # Every figure says which table it came from.
    for source in ('flats.total_price', 'payments', 'supplier_payments',
                   'expense_schedule', 'petty_cash_expenses'):
        assert source in html


def test_page_is_reachable_from_overview_and_dashboard(factory, client):
    project_id = factory.project('link')
    factory.commit()
    target = '/project/%d/profitability' % project_id

    assert target in client.get('/project/%d/overview' % project_id) \
        .get_data(as_text=True)
    assert target in client.get('/dashboard').get_data(as_text=True)


def test_page_needs_a_login():
    app_module.app.config['TESTING'] = False
    with app_module.app.test_client() as anonymous:
        response = anonymous.get('/project/1/profitability')

    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    assert response.headers['Location'] == '/login?next=/project/1/profitability'


def test_unknown_project_is_a_404(client):
    assert client.get('/project/999999999/profitability').status_code == 404


def test_page_only_reads_and_runs_three_queries(factory, client,
                                                query_counter):
    scenario = build_scenario(factory)
    factory.commit()
    query_counter.clear()

    response = client.get('/project/%d/profitability' % scenario['project_id'])

    assert response.status_code == 200
    statements = [' '.join(sql.split()).upper() for sql in query_counter]
    assert len(statements) == 3
    for statement in statements:
        assert statement.startswith(('SELECT', 'WITH'))
        for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER '):
            assert word not in statement


def test_page_is_get_only(factory, client):
    project_id = factory.project('get')
    factory.commit()

    response = client.post('/project/%d/profitability' % project_id)

    assert response.status_code == 405
