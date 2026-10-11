"""/overdue: late installments and late portfolio checks, in age buckets.

"Today" is Wednesday 17 June 2026. The report lists every customer in the
database, so each test looks only at the customers it created (names start
with PYTEST).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from overdue import bucket_totals

D = Decimal
TODAY = date(2026, 6, 17)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    import routes.forecast
    monkeypatch.setattr(routes.forecast, 'today_istanbul', lambda: TODAY)


def days_ago(n):
    return TODAY - timedelta(days=n)


def report(client, render_context):
    response = client.get('/overdue')
    assert response.status_code == 200
    ctx = render_context.latest('overdue.html')
    return ctx, response.get_data(as_text=True)


def mine(rows):
    return [r for r in rows if r['name'].startswith('PYTEST ')]


def customer_with_flat(factory, first, project_id=None, flat_no=1):
    project_id = project_id or factory.project('gecikme ' + first)
    customer_id = factory.customer(first=first)
    flat_id = factory.flat(project_id, owner_id=customer_id, flat_no=flat_no)
    return project_id, customer_id, flat_id


# --- buckets ------------------------------------------------------------------

def test_bucket_edges_are_30_60_90_days(client, factory, render_context):
    _, _, flat_id = customer_with_flat(factory, 'XlsxGecKova')
    for late, amount in ((30, 1), (31, 2), (60, 4), (61, 8), (90, 16), (91, 32)):
        factory.installment(flat_id, days_ago(late), D(amount))
    factory.installment(flat_id, TODAY, D('1000'))                    # due today
    factory.installment(flat_id, TODAY + timedelta(days=1), D('1000'))
    factory.commit()

    ctx, _ = report(client, render_context)

    row, = mine(ctx['installments'])
    assert (row['b0_30'], row['b31_60'], row['b61_90'], row['b90_plus']) == (
        D('1'), D('6'), D('24'), D('32'))
    assert row['total'] == D('63') and row['oldest_days'] == 91
    assert row['items'] == 6


def test_only_the_unpaid_part_is_counted(client, factory, render_context):
    _, _, flat_id = customer_with_flat(factory, 'XlsxGecKismi')
    factory.installment(flat_id, days_ago(10), D('1000'), paid_amount=D('400'))
    factory.installment(flat_id, days_ago(20), D('900'), paid_amount=D('900'),
                        is_paid=True)
    factory.commit()

    ctx, _ = report(client, render_context)

    row, = mine(ctx['installments'])
    assert row['b0_30'] == D('600') and row['total'] == D('600')


def test_most_late_customer_comes_first(client, factory, render_context):
    _, _, recent = customer_with_flat(factory, 'XlsxGecYeni')
    _, _, ancient = customer_with_flat(factory, 'XlsxGecEski')
    _, _, tie_small = customer_with_flat(factory, 'XlsxGecEsitKucuk')
    _, _, tie_big = customer_with_flat(factory, 'XlsxGecEsitBuyuk')
    factory.installment(recent, days_ago(5), D('999999'))
    factory.installment(ancient, days_ago(200), D('10'))
    factory.installment(tie_small, days_ago(40), D('10'))
    factory.installment(tie_big, days_ago(40), D('500'))
    factory.commit()

    ctx, _ = report(client, render_context)

    names = [r['name'] for r in mine(ctx['installments'])]
    assert names == ['PYTEST XlsxGecEski Musteri',
                     'PYTEST XlsxGecEsitBuyuk Musteri',
                     'PYTEST XlsxGecEsitKucuk Musteri',
                     'PYTEST XlsxGecYeni Musteri']


def test_one_row_per_customer_even_with_two_flats(client, factory, render_context):
    project_id, _, first_flat = customer_with_flat(factory, 'XlsxGecIkiDaire')
    # Same customer owns a second flat.
    cur = factory.cur
    cur.execute("SELECT owner_id FROM flats WHERE id = %s", (first_flat,))
    owner = cur.fetchone()[0]
    second_flat = factory.flat(project_id, owner_id=owner, flat_no=2)
    factory.installment(first_flat, days_ago(10), D('100'))
    factory.installment(second_flat, days_ago(70), D('200'))
    factory.commit()

    ctx, _ = report(client, render_context)

    row, = [r for r in mine(ctx['installments']) if 'IkiDaire' in r['name']]
    assert row['flat_count'] == 2 and row['total'] == D('300')
    assert (row['b0_30'], row['b61_90']) == (D('100'), D('200'))


# --- links to /debts ----------------------------------------------------------

def test_customer_names_link_to_the_matching_debt_record(client, factory,
                                                         render_context):
    project_id, _, single = customer_with_flat(factory, 'XlsxGecTek')
    factory.installment(single, days_ago(15), D('100'))
    project_two, _, flat_a = customer_with_flat(factory, 'XlsxGecCok')
    cur = factory.cur
    cur.execute("SELECT owner_id FROM flats WHERE id = %s", (flat_a,))
    flat_b = factory.flat(project_two, owner_id=cur.fetchone()[0], flat_no=2)
    factory.installment(flat_a, days_ago(15), D('100'))
    factory.installment(flat_b, days_ago(16), D('100'))
    factory.commit()

    _, html = report(client, render_context)

    one_flat = '/debts?project_id=%d&amp;flat_id=%d#flat-%d' % (project_id, single, single)
    assert one_flat in html
    # The link really opens that flat on /debts.
    debts = client.get(one_flat.replace('&amp;', '&').split('#')[0])
    assert debts.status_code == 200
    assert 'id="flat-%d"' % single in debts.get_data(as_text=True)
    # Two late flats: a name search that finds both.
    assert '/debts?search=PYTEST+XlsxGecCok+Musteri' in html
    found = client.get('/debts?search=PYTEST+XlsxGecCok+Musteri').get_data(as_text=True)
    assert 'id="flat-%d"' % flat_a in found and 'id="flat-%d"' % flat_b in found


# --- checks table -------------------------------------------------------------

def test_late_portfolio_checks_use_the_same_buckets_and_their_own_total(
        client, factory, render_context):
    _, customer_id, flat_id = customer_with_flat(factory, 'XlsxGecCek')
    factory.installment(flat_id, days_ago(50), D('1000'))
    handed = factory.check(customer_id, D('1000'), status='portfoyde',
                           due_date=days_ago(45))
    factory.payment(flat_id, D('1000'), payment_method='çek', check_id=handed)
    factory.check(customer_id, D('7'), status='portfoyde', due_date=days_ago(3))
    factory.check(customer_id, D('5000'), status='tahsil_edildi', due_date=days_ago(40))
    factory.check(customer_id, D('6000'), status='karsiliksiz', due_date=days_ago(40))
    factory.check(customer_id, D('8000'), status='portfoyde', due_date=TODAY)
    factory.commit()

    ctx, _ = report(client, render_context)

    check_row, = mine(ctx['checks'])
    assert (check_row['b0_30'], check_row['b31_60']) == (D('7'), D('1000'))
    assert check_row['total'] == D('1007')       # cleared, bounced, not-yet-due left out
    # A portfolio check does not close the debt, so the installment is whole.
    installment_row, = mine(ctx['installments'])
    assert installment_row['total'] == D('1000')


def test_footer_totals_are_the_sum_of_the_rows(client, factory, render_context):
    for first in ('XlsxGecT1', 'XlsxGecT2', 'XlsxGecT3'):
        _, _, flat_id = customer_with_flat(factory, first)
        factory.installment(flat_id, days_ago(35), D('100'))
        factory.installment(flat_id, days_ago(95), D('11'))
    factory.commit()

    ctx, html = report(client, render_context)

    totals = ctx['installment_totals']
    assert totals == bucket_totals(ctx['installments'])
    assert totals['total'] == sum(r['total'] for r in ctx['installments'])
    assert sum(totals[k] for k in ('b0_30', 'b31_60', 'b61_90', 'b90_plus')) == totals['total']
    assert 'id="aging-total"' in html and '<tfoot' in html


# --- the page -------------------------------------------------------------------

def test_overdue_page_has_boxes_both_tables_and_the_navbar_link(client, factory,
                                                                render_context):
    _, _, flat_id = customer_with_flat(factory, 'XlsxGecSayfa')
    factory.installment(flat_id, days_ago(95), D('1234'))
    factory.commit()

    _, html = report(client, render_context)

    for label in ('0-30 gün', '31-60 gün', '61-90 gün', '90+ gün'):
        assert label in html
    assert 'id="tbl-installments"' in html and 'id="tbl-checks"' in html
    assert 'href="/overdue"' in html
    assert '1.234 ₺' in html


def test_overdue_needs_a_login_and_only_answers_get(client):
    import app as app_module
    with app_module.app.test_client() as anonymous:
        response = anonymous.get('/overdue')
    assert response.status_code == 302 and '/login' in response.headers['Location']
    assert client.post('/overdue').status_code == 405


def test_overdue_runs_two_queries_however_many_customers(client, factory,
                                                         query_counter):
    for n in range(15):
        _, customer_id, flat_id = customer_with_flat(factory, 'XlsxGecSorgu%d' % n)
        factory.installment(flat_id, days_ago(10 + n), D('10'))
        factory.check(customer_id, D('10'), due_date=days_ago(10 + n))
    factory.commit()

    query_counter.clear()
    assert client.get('/overdue').status_code == 200

    assert len(query_counter) == 2
