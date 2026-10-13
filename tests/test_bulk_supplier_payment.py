"""The bulk supplier payment: /supplier_payment/new.

One payment is spread over every open installment of a supplier in a project.
It used to write expense_schedule by hand and to attach the whole payment to a
single expense, so the other expenses showed paid in the schedule with no
payment behind them. The next reconcile on such an expense put it back to
zero.

Now each expense that receives a part gets its own supplier_payments row and
the schedule is rebuilt by reconcile_supplier_payments.
"""
from datetime import date
from decimal import Decimal

from reconcile import reconcile_supplier_payments

D = Decimal
JAN = date(2026, 1, 20)
FEB = date(2026, 2, 20)
MAR = date(2026, 3, 20)


def two_expenses(factory):
    """Supplier with two expenses whose due dates interleave.

        X: JAN 1000, MAR 1000        Y: FEB 1000

    Open installments in payment order: X-JAN, Y-FEB, X-MAR.
    """
    project_id = factory.project('toplu odeme')
    supplier_id = factory.supplier(project_id)
    x = factory.expense(project_id, supplier_id, amount=D(2000), title='X')
    factory.expense_installment(x, JAN, D(1000))
    factory.expense_installment(x, MAR, D(1000))
    y = factory.expense(project_id, supplier_id, amount=D(1000), title='Y')
    factory.expense_installment(y, FEB, D(1000))
    return project_id, supplier_id, x, y


def pay(client, factory, project_id, supplier_id, amount, token=True):
    form = {'supplier_id': str(supplier_id), 'project_id': str(project_id),
            'amount': str(amount), 'payment_date': '2026-06-15',
            'description': 'PYTEST toplu'}
    if token:
        form['submission_token'] = factory.new_token()
    response = client.post('/supplier_payment/new', data=form,
                           follow_redirects=True)
    track_route_payments(factory, supplier_id)
    return response


def track_route_payments(factory, supplier_id):
    """Hand the rows the route created to the factory for cleanup, so they
    are deleted even when an assertion fails later in the test."""
    factory.cur.execute(
        "SELECT id FROM supplier_payments WHERE supplier_id = %s", (supplier_id,))
    for (payment_id,) in factory.cur.fetchall():
        factory.track('supplier_payments', payment_id)


def payments_of(factory, expense_id):
    """[(amount, payment_method)] of one expense, oldest first."""
    factory.cur.execute(
        "SELECT amount, payment_method FROM supplier_payments "
        "WHERE expense_id = %s ORDER BY id", (expense_id,))
    return list(factory.cur.fetchall())


# --- the root cause ----------------------------------------------------------

def test_every_expense_gets_its_own_payment_row(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    response = pay(client, factory, project_id, supplier_id, D(2500))

    assert response.status_code == 200
    # X-JAN 1000 + Y-FEB 1000 + X-MAR 500
    assert payments_of(factory, x) == [(D('1500'), 'nakit')]
    assert payments_of(factory, y) == [(D('1000'), 'nakit')]


def test_both_schedules_are_right_after_the_payment(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(2500))

    assert factory.expense_paid_amounts(x) == [D('1000'), D('500')]
    assert factory.expense_paid_flags(x) == [True, False]
    assert factory.expense_paid_amounts(y) == [D('1000')]
    assert factory.expense_paid_flags(y) == [True]


def test_a_later_reconcile_changes_nothing(client, factory):
    """The old code left expense Y paid in the schedule with no payment row.
    Reconcile then set it back to zero. Now the schedule is already what
    reconcile would write."""
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()
    pay(client, factory, project_id, supplier_id, D(2500))
    before = (factory.expense_state(x), factory.expense_state(y))

    reconcile_supplier_payments(factory.cur, x)
    reconcile_supplier_payments(factory.cur, y)

    assert (factory.expense_state(x), factory.expense_state(y)) == before


def test_the_money_adds_up(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D('1234.56'))

    total = sum(a for e in (x, y) for a, _ in payments_of(factory, e))
    assert total == D('1234.56')


def test_a_second_payment_continues_where_the_first_stopped(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(1500))   # X-JAN, Y 500
    pay(client, factory, project_id, supplier_id, D(1000))   # Y 500, X-MAR 500

    assert factory.expense_paid_amounts(x) == [D('1000'), D('500')]
    assert factory.expense_paid_amounts(y) == [D('1000')]


# --- what the payment trusts ---------------------------------------------------

def test_a_stale_schedule_does_not_hide_a_debt(client, factory):
    """The schedule says paid, the payments say nothing was paid (the same
    shape as expense 107 in the live data). The debt is real, so the payment
    must go to it, and the schedule must come out right."""
    project_id = factory.project('toplu bayat')
    supplier_id = factory.supplier(project_id)
    stale = factory.expense(project_id, supplier_id, amount=D(1000))
    factory.expense_installment(stale, JAN, D(1000), paid_amount=D(1000),
                                is_paid=True)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(1000))

    assert payments_of(factory, stale) == [(D('1000'), 'nakit')]
    assert factory.expense_paid_amounts(stale) == [D('1000')]


