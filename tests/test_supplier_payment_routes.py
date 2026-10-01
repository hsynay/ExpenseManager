"""Editing and deleting a supplier payment, through the real routes.

Both routes recalculate the expense afterwards. They used to call
reconcile_expense_payments, which counted a check that was only handed over
('verildi') as paid. They now call reconcile_supplier_payments, the same
function every other expense action uses, so an expense looks the same no
matter which action touched it last.
"""
from datetime import date
from decimal import Decimal

D = Decimal
JAN = date(2026, 1, 20)
FEB = date(2026, 2, 20)


def expense_with_two_installments(factory, amount=D('2000')):
    project_id = factory.project('tedarikci odeme route')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=amount)
    factory.expense_installment(expense_id, JAN, amount / 2)
    factory.expense_installment(expense_id, FEB, amount / 2)
    return project_id, supplier_id, expense_id


def edit_form(amount='2000', check_due='2026-07-15'):
    return {'amount': amount, 'payment_date': '2026-06-15',
            'description': 'PYTEST duzenleme', 'check_due_date': check_due}


# --- edit ------------------------------------------------------------

def test_editing_a_payment_by_unpaid_check_does_not_mark_it_paid(client,
                                                                 factory):
    """The case the old function got wrong."""
    project_id, supplier_id, expense_id = expense_with_two_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='verildi')
    payment_id = factory.supplier_payment(expense_id, supplier_id, D('2000'),
                                          'çek', check_id=check_id)
    factory.commit()

    client.post('/supplier_payment/%d/edit' % payment_id, data=edit_form(),
                follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]
    assert factory.expense_paid_flags(expense_id) == [False, False]


def test_editing_a_payment_by_paid_check_still_counts(client, factory):
    project_id, supplier_id, expense_id = expense_with_two_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='odendi')
    payment_id = factory.supplier_payment(expense_id, supplier_id, D('2000'),
                                          'çek', check_id=check_id)
    factory.commit()

    client.post('/supplier_payment/%d/edit' % payment_id, data=edit_form(),
                follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000')]


def test_editing_a_cash_payment_redistributes_the_new_amount(client, factory):
    project_id, supplier_id, expense_id = expense_with_two_installments(factory)
    payment_id = factory.supplier_payment(expense_id, supplier_id, D('2000'),
                                          'nakit')
    factory.commit()

    client.post('/supplier_payment/%d/edit' % payment_id,
                data=edit_form(amount='1500'), follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('500')]


# --- delete ----------------------------------------------------------

def test_deleting_a_cash_payment_leaves_an_unpaid_check_uncounted(client,
                                                                  factory):
    """After the cash is gone only the unpaid check is left, and it counts
    for nothing. The old function would have spread it over the plan."""
    project_id, supplier_id, expense_id = expense_with_two_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('1000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=check_id)
    cash_id = factory.supplier_payment(expense_id, supplier_id, D('1000'),
                                       'nakit')
    factory.commit()

    client.post('/supplier_payment/%d/delete' % cash_id,
                data={'project_id': str(project_id)}, follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0')]


def test_deleting_one_of_two_cash_payments_keeps_the_other(client, factory):
    project_id, supplier_id, expense_id = expense_with_two_installments(factory)
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'nakit')
    second = factory.supplier_payment(expense_id, supplier_id, D('500'),
                                      'nakit')
    factory.commit()

    client.post('/supplier_payment/%d/delete' % second,
                data={'project_id': str(project_id)}, follow_redirects=True)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('0')]


# --- ordering ----------------------------------------------------------

def test_installments_due_the_same_day_are_filled_in_creation_order(client,
                                                                    factory):
    """The old function ordered by due date only, so two installments on the
    same day were filled in no fixed order. Ties now go by id."""
    project_id = factory.project('ayni gun taksit')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('1500'))
    factory.expense_installment(expense_id, JAN, D('1000'))
    factory.expense_installment(expense_id, JAN, D('500'))
    payment_id = factory.supplier_payment(expense_id, supplier_id, D('1000'),
                                          'nakit')
    factory.commit()

    client.post('/supplier_payment/%d/edit' % payment_id,
                data=edit_form(amount='1000'), follow_redirects=True)

    # expense_state orders by due_date, id: the first created is filled first.
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('0')]
