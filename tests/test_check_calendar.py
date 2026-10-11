"""/check-calendar: waiting checks in one list, by when they fall due.

"Today" is Wednesday 17 June 2026, so this week ends on Sunday 21 June and
the month on Tuesday 30 June. The calendar lists every check in the database,
so each test picks out only the checks it created (their party name starts
with PYTEST) and reads the box totals as a change from a first look.
"""
from datetime import date
from decimal import Decimal

import pytest

from check_calendar import group_checks

D = Decimal
TODAY = date(2026, 6, 17)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    import routes.forecast
    monkeypatch.setattr(routes.forecast, 'today_istanbul', lambda: TODAY)


def look(client, render_context):
    assert client.get('/check-calendar').status_code == 200
    return render_context.latest('check_calendar.html')['grouped']


def mine(rows):
    """Only the checks this test created."""
    return [c for c in rows if c['party'].startswith('PYTEST ')]


def ids(rows):
    return {(c['kind'], c['id']) for c in mine(rows)}


def sample(factory):
    """A customer and a supplier, ready to hold checks."""
    customer_id = factory.customer(first='XlsxTakvim')
    supplier_id = factory.supplier(name='XlsxTakvimci')
    return customer_id, supplier_id


# --- sections -------------------------------------------------------------------

def test_each_check_lands_in_the_right_section(client, factory, render_context):
    customer_id, supplier_id = sample(factory)
    spots = {
        ('in', 'overdue'): factory.check(customer_id, D('100'), due_date=date(2026, 6, 10)),
        ('in', 'week'): factory.check(customer_id, D('100'), due_date=date(2026, 6, 21)),
        ('in', 'today'): factory.check(customer_id, D('100'), due_date=TODAY),
        ('in', 'month'): factory.check(customer_id, D('100'), due_date=date(2026, 6, 25)),
        ('in', 'later'): factory.check(customer_id, D('100'), due_date=date(2026, 7, 1)),
        ('out', 'overdue'): factory.outgoing_check(supplier_id, D('100'), due_date=date(2026, 6, 16)),
        ('out', 'week'): factory.outgoing_check(supplier_id, D('100'), due_date=date(2026, 6, 19)),
        ('out', 'month'): factory.outgoing_check(supplier_id, D('100'), due_date=date(2026, 6, 30)),
        ('out', 'later'): factory.outgoing_check(supplier_id, D('100'), due_date=date(2026, 12, 1)),
    }
    factory.commit()

    sections = look(client, render_context)['sections']

    expect = {'overdue': set(), 'week': set(), 'month': set(), 'later': set()}
    for (kind, name), check_id in spots.items():
        expect[name if name != 'today' else 'week'].add((kind, check_id))
    for name in expect:
        assert ids(sections[name]) == expect[name], name


def test_cleared_checks_are_not_listed_and_bounced_ones_are_apart(
        client, factory, render_context):
    customer_id, supplier_id = sample(factory)
    cleared_in = factory.check(customer_id, D('100'), status='tahsil_edildi',
                               due_date=date(2026, 6, 20))
    cleared_out = factory.outgoing_check(supplier_id, D('100'), status='odendi',
                                         due_date=date(2026, 6, 20))
    bounced_in = factory.check(customer_id, D('100'), status='karsiliksiz',
                               due_date=date(2026, 6, 1))
    bounced_out = factory.outgoing_check(supplier_id, D('100'), status='karsiliksiz',
                                         due_date=date(2026, 6, 20))
    factory.commit()

    grouped = look(client, render_context)

    everywhere = set()
    for name in ('overdue', 'week', 'month', 'later'):
        everywhere |= ids(grouped['sections'][name])
    assert everywhere == set()
    assert ('in', cleared_in) not in ids(grouped['bounced'])
    assert ('out', cleared_out) not in ids(grouped['bounced'])
    assert ids(grouped['bounced']) == {('in', bounced_in), ('out', bounced_out)}


def test_box_totals_count_only_waiting_checks(client, factory, render_context):
    customer_id, supplier_id = sample(factory)
    before = look(client, render_context)['totals']

    factory.check(customer_id, D('1000'), due_date=date(2026, 6, 10))      # overdue
    factory.check(customer_id, D('300'), due_date=date(2026, 6, 18))       # week
    factory.check(customer_id, D('77'), due_date=date(2026, 6, 25))        # month
    factory.check(customer_id, D('9999'), status='karsiliksiz',
                  due_date=date(2026, 6, 10))                              # never
    factory.check(customer_id, D('8888'), status='tahsil_edildi',
                  due_date=date(2026, 6, 18))                              # done
    factory.outgoing_check(supplier_id, D('2000'), due_date=date(2026, 6, 12))
    factory.outgoing_check(supplier_id, D('450'), due_date=date(2026, 6, 20))
    factory.outgoing_check(supplier_id, D('7777'), status='odendi',
                           due_date=date(2026, 6, 12))
    factory.commit()

    after = look(client, render_context)['totals']

    assert after['overdue_in'] - before['overdue_in'] == D('1000')
    assert after['overdue_out'] - before['overdue_out'] == D('2000')
    assert after['week_in'] - before['week_in'] == D('300')
    assert after['week_out'] - before['week_out'] == D('450')


