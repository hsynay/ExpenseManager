"""One rule for "the plan of an expense", on all three screens.

    plan = sum of the installments, or the expense amount when there are none

The cooperative report ("Kalan Taahhüt"), the profitability screen
("Taahhüt") and project_overview (unpaid expense) all add up the plan. They
used to look only at expense_schedule, so an expense entered without
installments was not in anyone's commitment: the remaining debt looked smaller
and the forecast profit bigger than they are. These tests run one scenario
through the three screens and require the same answer.
"""
import inspect
from datetime import date
from decimal import Decimal

import expense_plan
import profitability
from profitability import load_profitability

D = Decimal
JUNE = date(2026, 6, 15)
JULY = date(2026, 7, 1)
AUG = date(2026, 8, 1)


def build(factory):
    """A cooperative project, every expense dated June 2026.

    expense  amount  installments        valid payments          plan
    P        3000    none                none                    3000
    Q        2000    1000 + 1000         cash 500                2000
    R        1000    none                cash 400                1000
    S        1500    one of 1000         none                    1000 (schedule wins)
    T         800    one of 800          check 'verildi' 800     800  (closes nothing)
    petty cash 60. One sold flat of 10000.

    plan total 7800, paid 900 (+ 60 petty), still to pay 6900.
    """
    project_id = factory.project('plan kurali', project_type='cooperative')
    customer_id = factory.customer()
    factory.flat(project_id, owner_id=customer_id, total_price=D(10000))
    supplier_id = factory.supplier(project_id)

    factory.expense(project_id, supplier_id, D(3000), title='P',
                    expense_date=JUNE)

    q = factory.expense(project_id, supplier_id, D(2000), title='Q',
                        expense_date=JUNE)
    factory.expense_installment(q, JULY, D(1000))
    factory.expense_installment(q, AUG, D(1000))
    factory.supplier_payment(q, supplier_id, D(500), 'nakit',
                             payment_date=JUNE)

    r = factory.expense(project_id, supplier_id, D(1000), title='R',
                        expense_date=JUNE)
    factory.supplier_payment(r, supplier_id, D(400), 'nakit',
                             payment_date=JUNE)

    s = factory.expense(project_id, supplier_id, D(1500), title='S',
                        expense_date=JUNE)
    factory.expense_installment(s, JULY, D(1000))

    t = factory.expense(project_id, supplier_id, D(800), title='T',
                        expense_date=JUNE)
    factory.expense_installment(t, JULY, D(800))
    check_id = factory.outgoing_check(supplier_id, D(800), status='verildi')
    factory.supplier_payment(t, supplier_id, D(800), 'çek', check_id=check_id,
                             payment_date=JUNE)

    factory.petty_cash(project_id, D(60), expense_date=JUNE)
    factory.commit()
    return project_id


def three_screens(client, factory, render_context, project_id):
    """(profitability dict, project_overview context, cooperative report)."""
    f = load_profitability(factory.cur, project_id)

    response = client.get('/project/%d/overview' % project_id)
    assert response.status_code == 200
    overview = render_context.latest('project_overview.html')

    response = client.get('/reports/cooperative/%d/2026/6' % project_id)
    assert response.status_code == 200
    report = render_context.latest('coop_report.html')['report_data']
    return f, overview, report


# --- the three screens agree -------------------------------------------------------

def test_the_three_screens_give_the_same_commitment(client, factory,
                                                    render_context):
    project_id = build(factory)

    f, overview, report = three_screens(client, factory, render_context,
                                        project_id)

    assert f['committed'] == D(6900)
    assert overview['total_unpaid_expense'] == D(6900)
    assert report['remaining_commitment'] == D(6900)


def test_the_three_screens_give_the_same_plan_total(client, factory,
                                                    render_context):
    project_id = build(factory)

    f, overview, report = three_screens(client, factory, render_context,
                                        project_id)

    # 3000 + 2000 + 1000 + 1000 (S: the schedule, not its 1500 amount) + 800
    assert f['planned_total'] == D(7800)
    assert report['planned_to_date'] == D(7800)
    # project_overview does not show the plan total on its own; the unpaid
    # figure is plan + petty - (paid + petty), which pins it.
    assert overview['total_unpaid_expense'] + f['spent_paid'] == D(7800)


def test_the_three_screens_give_the_same_paid_figure(client, factory,
                                                     render_context):
    project_id = build(factory)

    f, overview, report = three_screens(client, factory, render_context,
                                        project_id)

    # cash 500 + 400, plus 60 petty cash. The handed over check (800) is not
    # money that left.
    assert f['spent'] == D(960)
    assert overview['total_paid_expense'] == D(960)
    assert report['current_expense'] == D(960)


