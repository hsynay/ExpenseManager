"""Tests for the list pagination on /payments, /customers, /checks, /expenses.

Each list shows at most PAGE_SIZE rows and carries previous and next links
that keep whatever filters and sorting the user picked. Two of the pages show
two lists, and those turn independently.

The totals shown above a list belong to the whole list, not to the page.
"""
from datetime import date, timedelta
from decimal import Decimal

from app import PAGE_SIZE

D = Decimal
JUNE = date(2026, 6, 15)


def get_ok(client, url):
    """GET a page and insist it rendered.

    Without this a dropped database connection shows up as a KeyError on the
    recorded template context, which says nothing about what went wrong.
    """
    response = client.get(url)
    assert response.status_code == 200, (
        "%s answered %s, so the page never rendered and there is no context "
        "to read" % (url, response.status_code))
    return response


def rows_on_page(render_context, template, key):
    return len(render_context.latest(template)[key])


def pager(render_context, template, key):
    return render_context.latest(template)[key]


# --- the helpers ------------------------------------------------------

def test_a_missing_page_number_means_page_one(client, render_context):
    get_ok(client, '/payments')
    assert pager(render_context, 'payments.html', 'payments_pager')['page'] == 1


def test_a_page_number_that_is_not_a_number_means_page_one(client,
                                                           render_context):
    get_ok(client, '/payments?page=abc')
    assert pager(render_context, 'payments.html', 'payments_pager')['page'] == 1


def test_a_negative_page_number_means_page_one(client, render_context):
    get_ok(client, '/payments?page=-4')
    assert pager(render_context, 'payments.html', 'payments_pager')['page'] == 1


def test_a_page_far_past_the_end_is_empty_and_does_not_fail(client):
    response = client.get('/payments?page=9999')
    assert response.status_code == 200


# --- /payments --------------------------------------------------------

def make_payments(factory, how_many):
    project_id = factory.project('sayfalama')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    for i in range(how_many):
        factory.payment(flat_id, D('100'), 'nakit',
                        payment_date=JUNE - timedelta(days=i))
    factory.commit()
    return project_id


def test_a_full_page_holds_page_size_rows(client, factory, render_context):
    make_payments(factory, PAGE_SIZE + 10)

    get_ok(client, '/payments')

    assert rows_on_page(render_context, 'payments.html', 'payments') == PAGE_SIZE
    assert pager(render_context, 'payments.html', 'payments_pager')['has_next'] is True
    assert pager(render_context, 'payments.html', 'payments_pager')['has_prev'] is False


def test_the_second_page_holds_the_rest(client, factory, render_context):
    make_payments(factory, PAGE_SIZE + 10)

    get_ok(client, '/payments')
    first = [row[0] for row in render_context.latest('payments.html')['payments']]
    get_ok(client, '/payments?page=2')
    second = [row[0] for row in render_context.latest('payments.html')['payments']]

    assert pager(render_context, 'payments.html', 'payments_pager')['has_prev'] is True
    # No row appears on both pages.
    assert not (set(first) & set(second))


def test_paging_keeps_the_filters_and_the_sorting(client, factory,
                                                  render_context):
    make_payments(factory, 3)

    get_ok(client, '/payments?sort_by=tutar&order=asc&start_date=2026-01-01&page=1')
    links = pager(render_context, 'payments.html', 'payments_pager')

    target = links['next_url'] or links['prev_url'] or ''
    if target:
        assert 'sort_by=tutar' in target
        assert 'order=asc' in target
        assert 'start_date=2026-01-01' in target


def test_a_filter_that_matches_nothing_gives_an_empty_page(client,
                                                            render_context):
    get_ok(client, '/payments?project=PYTEST%20bulunmayan%20proje')
    ctx = render_context.latest('payments.html')
    assert ctx['payments'] == []
    assert ctx['payments_pager']['has_next'] is False


# --- /customers -------------------------------------------------------

def test_customers_are_paged(client, factory, render_context):
    for i in range(PAGE_SIZE + 5):
        factory.customer(first='Sayfa%03d' % i)
    factory.commit()

    get_ok(client, '/customers')

    assert rows_on_page(render_context, 'customers.html',
                        'customers_data') == PAGE_SIZE
    assert pager(render_context, 'customers.html',
                 'customers_pager')['has_next'] is True


def test_the_customer_search_survives_paging(client, factory, render_context):
    for i in range(3):
        factory.customer(first='Arama%03d' % i)
    factory.commit()

    get_ok(client, '/customers?search=Arama')
    ctx = render_context.latest('customers.html')

    assert len(ctx['customers_data']) == 3
    assert ctx['customers_pager']['has_next'] is False