def test_list_is_in_due_date_order_with_both_kinds_mixed(client, factory,
                                                         render_context):
    customer_id, supplier_id = sample(factory)
    factory.check(customer_id, D('1'), due_date=date(2026, 8, 5))
    factory.outgoing_check(supplier_id, D('1'), due_date=date(2026, 8, 2))
    factory.check(customer_id, D('1'), due_date=date(2026, 8, 3))
    factory.outgoing_check(supplier_id, D('1'), due_date=date(2026, 8, 4))
    factory.commit()

    later = mine(look(client, render_context)['sections']['later'])

    assert [(c['due_date'].day, c['kind']) for c in later] == [
        (2, 'out'), (3, 'in'), (4, 'out'), (5, 'in')]


def test_a_week_that_runs_into_next_month_leaves_the_month_empty():
    def check(due):
        return {'kind': 'in', 'id': 1, 'due_date': due, 'amount': D('10'),
                'status': 'portfoyde', 'party': 'x'}

    # Thursday 29 October: the week ends on Sunday 1 November.
    grouped = group_checks(
        [check(date(2026, 10, 31)), check(date(2026, 11, 1)),
         check(date(2026, 11, 2))], date(2026, 10, 29))

    assert grouped['week_end'] == date(2026, 11, 1)
    assert [c['due_date'] for c in grouped['sections']['week']] == [
        date(2026, 10, 31), date(2026, 11, 1)]
    assert grouped['sections']['month'] == []
    assert [c['due_date'] for c in grouped['sections']['later']] == [date(2026, 11, 2)]


# --- project, links, page -----------------------------------------------------

def test_project_comes_from_the_payment_the_expense_or_the_supplier(
        client, factory, render_context):
    project_id = factory.project('takvim proje')
    customer_id, _ = sample(factory)
    flat_id = factory.flat(project_id, owner_id=customer_id)
    supplier_id = factory.supplier(project_id, name='XlsxTakvimProje')
    expense_id = factory.expense(project_id, supplier_id)

    with_payment = factory.check(customer_id, D('1'), due_date=date(2026, 9, 1))
    factory.payment(flat_id, D('1'), payment_method='çek', check_id=with_payment)
    orphan = factory.check(customer_id, D('1'), due_date=date(2026, 9, 2))
    paid_expense = factory.outgoing_check(supplier_id, D('1'), due_date=date(2026, 9, 3))
    factory.supplier_payment(expense_id, supplier_id, D('1'),
                             payment_method='çek', check_id=paid_expense)
    by_supplier = factory.outgoing_check(supplier_id, D('1'), due_date=date(2026, 9, 4))
    factory.commit()

    later = {(c['kind'], c['id']): c['project_name'] for c in
             mine(look(client, render_context)['sections']['later'])}

    assert later[('in', with_payment)] == 'PYTEST takvim proje'
    assert later[('in', orphan)] is None
    assert later[('out', paid_expense)] == 'PYTEST takvim proje'
    assert later[('out', by_supplier)] == 'PYTEST takvim proje'


def test_every_row_links_to_the_check_on_the_checks_page(client, factory,
                                                         render_context):
    customer_id, supplier_id = sample(factory)
    in_id = factory.check(customer_id, D('123'), due_date=date(2026, 7, 14))
    out_id = factory.outgoing_check(supplier_id, D('456'), due_date=date(2026, 7, 15))
    factory.commit()

    html = client.get('/check-calendar').get_data(as_text=True)

    for anchor in ('#chk-in-%d' % in_id, '#chk-out-%d' % out_id):
        link = next(h for h in _hrefs(html) if h.endswith(anchor))
        page = client.get(link.replace('&amp;', '&'))
        assert page.status_code == 200
        # The target page really contains that row.
        assert 'id="%s"' % anchor[1:] in page.get_data(as_text=True)


def _hrefs(html):
    import re
    return re.findall(r'href="([^"]+)"', html)


def test_page_shows_the_four_boxes_and_the_closed_bounced_section(client, factory):
    customer_id, _ = sample(factory)
    factory.check(customer_id, D('1'), status='karsiliksiz', due_date=date(2026, 6, 1))
    factory.commit()

    html = client.get('/check-calendar').get_data(as_text=True)

    for box in ('box-overdue-in', 'box-overdue-out', 'box-week-in', 'box-week-out'):
        assert 'id="%s"' % box in html
    assert 'id="bounced-section"' in html
    assert 'class="collapse" id="bounced-section"' in html      # closed by default
    assert 'hiçbir toplamda sayılmaz' in html
    assert 'href="/check-calendar"' in html                     # navbar entry


def test_calendar_needs_a_login_and_only_answers_get(client):
    import app as app_module
    with app_module.app.test_client() as anonymous:
        response = anonymous.get('/check-calendar')
    assert response.status_code == 302 and '/login' in response.headers['Location']
    assert client.post('/check-calendar').status_code == 405


def test_calendar_runs_a_single_query(client, factory, query_counter):
    customer_id, supplier_id = sample(factory)
    for day in range(1, 20):
        factory.check(customer_id, D('1'), due_date=date(2026, 7, day))
        factory.outgoing_check(supplier_id, D('1'), due_date=date(2026, 7, day))
    factory.commit()

    query_counter.clear()
    assert client.get('/check-calendar').status_code == 200

    assert len(query_counter) == 1