def test_paid_plus_still_to_pay_is_the_plan_plus_petty_cash(client, factory,
                                                            render_context):
    project_id = build(factory)

    f, overview, report = three_screens(client, factory, render_context,
                                        project_id)

    assert f['total_cost'] == D(7860)
    assert report['current_expense'] + report['remaining_commitment'] == D(7860)
    assert overview['total_paid_expense'] + overview['total_unpaid_expense'] \
        == D(7860)


# --- what the rule changes -------------------------------------------------------

def test_an_expense_without_installments_is_in_the_commitment(factory):
    """P alone is 3000 that is owed. Before, it was nowhere."""
    project_id = factory.project('plansiz tek')
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(3000), title='P')

    f = load_profitability(factory.cur, project_id)

    assert f['planned_total'] == D(3000)
    assert f['committed'] == D(3000)
    assert f['total_cost'] == D(3000)


def test_forecast_profit_is_not_flattered_by_a_missing_plan(factory):
    project_id = factory.project('plansiz kar')
    customer_id = factory.customer()
    factory.flat(project_id, owner_id=customer_id, total_price=D(10000))
    supplier_id = factory.supplier(project_id)
    factory.expense(project_id, supplier_id, D(3000), title='P')

    f = load_profitability(factory.cur, project_id)

    assert f['forecast_profit'] == D(10000) - D(3000)


def test_the_schedule_wins_when_an_expense_has_installments(factory):
    project_id = factory.project('plan kazanir')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D(1500), title='S')
    factory.expense_installment(expense_id, JULY, D(1000))

    f = load_profitability(factory.cur, project_id)

    assert f['planned_total'] == D(1000)


def test_a_payment_on_an_expense_without_installments_is_not_overpaid(factory):
    """Planned used to be 0 for such an expense, so any payment on it looked
    like an overpayment."""
    project_id = factory.project('plansiz odeme')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D(1000), title='R')
    factory.supplier_payment(expense_id, supplier_id, D(400), 'nakit')

    f = load_profitability(factory.cur, project_id)

    assert f['overpaid_count'] == 0
    assert f['committed'] == D(600)


def test_a_real_overpayment_without_installments_is_still_flagged(factory):
    project_id = factory.project('plansiz fazla')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, D(500), title='R')
    factory.supplier_payment(expense_id, supplier_id, D(700), 'nakit')

    f = load_profitability(factory.cur, project_id)

    assert (f['overpaid_count'], f['overpaid_total']) == (1, D(200))


# --- project_overview lists the expense too -----------------------------------------

def test_overview_lists_an_expense_without_installments(client, factory,
                                                        render_context):
    project_id = build(factory)

    response = client.get('/project/%d/overview' % project_id)
    assert response.status_code == 200
    items = render_context.latest('project_overview.html')['expense_items']

    by_title = {i['description']: i for i in items}
    assert by_title['PYTEST P']['amount'] == D(3000)
    assert by_title['PYTEST P']['status'] in ('Gecikmiş', 'Bekleniyor')
    # R has 400 of 1000 paid in cash.
    assert by_title['PYTEST R']['amount'] == D(1000)
    assert by_title['PYTEST R']['status'].startswith('Kısmen')


def test_overview_list_adds_up_to_the_plan_total(client, factory,
                                                 render_context):
    project_id = build(factory)

    client.get('/project/%d/overview' % project_id)
    items = render_context.latest('project_overview.html')['expense_items']

    planned = [i for i in items if i['details'].startswith('Planlı Gider')]
    assert sum(i['amount'] for i in planned) == D(7800)


# --- one definition ------------------------------------------------------------------

def test_the_rule_is_written_in_one_place():
    """Every screen builds its query from the shared fragments, so a change to
    the rule is a change in expense_plan.py and nowhere else."""
    import routes.projects as projects
    import routes.reports as reports

    assert expense_plan.PLAN_AMOUNT in profitability.EXPENSE_SQL
    for module in (reports, projects):
        source = inspect.getsource(module)
        assert 'expense_plan' in source, module.__name__
        assert 'PLAN_AMOUNT' in source and 'PLAN_JOIN' in source, module.__name__


def test_no_screen_adds_up_the_schedule_on_its_own():
    """The old way to get the plan was SUM over expense_schedule. It must not
    come back in the three screens."""
    import routes.projects as projects
    import routes.reports as reports

    for text in (profitability.EXPENSE_SQL, inspect.getsource(reports),
                 inspect.getsource(projects)):
        flat = ' '.join(text.split()).upper()
        assert 'SUM(ES.AMOUNT)' not in flat
