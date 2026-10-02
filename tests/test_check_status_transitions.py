"""Characterization tests for check status changes and their effect on debt.

The rule: a check moves money only when someone marks it. An incoming check
counts at 'tahsil_edildi', an outgoing one at 'odendi'. The due date changes
nothing on its own, and a check can be marked before it is due.

These go through the real route (/check/update_status), so they also cover
the reconcile call it makes afterwards.
"""
from datetime import date, timedelta
from decimal import Decimal

D = Decimal
JAN = date(2026, 1, 10)
FEB = date(2026, 2, 10)
LONG_AGO = date(2026, 1, 1)
FAR_AHEAD = date(2030, 1, 1)


def set_status(client, check_id, check_type, new_status):
    return client.post('/check/update_status', data={
        'check_id': str(check_id), 'check_type': check_type,
        'new_status': new_status}, follow_redirects=True)


def flat_with_check(factory, amount=D('2000'), status='portfoyde',
                    due_date=None):
    project_id = factory.project('cek gecis')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=amount)
    factory.installment(flat_id, JAN, amount / 2)
    factory.installment(flat_id, FEB, amount / 2)
    check_id = factory.check(customer_id, amount, status=status,
                             due_date=due_date)
    factory.payment(flat_id, amount, 'çek', check_id=check_id)
    factory.commit()
    return flat_id, check_id


def expense_with_check(factory, amount=D('2000'), status='verildi'):
    project_id = factory.project('gider cek gecis')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=amount)
    factory.expense_installment(expense_id, JAN, amount / 2)
    factory.expense_installment(expense_id, FEB, amount / 2)
    check_id = factory.outgoing_check(supplier_id, amount, status=status)
    factory.supplier_payment(expense_id, supplier_id, amount, 'çek',
                             check_id=check_id)
    factory.commit()
    return expense_id, check_id


# --- incoming checks -------------------------------------------------

def test_marking_an_incoming_check_cleared_closes_the_debt(client, factory):
    flat_id, check_id = flat_with_check(factory)
    assert factory.paid_amounts(flat_id) == [D('0'), D('0')]

    response = set_status(client, check_id, 'incoming', 'tahsil_edildi')

    assert response.status_code == 200
    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000')]
    assert factory.paid_flags(flat_id) == [True, True]


def test_marking_an_incoming_check_bounced_reopens_the_debt(client, factory):
    flat_id, check_id = flat_with_check(factory, status='tahsil_edildi')
    set_status(client, check_id, 'incoming', 'tahsil_edildi')
    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000')]

    set_status(client, check_id, 'incoming', 'karsiliksiz')

    assert factory.paid_amounts(flat_id) == [D('0'), D('0')]
    assert factory.paid_flags(flat_id) == [False, False]


def test_putting_a_check_back_in_the_portfolio_reopens_the_debt(client, factory):
    flat_id, check_id = flat_with_check(factory, status='tahsil_edildi')
    set_status(client, check_id, 'incoming', 'portfoyde')

    assert factory.paid_amounts(flat_id) == [D('0'), D('0')]


def test_an_overdue_check_still_closes_nothing_by_itself(client, factory):
    """The due date is information only; nothing happens when it passes."""
    flat_id, _ = flat_with_check(factory, status='portfoyde',
                                 due_date=LONG_AGO)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0')]


def test_a_check_can_be_marked_cleared_before_it_is_due(client, factory):
    flat_id, check_id = flat_with_check(factory, status='portfoyde',
                                        due_date=FAR_AHEAD)

    set_status(client, check_id, 'incoming', 'tahsil_edildi')

    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000')]


def test_the_check_row_itself_is_updated(client, factory):
    _, check_id = flat_with_check(factory)

    set_status(client, check_id, 'incoming', 'tahsil_edildi')

    factory.cur.execute("SELECT status FROM checks WHERE id = %s", (check_id,))
    assert factory.cur.fetchone()[0] == 'tahsil_edildi'


# --- outgoing checks -------------------------------------------------

def test_marking_an_outgoing_check_paid_closes_the_expense(client, factory):
    expense_id, check_id = expense_with_check(factory)
    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]

    response = set_status(client, check_id, 'outgoing', 'odendi')

    assert response.status_code == 200
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000')]


def test_marking_an_outgoing_check_bounced_reopens_the_expense(client, factory):
    expense_id, check_id = expense_with_check(factory, status='odendi')
    set_status(client, check_id, 'outgoing', 'odendi')
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000')]

    set_status(client, check_id, 'outgoing', 'karsiliksiz')

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]


def test_taking_an_outgoing_check_back_to_given_reopens_the_expense(client, factory):
    expense_id, check_id = expense_with_check(factory, status='odendi')
    set_status(client, check_id, 'outgoing', 'verildi')

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]


def test_one_outgoing_check_covering_two_expenses_updates_both(client, factory):
    project_id = factory.project('tek cek iki gider')
    supplier_id = factory.supplier(project_id)
    first = factory.expense(project_id, supplier_id, amount=D('1000'),
                            title='birinci')
    second = factory.expense(project_id, supplier_id, amount=D('1000'),
                             title='ikinci')
    factory.expense_installment(first, JAN, D('1000'))
    factory.expense_installment(second, JAN, D('1000'))
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='verildi')
    factory.supplier_payment(first, supplier_id, D('1000'), 'çek',
                             check_id=check_id)
    factory.supplier_payment(second, supplier_id, D('1000'), 'çek',
                             check_id=check_id)
    factory.commit()

    set_status(client, check_id, 'outgoing', 'odendi')

    assert factory.expense_paid_amounts(first) == [D('1000')]
    assert factory.expense_paid_amounts(second) == [D('1000')]
