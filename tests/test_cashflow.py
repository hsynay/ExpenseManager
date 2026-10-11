"""/cashflow: the week by week forecast of money in and out.

"Today" is fixed to Wednesday 17 June 2026, so the first week is Monday
15 June. Every test works inside its own project and passes project_id, which
keeps the sample data in the test database out of the numbers.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from cashflow import parse_money_arg

D = Decimal
TODAY = date(2026, 6, 17)               # a Wednesday
MONDAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    import routes.forecast
    monkeypatch.setattr(routes.forecast, 'today_istanbul', lambda: TODAY)


def world(factory):
    """A project with one customer flat and one supplier expense."""
    project_id = factory.project('nakit akisi')
    customer_id = factory.customer(first='XlsxNakit')
    flat_id = factory.flat(project_id, owner_id=customer_id)
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id)
    return project_id, customer_id, flat_id, supplier_id, expense_id


def forecast(client, render_context, project_id, query=''):
    response = client.get('/cashflow?project_id=%d%s' % (project_id, query))
    assert response.status_code == 200
    return render_context.latest('cashflow.html')['result']


def week(result, start):
    return next(w for w in result['weeks'] if w['key'] == start.isoformat())


# --- weeks, totals, running total ---------------------------------------------

def test_items_land_in_their_monday_week_and_the_total_runs(client, factory,
                                                             render_context):
    project_id, _, flat_id, _, expense_id = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.installment(flat_id, date(2026, 6, 24), D('2000'))
    factory.installment(flat_id, date(2026, 6, 21), D('50'))     # Sunday
    factory.expense_installment(expense_id, date(2026, 6, 18), D('500'))
    factory.commit()

    result = forecast(client, render_context, project_id)

    first, second = week(result, MONDAY), week(result, date(2026, 6, 22))
    # Sunday 21 June still belongs to the week that began on Monday 15 June.
    assert (first['incoming'], first['outgoing'], first['net']) == (
        D('1050'), D('500'), D('550'))
    assert first['cumulative'] == D('550')
    assert (second['incoming'], second['cumulative']) == (D('2000'), D('2550'))
    assert result['weeks'][0]['start'] == MONDAY
    assert result['total_in'] == D('3050') and result['total_out'] == D('500')


def test_opening_balance_is_added_to_the_running_total_only(client, factory,
                                                             render_context):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.commit()

    result = forecast(client, render_context, project_id, '&opening=10.000,50')

    assert result['weeks'][0]['net'] == D('1000')
    assert result['weeks'][0]['cumulative'] == D('11000.50')
    assert result['closing'] == D('11000.50')
    assert result['total_net'] == D('1000')


def test_a_broken_opening_balance_counts_as_zero_and_warns(client, factory,
                                                           render_context):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.commit()

    response = client.get('/cashflow?project_id=%d&opening=abc' % project_id)

    assert response.status_code == 200
    assert 'geçerli bir sayı değil' in response.get_data(as_text=True)
    assert render_context.latest('cashflow.html')['result']['opening'] == 0


def test_parse_money_arg():
    assert parse_money_arg('') == 0
    assert parse_money_arg('1.500.000,50') == D('1500000.50')
    assert parse_money_arg('1500000.5') == D('1500000.50')
    assert parse_money_arg('1,500.25') == D('1500.25')
    assert parse_money_arg('-250000') == D('-250000')
    assert parse_money_arg('12,5 ₺') == D('12.50')
    for bad in ('abc', '1e99', 'nan', '1.2.3.4x'):
        with pytest.raises(ValueError):
            parse_money_arg(bad)


# --- what counts --------------------------------------------------------------

def test_paid_installments_do_not_count_and_partial_ones_count_the_rest(
        client, factory, render_context):
    project_id, _, flat_id, _, expense_id = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'),
                        paid_amount=D('400'))
    factory.installment(flat_id, date(2026, 6, 18), D('900'),
                        paid_amount=D('900'), is_paid=True)
    factory.expense_installment(expense_id, date(2026, 6, 17), D('700'),
                                paid_amount=D('200'))
    factory.commit()

    first = week(forecast(client, render_context, project_id), MONDAY)

    assert first['incoming'] == D('600')
    assert first['outgoing'] == D('500')


def test_portfolio_checks_count_on_their_due_date_and_bounced_ones_never(
        client, factory, render_context):
    project_id, customer_id, flat_id, supplier_id, expense_id = world(factory)
    due = date(2026, 6, 25)                                   # week of 22 June
    for status in ('portfoyde', 'tahsil_edildi', 'karsiliksiz'):
        check_id = factory.check(customer_id, D('3000'), status=status,
                                 due_date=due)
        factory.payment(flat_id, D('3000'), payment_method='çek',
                        check_id=check_id)
    for status in ('verildi', 'odendi', 'karsiliksiz'):
        check_id = factory.outgoing_check(supplier_id, D('1500'), status=status,
                                          due_date=due)
        factory.supplier_payment(expense_id, supplier_id, D('1500'),
                                 payment_method='çek', check_id=check_id)
    factory.commit()

    result = forecast(client, render_context, project_id)

    target = week(result, date(2026, 6, 22))
    assert target['parts']['check'] == D('3000')              # only portfoyde
    assert target['parts']['outgoing_check'] == D('1500')     # only verildi
    assert result['total_in'] == D('3000')
    assert result['total_out'] == D('1500')


def test_a_check_given_for_an_installment_is_not_counted_twice(client, factory,
                                                               render_context):
    project_id, customer_id, flat_id, supplier_id, expense_id = world(factory)
    # Two installments of 1000. A 1200 check was handed over for them.
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.installment(flat_id, date(2026, 7, 1), D('1000'))
    check_id = factory.check(customer_id, D('1200'), status='portfoyde',
                             due_date=date(2026, 6, 30))
    factory.payment(flat_id, D('1200'), payment_method='çek', check_id=check_id)
    # Same thing on the expense side.
    factory.expense_installment(expense_id, date(2026, 6, 17), D('1000'))
    factory.expense_installment(expense_id, date(2026, 7, 1), D('1000'))
    out_id = factory.outgoing_check(supplier_id, D('1200'), status='verildi',
                                    due_date=date(2026, 6, 30))
    factory.supplier_payment(expense_id, supplier_id, D('1200'),
                             payment_method='çek', check_id=out_id)
    factory.commit()

    result = forecast(client, render_context, project_id)

    # 2000 owed in total, and 2000 expected: not 3200.
    assert result['total_in'] == D('2000')
    assert result['total_out'] == D('2000')
    assert week(result, MONDAY)['incoming'] == 0       # first installment is covered
    last = week(result, date(2026, 6, 29))
    assert last['parts']['check'] == D('1200')
    assert last['parts']['installment'] == D('800')


def test_late_items_get_one_separate_row_that_feeds_the_total(client, factory,
                                                              render_context):
    project_id, _, flat_id, _, expense_id = world(factory)
    factory.installment(flat_id, date(2026, 6, 1), D('4000'))     # before Monday
    factory.expense_installment(expense_id, date(2026, 5, 20), D('1000'))
    factory.installment(flat_id, date(2026, 6, 16), D('10'))      # this week
    factory.commit()

    result = forecast(client, render_context, project_id)

    late = result['weeks'][0]
    assert late['late'] and late['key'] == 'late'
    assert (late['incoming'], late['outgoing']) == (D('4000'), D('1000'))
    assert week(result, MONDAY)['incoming'] == D('10')
    assert week(result, MONDAY)['cumulative'] == D('3010')
    assert result['months'][0]['late']


def test_nothing_late_means_no_late_row(client, factory, render_context):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('10'))
    factory.commit()

    result = forecast(client, render_context, project_id)

    assert not any(w['late'] for w in result['weeks'])


# --- period, months, warnings --------------------------------------------------

def test_period_chooser_controls_how_far_the_forecast_reaches(client, factory,
                                                              render_context):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 10, 20), D('777'))    # ~4 months out
    factory.installment(flat_id, date(2027, 3, 3), D('555'))      # ~9 months out
    factory.commit()

    three = forecast(client, render_context, project_id)
    six = forecast(client, render_context, project_id, '&months=6')
    twelve = forecast(client, render_context, project_id, '&months=12')
    odd = forecast(client, render_context, project_id, '&months=5')

    assert [r['total_in'] for r in (three, six, twelve)] == [
        D('0'), D('777'), D('1332')]
    # 17 June + 3 months is in the week of Monday 14 September, so the table
    # runs 15 June ... 20 September: 14 weeks.
    assert len(three['weeks']) == 14
    assert len(twelve['weeks']) > len(six['weeks']) > len(three['weeks'])
    assert len(odd['weeks']) == len(three['weeks'])       # unknown value -> 3


def test_the_monthly_table_adds_up_to_the_weekly_one(client, factory,
                                                     render_context):
    project_id, _, flat_id, _, expense_id = world(factory)
    for n, day in enumerate((date(2026, 6, 1), date(2026, 6, 30), date(2026, 7, 1),
                             date(2026, 7, 31), date(2026, 8, 31),
                             date(2026, 9, 20))):
        factory.installment(flat_id, day, D(100 * (n + 1)))
        factory.expense_installment(expense_id, day + timedelta(days=1),
                                    D(10 * (n + 1)))
    factory.commit()

    result = forecast(client, render_context, project_id, '&opening=500')

    for key in ('incoming', 'outgoing', 'net'):
        assert sum(m[key] for m in result['months']) == sum(
            w[key] for w in result['weeks'])
    assert result['months'][-1]['cumulative'] == result['weeks'][-1]['cumulative']
    assert result['months'][-1]['cumulative'] == result['closing']


def test_weeks_that_run_below_zero_are_marked_red(client, factory,
                                                  render_context):
    project_id, _, flat_id, _, expense_id = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.expense_installment(expense_id, date(2026, 6, 18), D('5000'))
    factory.installment(flat_id, date(2026, 7, 1), D('6000'))
    factory.commit()

    response = client.get('/cashflow?project_id=%d' % project_id)
    result = render_context.latest('cashflow.html')['result']
    html = response.get_data(as_text=True)

    negatives = [w['label'] for w in result['weeks'] if w['negative']]
    assert negatives and result['first_negative']['start'] == MONDAY
    assert result['lowest']['cumulative'] == D('-4000')
    assert 'table-danger' in html and 'eksiye düşüyor' in html


def test_no_red_when_the_total_never_goes_below_zero(client, factory,
                                                     render_context):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.commit()

    response = client.get('/cashflow?project_id=%d' % project_id)

    assert render_context.latest('cashflow.html')['result']['first_negative'] is None
    assert 'hiçbir hafta eksiye düşmüyor' in response.get_data(as_text=True)


# --- the page itself ----------------------------------------------------------

def test_cashflow_needs_a_login_and_only_answers_get():
    import app as app_module
    with app_module.app.test_client() as anonymous:
        response = anonymous.get('/cashflow')
    assert response.status_code == 302 and '/login' in response.headers['Location']


def test_cashflow_does_not_accept_post(client):
    assert client.post('/cashflow').status_code == 405


def test_cashflow_runs_two_queries_whatever_the_period(client, factory,
                                                       query_counter):
    project_id, _, flat_id, _, expense_id = world(factory)
    for n in range(40):
        factory.installment(flat_id, date(2026, 6, 20) + timedelta(days=9 * n),
                            D('10'))
        factory.expense_installment(expense_id,
                                    date(2026, 6, 21) + timedelta(days=9 * n),
                                    D('10'))
    factory.commit()

    counts = {}
    for months in (3, 6, 12):
        query_counter.clear()
        assert client.get('/cashflow?project_id=%d&months=%d'
                          % (project_id, months)).status_code == 200
        counts[months] = len(query_counter)

    # The project list and the one query behind all the weeks and months.
    assert counts == {3: 2, 6: 2, 12: 2}


def test_cashflow_page_has_the_table_the_chart_and_the_navbar_link(client,
                                                                   factory):
    project_id, _, flat_id, _, _ = world(factory)
    factory.installment(flat_id, date(2026, 6, 17), D('1000'))
    factory.commit()

    html = client.get('/cashflow?project_id=%d' % project_id).get_data(as_text=True)

    assert 'id="weekly-table"' in html and 'id="monthly-table"' in html
    assert 'id="cashflow-chart"' in html and 'chart.js' in html
    assert 'href="/cashflow"' in html           # the navbar entry
    assert '1.000 ₺' in html                    # money like the other pages
    assert '15.06.2026' in html                 # dates like the other pages
