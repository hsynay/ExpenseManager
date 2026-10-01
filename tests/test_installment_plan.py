"""Characterization tests for the payment plan routes.

manage_payment_plan rewrites a flat's whole plan from the submitted rows,
then redistributes the payments already recorded and refreshes the totals on
the flat. manage_expense_plan does the same on the expense side.
"""
import json
from datetime import date
from decimal import Decimal

D = Decimal
JAN = date(2026, 1, 10)
FEB = date(2026, 2, 10)
MAR = date(2026, 3, 10)


def plan_payload(rows, token=None):
    """rows: [(iso date, amount string)]"""
    data = {'plan_json': json.dumps(
        [{'due_date': d, 'amount': a} for d, a in rows])}
    if token:
        data['submission_token'] = token
    return data


def flat_totals(factory, flat_id):
    factory.cur.execute(
        "SELECT total_price, total_installments FROM flats WHERE id = %s",
        (flat_id,))
    return factory.cur.fetchone()


# --- payment plan ----------------------------------------------------

def test_plan_is_created_from_the_submitted_rows(client, factory):
    project_id = factory.project('plan')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=D('0'))
    factory.commit()

    response = client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-01-10', '1000'), ('2026-02-10', '1000'),
         ('2026-03-10', '1000')]), follow_redirects=True)

    assert response.status_code == 200
    state = factory.installment_state(flat_id)
    assert [row[0] for row in state] == [JAN, FEB, MAR]
    assert [row[1] for row in state] == [D('1000'), D('1000'), D('1000')]


def test_plan_rows_are_sorted_by_due_date(client, factory):
    project_id = factory.project('plan sira')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-03-10', '300'), ('2026-01-10', '100'),
         ('2026-02-10', '200')]), follow_redirects=True)

    state = factory.installment_state(flat_id)
    assert [row[0] for row in state] == [JAN, FEB, MAR]
    assert [row[1] for row in state] == [D('100'), D('200'), D('300')]


def test_saving_a_plan_updates_the_flat_totals(client, factory):
    project_id = factory.project('plan toplam')
    flat_id = factory.flat(project_id, total_price=D('55'))
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-01-10', '1000'), ('2026-02-10', '1500')]),
        follow_redirects=True)

    assert flat_totals(factory, flat_id) == (D('2500'), 2)


def test_saving_a_plan_replaces_the_previous_one(client, factory):
    project_id = factory.project('plan degistir')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.installment(flat_id, JAN, D('9999'))
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-02-10', '100')]), follow_redirects=True)

    state = factory.installment_state(flat_id)
    assert len(state) == 1
    assert state[0][0] == FEB and state[0][1] == D('100')


def test_existing_payments_are_redistributed_over_the_new_plan(client, factory):
    project_id = factory.project('plan dagit')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=D('0'))
    factory.payment(flat_id, D('1500'), 'nakit')
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-01-10', '1000'), ('2026-02-10', '1000')]),
        follow_redirects=True)

    assert factory.paid_amounts(flat_id) == [D('1000'), D('500')]
    assert factory.paid_flags(flat_id) == [True, False]


def test_a_portfolio_check_is_not_spread_over_the_new_plan(client, factory):
    """The check rule still holds while a plan is rewritten."""
    project_id = factory.project('plan cek')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=D('0'))
    check_id = factory.check(customer_id, D('2000'), status='portfoyde')
    factory.payment(flat_id, D('2000'), 'çek', check_id=check_id)
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-01-10', '1000'), ('2026-02-10', '1000')]),
        follow_redirects=True)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0')]


# --- validation errors ---------------------------------------------------
#
# A refused plan sends the user back to the plan form with the message, and
# the plan that was there before stays exactly as it was.
#
# The user lands on the plan form on purpose and not on the 'next' address:
# /debts does not render flash messages, so the message would be lost there.
# 'next' is carried along, so the save that follows still returns to /debts.
#
# This used to answer 500: the error branch did not return, the finally block
# closed the cursor and the GET branch below reused it.

def post_plan(client, flat_id, rows, next_url=None):
    url = '/flat/%d/manage_plan' % flat_id
    if next_url:
        url += '?next=' + next_url
    return client.post(url, data=plan_payload(rows), follow_redirects=True)


def test_invalid_date_is_refused_with_a_message(client, factory):
    project_id = factory.project('plan hatali tarih')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.installment(flat_id, JAN, D('777'))
    factory.commit()

    response = post_plan(client, flat_id, [('22026-01-10', '100')])

    assert response.status_code == 200
    assert 'Geçersiz tarih' in response.get_data(as_text=True)
    state = factory.installment_state(flat_id)
    assert len(state) == 1 and state[0][1] == D('777')


def test_invalid_amount_is_refused_with_a_message(client, factory):
    project_id = factory.project('plan hatali tutar')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.installment(flat_id, JAN, D('777'))
    factory.commit()

    response = post_plan(client, flat_id, [('2026-01-10', 'abc')])

    assert response.status_code == 200
    assert 'Geçersiz tutar' in response.get_data(as_text=True)
    state = factory.installment_state(flat_id)
    assert len(state) == 1 and state[0][1] == D('777')


def test_empty_plan_is_refused_with_a_message(client, factory):
    project_id = factory.project('plan bos')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.installment(flat_id, JAN, D('777'))
    factory.commit()

    response = post_plan(client, flat_id, [])

    assert response.status_code == 200
    assert 'En az bir taksit' in response.get_data(as_text=True)
    assert len(factory.installment_state(flat_id)) == 1


def test_a_refused_plan_returns_to_the_plan_form_and_keeps_next(client,
                                                                factory):
    project_id = factory.project('plan hata next')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.installment(flat_id, JAN, D('777'))
    factory.commit()

    response = post_plan(client, flat_id, [('2026-01-10', 'abc')],
                         next_url='/debts')

    assert response.request.path == '/flat/%d/manage_plan' % flat_id
    assert 'value="/debts"' in response.get_data(as_text=True)


def test_rows_with_a_missing_field_are_skipped(client, factory):
    project_id = factory.project('plan eksik')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.commit()

    client.post('/flat/%d/manage_plan' % flat_id, data=plan_payload(
        [('2026-01-10', '100'), ('', '200'), ('2026-03-10', '')]),
        follow_redirects=True)

    state = factory.installment_state(flat_id)
    assert len(state) == 1 and state[0][1] == D('100')


# --- expense plan ----------------------------------------------------

def test_expense_plan_is_created_and_payments_redistributed(client, factory):
    project_id = factory.project('gider plan')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('2000'))
    factory.supplier_payment(expense_id, supplier_id, D('1200'), 'nakit')
    factory.commit()

    response = client.post('/expense/%d/manage_plan' % expense_id,
                           data=plan_payload([('2026-01-20', '1000'),
                                              ('2026-02-20', '1000')]),
                           follow_redirects=True)

    assert response.status_code == 200
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('200')]


def test_expense_plan_ignores_a_check_that_was_only_handed_over(client, factory):
    """manage_expense_plan uses the correct reconcile function."""
    project_id = factory.project('gider plan cek')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('2000'))
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('2000'), 'çek',
                             check_id=check_id)
    factory.commit()

    client.post('/expense/%d/manage_plan' % expense_id,
                data=plan_payload([('2026-01-20', '1000'),
                                   ('2026-02-20', '1000')]),
                follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]