def test_a_check_that_is_not_paid_closes_nothing(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    check_id = factory.outgoing_check(supplier_id, D(1000), status='verildi')
    factory.supplier_payment(y, supplier_id, D(1000), 'çek', check_id=check_id)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(1000))

    # Y is still open, because a handed over check is not money that left.
    # The oldest open installment is X-JAN.
    assert payments_of(factory, x) == [(D('1000'), 'nakit')]


# --- edges ---------------------------------------------------------------------

def test_a_payment_above_the_debt_is_kept_in_full_with_a_warning(client,
                                                                 factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    response = pay(client, factory, project_id, supplier_id, D(3500))

    # 3000 of debt + 500 extra, recorded on the last expense that was reached.
    assert payments_of(factory, x) == [(D('2500'), 'nakit')]
    assert payments_of(factory, y) == [(D('1000'), 'nakit')]
    assert 'açık borcun' in response.get_data(as_text=True)
    # The schedule is capped at the plan, so it still reads "fully paid".
    assert factory.expense_paid_amounts(x) == [D('1000'), D('1000')]


def test_no_open_debt_records_nothing(client, factory):
    project_id = factory.project('toplu borcsuz')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D(1000))
    factory.expense_installment(expense_id, JAN, D(1000))
    factory.supplier_payment(expense_id, supplier_id, D(1000), 'nakit')
    factory.commit()

    response = pay(client, factory, project_id, supplier_id, D(500))

    assert 'ödenmemiş bir borcu bulunamadı' in response.get_data(as_text=True)
    assert len(payments_of(factory, expense_id)) == 1


def test_zero_and_negative_amounts_are_refused(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(0))
    pay(client, factory, project_id, supplier_id, D(-100))

    assert payments_of(factory, x) == []
    assert payments_of(factory, y) == []


def test_another_supplier_and_another_project_are_not_touched(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    other_supplier = factory.supplier(project_id, name='Baska')
    other_expense = factory.expense(project_id, other_supplier, amount=D(500))
    factory.expense_installment(other_expense, JAN, D(500))
    other_project = factory.project('baska proje')
    elsewhere = factory.expense(other_project, supplier_id, amount=D(500))
    factory.expense_installment(elsewhere, JAN, D(500))
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(3000))

    assert payments_of(factory, other_expense) == []
    assert payments_of(factory, elsewhere) == []
    assert factory.expense_paid_amounts(other_expense) == [D('0')]
    assert factory.expense_paid_amounts(elsewhere) == [D('0')]


def test_the_same_token_twice_records_one_payment(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()
    form = {'supplier_id': str(supplier_id), 'project_id': str(project_id),
            'amount': '1000', 'payment_date': '2026-06-15',
            'description': 'PYTEST toplu',
            'submission_token': factory.new_token()}

    client.post('/supplier_payment/new', data=form, follow_redirects=True)
    client.post('/supplier_payment/new', data=form, follow_redirects=True)
    track_route_payments(factory, supplier_id)

    assert payments_of(factory, x) == [(D('1000'), 'nakit')]
    assert payments_of(factory, y) == []


def test_the_audit_log_names_every_expense(client, factory):
    project_id, supplier_id, x, y = two_expenses(factory)
    factory.commit()

    pay(client, factory, project_id, supplier_id, D(2500))

    factory.cur.execute(
        "SELECT details FROM audit_logs WHERE action = 'supplier_payment_create' "
        "ORDER BY id DESC LIMIT 1")
    details = factory.cur.fetchone()[0]
    assert sorted(details['expense_ids']) == sorted([x, y])
    assert details['allocations'][str(x)] == 1500.0
    assert details['allocations'][str(y)] == 1000.0