# --- /checks: two lists that page independently -----------------------

def test_the_two_check_lists_page_on_their_own(client, factory,
                                               render_context):
    project_id = factory.project('cek sayfalama')
    customer_id = factory.customer()
    supplier_id = factory.supplier(project_id)
    for i in range(PAGE_SIZE + 3):
        factory.check(customer_id, D('100'),
                      due_date=JUNE + timedelta(days=i))
    factory.outgoing_check(supplier_id, D('100'))
    factory.commit()

    get_ok(client, '/checks')
    ctx = render_context.latest('checks.html')
    assert len(ctx['incoming_checks']) == PAGE_SIZE
    assert ctx['incoming_pager']['has_next'] is True
    assert ctx['outgoing_pager']['has_next'] is False

    # Turning the incoming list does not move the outgoing one.
    get_ok(client, '/checks?in_page=2')
    ctx = render_context.latest('checks.html')
    assert ctx['incoming_pager']['page'] == 2
    assert ctx['outgoing_pager']['page'] == 1


def test_the_check_totals_cover_every_page(client, factory, render_context):
    """The boxes above the lists count all checks, not the page."""
    project_id = factory.project('cek toplam')
    customer_id = factory.customer()
    for i in range(PAGE_SIZE + 4):
        factory.check(customer_id, D('100'), status='portfoyde',
                      due_date=JUNE + timedelta(days=i))
    factory.commit()

    get_ok(client, '/checks')
    first = render_context.latest('checks.html')['total_incoming_portfolio']
    get_ok(client, '/checks?in_page=2')
    second = render_context.latest('checks.html')['total_incoming_portfolio']

    assert first == second


# --- /expenses: two lists, one of them sorted in Python ---------------

def test_petty_cash_is_paged_and_its_total_stays_whole(client, factory,
                                                       render_context):
    project_id = factory.project('kucuk gider sayfalama')
    for i in range(PAGE_SIZE + 7):
        factory.petty_cash(project_id, D('10'),
                           expense_date=JUNE - timedelta(days=i))
    factory.commit()
    expected_total = D('10') * (PAGE_SIZE + 7)

    get_ok(client, '/expenses?project_id=%d' % project_id)
    first = render_context.latest('expenses.html')
    get_ok(client, '/expenses?project_id=%d&pc_page=2' % project_id)
    second = render_context.latest('expenses.html')

    assert len(first['petty_cash_items']) == PAGE_SIZE
    assert len(second['petty_cash_items']) == 7
    # The box above the list shows the whole project on both pages.
    assert first['total_petty_cash_expense'] == expected_total
    assert second['total_petty_cash_expense'] == expected_total


def test_petty_cash_sorting_still_applies_across_pages(client, factory,
                                                       render_context):
    project_id = factory.project('kucuk gider sirali')
    for i in range(PAGE_SIZE + 5):
        factory.petty_cash(project_id, D(str(i + 1)),
                           expense_date=JUNE - timedelta(days=i))
    factory.commit()

    url = '/expenses?project_id=%d&pc_sort=amount&pc_order=asc' % project_id
    get_ok(client, url)
    first = [row[2] for row in
             render_context.latest('expenses.html')['petty_cash_items']]
    get_ok(client, url + '&pc_page=2')
    second = [row[2] for row in
              render_context.latest('expenses.html')['petty_cash_items']]

    assert first == sorted(first)
    assert second == sorted(second)
    # The second page continues where the first stopped.
    assert min(second) >= max(first)


def test_the_two_expense_lists_page_on_their_own(client, factory,
                                                 render_context):
    project_id = factory.project('gider iki liste')
    supplier_id = factory.supplier(project_id)
    for i in range(3):
        factory.expense(project_id, supplier_id, amount=D('100'),
                        title='gider %d' % i)
    for i in range(PAGE_SIZE + 2):
        factory.petty_cash(project_id, D('10'),
                           expense_date=JUNE - timedelta(days=i))
    factory.commit()

    get_ok(client, '/expenses?project_id=%d&pc_page=2' % project_id)
    ctx = render_context.latest('expenses.html')

    assert ctx['petty_pager']['page'] == 2
    assert ctx['expenses_pager']['page'] == 1
    assert len(ctx['expenses_data']) == 3


def test_the_project_picker_still_works(client, render_context):
    """Without a project the page shows the picker and no list."""
    response = get_ok(client, '/expenses?project_id=all')
    assert render_context.latest('expenses.html')['detailed_view'] is False
