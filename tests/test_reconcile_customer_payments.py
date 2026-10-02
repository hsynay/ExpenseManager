"""Characterization tests for reconcile_customer_payments.

The business rule for the income side: a payment only closes debt when the
money really moved. Cash always counts. A check counts only once it is marked
'tahsil_edildi'. A check still in the portfolio ('portfoyde') and a bounced
one ('karsiliksiz') count for nothing.

These tests record what the function does today. They are not a wish list.
"""
from datetime import date
from decimal import Decimal

from app import reconcile_customer_payments

D = Decimal
JAN = date(2026, 1, 10)
FEB = date(2026, 2, 10)
MAR = date(2026, 3, 10)


def make_flat_with_three_installments(factory, amount=D('1000')):
    """A flat owing 3000 in three equal monthly installments."""
    project_id = factory.project('gelir')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id,
                           total_price=amount * 3)
    for due in (JAN, FEB, MAR):
        factory.installment(flat_id, due, amount)
    return project_id, customer_id, flat_id


def test_no_payment_leaves_every_installment_open(factory, cur):
    _, _, flat_id = make_flat_with_three_installments(factory)

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0'), D('0')]
    assert factory.paid_flags(flat_id) == [False, False, False]


def test_cash_fills_the_oldest_installment_first(factory, cur):
    _, _, flat_id = make_flat_with_three_installments(factory)
    factory.payment(flat_id, D('1500'), 'nakit')

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('1000'), D('500'), D('0')]
    assert factory.paid_flags(flat_id) == [True, False, False]


def test_cash_covering_everything_closes_all_installments(factory, cur):
    _, _, flat_id = make_flat_with_three_installments(factory)
    factory.payment(flat_id, D('3000'), 'nakit')

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000'), D('1000')]
    assert factory.paid_flags(flat_id) == [True, True, True]


def test_money_beyond_the_plan_is_not_carried_anywhere(factory, cur):
    _, _, flat_id = make_flat_with_three_installments(factory)
    factory.payment(flat_id, D('5000'), 'nakit')

    reconcile_customer_payments(cur, flat_id)

    # The extra 2000 simply has nowhere to go; no installment goes above its
    # own amount.
    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000'), D('1000')]


def test_check_in_the_portfolio_closes_nothing(factory, cur):
    _, customer_id, flat_id = make_flat_with_three_installments(factory)
    check_id = factory.check(customer_id, D('3000'), status='portfoyde')
    factory.payment(flat_id, D('3000'), 'çek', check_id=check_id)

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0'), D('0')]
    assert factory.paid_flags(flat_id) == [False, False, False]


def test_bounced_check_closes_nothing(factory, cur):
    _, customer_id, flat_id = make_flat_with_three_installments(factory)
    check_id = factory.check(customer_id, D('3000'), status='karsiliksiz')
    factory.payment(flat_id, D('3000'), 'çek', check_id=check_id)

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0'), D('0')]


def test_cleared_check_counts_like_cash(factory, cur):
    _, customer_id, flat_id = make_flat_with_three_installments(factory)
    check_id = factory.check(customer_id, D('3000'), status='tahsil_edildi')
    factory.payment(flat_id, D('3000'), 'çek', check_id=check_id)

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000'), D('1000')]
    assert factory.paid_flags(flat_id) == [True, True, True]


def test_only_the_cleared_part_of_a_mix_counts(factory, cur):
    _, customer_id, flat_id = make_flat_with_three_installments(factory)
    cleared = factory.check(customer_id, D('1000'), status='tahsil_edildi')
    portfolio = factory.check(customer_id, D('1000'), status='portfoyde')
    bounced = factory.check(customer_id, D('1000'), status='karsiliksiz')
    factory.payment(flat_id, D('500'), 'nakit')
    factory.payment(flat_id, D('1000'), 'çek', check_id=cleared)
    factory.payment(flat_id, D('1000'), 'çek', check_id=portfolio)
    factory.payment(flat_id, D('1000'), 'çek', check_id=bounced)

    reconcile_customer_payments(cur, flat_id)

    # 500 cash + 1000 cleared check = 1500 of real money.
    assert factory.paid_amounts(flat_id) == [D('1000'), D('500'), D('0')]


def test_a_check_turning_bad_takes_its_money_back(factory, cur):
    """Reconcile starts from zero every time, so a status change is undone."""
    _, customer_id, flat_id = make_flat_with_three_installments(factory)
    check_id = factory.check(customer_id, D('2000'), status='tahsil_edildi')
    factory.payment(flat_id, D('2000'), 'çek', check_id=check_id)

    reconcile_customer_payments(cur, flat_id)
    assert factory.paid_amounts(flat_id) == [D('1000'), D('1000'), D('0')]

    cur.execute("UPDATE checks SET status = 'karsiliksiz' WHERE id = %s",
                (check_id,))
    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('0'), D('0'), D('0')]
    assert factory.paid_flags(flat_id) == [False, False, False]


def test_a_payment_without_a_check_row_counts_as_cash(factory, cur):
    """payment_method is the deciding field; a null check_id means cash."""
    _, _, flat_id = make_flat_with_three_installments(factory)
    factory.payment(flat_id, D('1000'), 'nakit', check_id=None)

    reconcile_customer_payments(cur, flat_id)

    assert factory.paid_amounts(flat_id) == [D('1000'), D('0'), D('0')]


def test_reconcile_ignores_other_flats(factory, cur):
    """Two flats in the same project must not affect each other."""
    project_id = factory.project('iki daire')
    customer_id = factory.customer()
    flat_a = factory.flat(project_id, owner_id=customer_id, flat_no=1)
    flat_b = factory.flat(project_id, owner_id=customer_id, flat_no=2)
    factory.installment(flat_a, JAN, D('1000'))
    factory.installment(flat_b, JAN, D('1000'))
    factory.payment(flat_a, D('1000'), 'nakit')

    reconcile_customer_payments(cur, flat_a)

    assert factory.paid_amounts(flat_a) == [D('1000')]
    assert factory.paid_amounts(flat_b) == [D('0')]
