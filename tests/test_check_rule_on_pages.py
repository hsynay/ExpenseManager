"""The check rule as the pages apply it.

A check in the portfolio is shown but never counted. A bounced check is not
counted anywhere at all. These tests read the numbers each page hands to its
template, so a wrong total is visible directly instead of through HTML.

Covered here: project_overview and the cooperative report. The expense side
of /expenses is covered by test_reconcile_expense_payments.py, which records
the known bug in that path.
"""
from datetime import date
from decimal import Decimal

D = Decimal
JUNE = date(2026, 6, 15)


def overview(client, render_context, project_id):
    response = client.get('/project/%d/overview' % project_id)
    assert response.status_code == 200
    return render_context.latest('project_overview.html')


def coop(client, render_context, project_id, year=2026, month=6):
    response = client.get('/reports/cooperative/%d/%d/%d'
                          % (project_id, year, month))
    assert response.status_code == 200
    return render_context.latest('coop_report.html')['report_data']


# --- project_overview ------------------------------------------------

def test_overview_counts_cash_income(client, factory, render_context):
    project_id = factory.project('ovw nakit')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('1000'))
    factory.payment(flat_id, D('1000'), 'nakit', payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_income'] == D('1000')


def test_overview_does_not_count_a_portfolio_check_as_income(client, factory,
                                                            render_context):
    project_id = factory.project('ovw portfoy')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('1000'))
    check_id = factory.check(customer_id, D('1000'), status='portfoyde')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_income'] == D('0')


def test_overview_does_not_count_a_bounced_check_as_income(client, factory,
                                                           render_context):
    project_id = factory.project('ovw karsiliksiz')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('1000'))
    check_id = factory.check(customer_id, D('1000'), status='karsiliksiz')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_income'] == D('0')


def test_overview_counts_a_cleared_check_as_income(client, factory,
                                                   render_context):
    project_id = factory.project('ovw tahsil')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('1000'))
    check_id = factory.check(customer_id, D('1000'), status='tahsil_edildi')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_income'] == D('1000')


def test_overview_does_not_count_an_unpaid_outgoing_check_as_expense(
        client, factory, render_context):
    project_id = factory.project('ovw gider cek')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('1000'))
    check_id = factory.outgoing_check(supplier_id, D('1000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=check_id, payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_expense'] == D('0')


def test_overview_counts_a_paid_outgoing_check_as_expense(client, factory,
                                                          render_context):
    project_id = factory.project('ovw gider odendi')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('1000'))
    check_id = factory.outgoing_check(supplier_id, D('1000'), status='odendi')
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=check_id, payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_expense'] == D('1000')


def test_overview_shows_the_portfolio_check_even_though_it_is_not_counted(
        client, factory, render_context):
    """Not counted is not the same as hidden: the row must still be listed."""
    project_id = factory.project('ovw gorunur')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('1000'))
    check_id = factory.check(customer_id, D('1000'), status='portfoyde')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    ctx = overview(client, render_context, project_id)

    assert ctx['total_paid_income'] == D('0')
    assert len(ctx['income_items']) == 1


# --- cooperative report ----------------------------------------------

def test_coop_report_counts_cash_contributions(client, factory,
                                               render_context):
    project_id = factory.project('koop nakit', project_type='cooperative')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.payment(flat_id, D('1000'), 'nakit', payment_date=JUNE)
    factory.commit()

    data = coop(client, render_context, project_id)

    assert data['current_income'] == D('1000')


def test_coop_report_ignores_a_portfolio_check(client, factory,
                                               render_context):
    project_id = factory.project('koop portfoy', project_type='cooperative')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    check_id = factory.check(customer_id, D('1000'), status='portfoyde')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    data = coop(client, render_context, project_id)

    assert data['current_income'] == D('0')


def test_coop_report_ignores_a_bounced_check(client, factory, render_context):
    project_id = factory.project('koop karsiliksiz',
                                 project_type='cooperative')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    check_id = factory.check(customer_id, D('1000'), status='karsiliksiz')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    data = coop(client, render_context, project_id)

    assert data['current_income'] == D('0')


def test_coop_report_counts_a_cleared_check(client, factory, render_context):
    project_id = factory.project('koop tahsil', project_type='cooperative')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    check_id = factory.check(customer_id, D('1000'), status='tahsil_edildi')
    factory.payment(flat_id, D('1000'), 'çek', check_id=check_id,
                    payment_date=JUNE)
    factory.commit()

    data = coop(client, render_context, project_id)

    assert data['current_income'] == D('1000')
