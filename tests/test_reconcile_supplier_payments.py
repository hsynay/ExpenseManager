"""Characterization tests for reconcile_supplier_payments.

This is the expense side twin of reconcile_customer_payments and it applies
the same rule correctly: cash counts, and an outgoing check counts only once
it is marked 'odendi'. A check that was handed over but not yet paid
('verildi') and a bounced one ('karsiliksiz') count for nothing.

Compare with test_reconcile_expense_payments.py, which covers the other
function writing to this same table under a different, wrong rule.
"""
from datetime import date
from decimal import Decimal

from app import reconcile_supplier_payments

D = Decimal
JAN = date(2026, 1, 20)
FEB = date(2026, 2, 20)
MAR = date(2026, 3, 20)


def make_expense_with_three_installments(factory, amount=D('1000')):
    """An expense of 3000 split into three equal installments."""
    project_id = factory.project('gider')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=amount * 3)
    for due in (JAN, FEB, MAR):
        factory.expense_installment(expense_id, due, amount)
    return project_id, supplier_id, expense_id


def test_no_payment_leaves_every_installment_open(factory, cur):
    _, _, expense_id = make_expense_with_three_installments(factory)

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0'), D('0')]
    assert factory.expense_paid_flags(expense_id) == [False, False, False]


def test_cash_fills_the_oldest_installment_first(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    factory.supplier_payment(expense_id, supplier_id, D('1500'), 'nakit')

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('500'), D('0')]
    assert factory.expense_paid_flags(expense_id) == [True, False, False]


def test_check_handed_over_but_not_paid_closes_nothing(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0'), D('0')]
    assert factory.expense_paid_flags(expense_id) == [False, False, False]


def test_bounced_outgoing_check_closes_nothing(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'),
                                      status='karsiliksiz')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0'), D('0')]


def test_paid_check_counts_like_cash(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'), status='odendi')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('1000')]
    assert factory.expense_paid_flags(expense_id) == [True, True, True]


def test_only_the_paid_part_of_a_mix_counts(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    paid = factory.outgoing_check(supplier_id, D('1000'), status='odendi')
    given = factory.outgoing_check(supplier_id, D('1000'), status='verildi')
    bounced = factory.outgoing_check(supplier_id, D('1000'),
                                     status='karsiliksiz')
    factory.supplier_payment(expense_id, supplier_id, D('500'), 'nakit')
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=paid)
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=given)
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=bounced)

    reconcile_supplier_payments(cur, expense_id)

    # 500 cash + 1000 paid check = 1500 of real money.
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('500'), D('0')]


def test_marking_a_check_paid_moves_the_expense_forward(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('2000'), 'çek',
                             check_id=check_id)

    reconcile_supplier_payments(cur, expense_id)
    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0'), D('0')]

    cur.execute("UPDATE outgoing_checks SET status = 'odendi' WHERE id = %s",
                (check_id,))
    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('0')]


def test_money_beyond_the_plan_is_not_carried_anywhere(factory, cur):
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    factory.supplier_payment(expense_id, supplier_id, D('9000'), 'nakit')

    reconcile_supplier_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('1000')]


def test_reconcile_ignores_other_expenses(factory, cur):
    project_id = factory.project('iki gider')
    supplier_id = factory.supplier(project_id)
    first = factory.expense(project_id, supplier_id, amount=D('1000'),
                            title='birinci')
    second = factory.expense(project_id, supplier_id, amount=D('1000'),
                             title='ikinci')
    factory.expense_installment(first, JAN, D('1000'))
    factory.expense_installment(second, JAN, D('1000'))
    factory.supplier_payment(first, supplier_id, D('1000'), 'nakit')

    reconcile_supplier_payments(cur, first)

    assert factory.expense_paid_amounts(first) == [D('1000')]
    assert factory.expense_paid_amounts(second) == [D('0')]
