"""Characterization tests for reconcile_expense_payments — A KNOWN BUG.

WARNING: these tests describe behaviour that is wrong on purpose.

app.py has two functions writing paid_amount into the same table:

    reconcile_supplier_payments   filters on  oc.status = 'odendi'      (right)
    reconcile_expense_payments    filters on  oc.status != 'karsiliksiz' (wrong)

The second one therefore treats a check that was only handed over
('verildi') as if the supplier had already been paid. It is still called by
edit_supplier_payment and delete_supplier_payment, so an expense changes
appearance depending on which action touched it last.

This is pending item 3 in CLAUDE.md, to be fixed after the test suite is in
place by deleting reconcile_expense_payments and pointing both callers at
reconcile_supplier_payments.

When that fix lands, the tests marked WRONG below are expected to fail. That
is the point: they mark exactly what changes. Delete them and keep the twin
file as the single description of the rule.
"""
from datetime import date
from decimal import Decimal

from app import reconcile_expense_payments, reconcile_supplier_payments

D = Decimal
JAN = date(2026, 1, 20)
FEB = date(2026, 2, 20)
MAR = date(2026, 3, 20)


def make_expense_with_three_installments(factory, amount=D('1000')):
    project_id = factory.project('gider hatali')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=amount * 3)
    for due in (JAN, FEB, MAR):
        factory.expense_installment(expense_id, due, amount)
    return project_id, supplier_id, expense_id


def test_cash_behaves_the_same_as_the_correct_function(factory, cur):
    """Cash is not affected by the bug; both functions agree here."""
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    factory.supplier_payment(expense_id, supplier_id, D('1500'), 'nakit')

    reconcile_expense_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('500'), D('0')]


def test_paid_check_counts(factory, cur):
    """A check marked 'odendi' counts, as it should."""
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'), status='odendi')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_expense_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('1000')]


def test_bounced_check_closes_nothing(factory, cur):
    """The one check state the wrong filter does exclude."""
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'),
                                      status='karsiliksiz')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_expense_payments(cur, expense_id)

    assert factory.expense_paid_amounts(expense_id) == [D('0'), D('0'), D('0')]


def test_WRONG_check_only_handed_over_is_treated_as_paid(factory, cur):
    """WRONG (pending item 3): 'verildi' must not close an installment.

    The supplier holds a check that no bank has paid yet, but the expense
    already shows as settled.
    """
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('3000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('3000'), 'çek',
                             check_id=check_id)

    reconcile_expense_payments(cur, expense_id)

    # Today's behaviour. The correct result is [0, 0, 0].
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('1000')]
    assert factory.expense_paid_flags(expense_id) == [True, True, True]


def test_WRONG_the_two_functions_disagree_on_the_same_expense(factory, cur):
    """WRONG (pending item 3): the number depends on which action ran last.

    This is what the user sees in practice: editing a supplier payment marks
    the installments paid, and the next action that recalculates the same
    expense silently puts them back.
    """
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    check_id = factory.outgoing_check(supplier_id, D('2000'), status='verildi')
    factory.supplier_payment(expense_id, supplier_id, D('2000'), 'çek',
                             check_id=check_id)

    # What edit_supplier_payment / delete_supplier_payment produce.
    reconcile_expense_payments(cur, expense_id)
    after_wrong = factory.expense_paid_amounts(expense_id)

    # What pay_expense_installment / update_check_status / manage_expense_plan
    # produce for the very same rows.
    reconcile_supplier_payments(cur, expense_id)
    after_right = factory.expense_paid_amounts(expense_id)

    assert after_wrong == [D('1000'), D('1000'), D('0')]
    assert after_right == [D('0'), D('0'), D('0')]
    assert after_wrong != after_right


def test_WRONG_mixed_states_count_everything_except_bounced(factory, cur):
    """WRONG (pending item 3): only 'karsiliksiz' is filtered out."""
    _, supplier_id, expense_id = make_expense_with_three_installments(factory)
    paid = factory.outgoing_check(supplier_id, D('1000'), status='odendi')
    given = factory.outgoing_check(supplier_id, D('1000'), status='verildi')
    bounced = factory.outgoing_check(supplier_id, D('1000'),
                                     status='karsiliksiz')
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=paid)
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=given)
    factory.supplier_payment(expense_id, supplier_id, D('1000'), 'çek',
                             check_id=bounced)

    reconcile_expense_payments(cur, expense_id)

    # 1000 real money, but 2000 is counted. Correct result: [1000, 0, 0].
    assert factory.expense_paid_amounts(expense_id) == [D('1000'), D('1000'), D('0')]
