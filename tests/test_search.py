"""Free text search on the list pages.

/customers had one; /debts and /payments now do too, in the same shape: a
'search' parameter, filtered in SQL with ILIKE, kept by the pager links.
"""
from datetime import date
from decimal import Decimal

D = Decimal
JUNE = date(2026, 6, 15)


def ok(client, url):
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response


def flat_ids_on_debts(render_context):
    ctx = render_context.latest('debts.html')
    return {flat['flat_id'] for project in ctx['projects_data']
            for flat in project['flats']}


def customer_with_flat(factory, first, last='Arama'):
    project_id = factory.project('arama ' + first)
    customer_id = factory.customer(first=first, last=last)
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JUNE, D('100'))
    return project_id, customer_id, flat_id


# --- /debts ----------------------------------------------------------------

def test_debts_search_finds_the_customer_by_first_name(client, factory,
                                                       render_context):
    _, _, wanted = customer_with_flat(factory, 'Zekiye')
    _, _, other = customer_with_flat(factory, 'Bahattin')
    factory.commit()

    ok(client, '/debts?search=Zekiye')

    found = flat_ids_on_debts(render_context)
    assert wanted in found and other not in found


def test_debts_search_matches_the_full_name(client, factory, render_context):
    _, _, wanted = customer_with_flat(factory, 'Nurcihan', last='Tavukcu')
    factory.commit()

    ok(client, '/debts?search=Nurcihan%20Tavukcu')

    assert wanted in flat_ids_on_debts(render_context)


def test_debts_search_ignores_letter_case(client, factory, render_context):
    _, _, wanted = customer_with_flat(factory, 'Melahat')
    factory.commit()

    ok(client, '/debts?search=MELAHAT')

    assert wanted in flat_ids_on_debts(render_context)


def test_debts_search_combines_with_the_project_filter(client, factory,
                                                       render_context):
    project_id, _, wanted = customer_with_flat(factory, 'Gulbahar')
    _, _, elsewhere = customer_with_flat(factory, 'Gulbahar')
    factory.commit()

    ok(client, '/debts?project_id=%d&search=Gulbahar' % project_id)

    found = flat_ids_on_debts(render_context)
    assert wanted in found and elsewhere not in found


def test_debts_search_box_shows_the_term(client, factory):
    factory.commit()
    body = ok(client, '/debts?search=Selvinaz').get_data(as_text=True)
    assert 'value="Selvinaz"' in body


def test_debts_edit_links_keep_the_search(client, factory):
    """After an edit the user comes back to the same filtered list."""
    _, _, flat_id = customer_with_flat(factory, 'Durdane')
    factory.commit()

    body = ok(client, '/debts?search=Durdane').get_data(as_text=True)

    assert 'search%3DDurdane' in body
    assert '%%23flat-%d' % flat_id in body


# --- /payments ---------------------------------------------------------

def test_payments_search_finds_the_customer(client, factory, render_context):
    _, _, wanted_flat = customer_with_flat(factory, 'Hikmet')
    _, _, other_flat = customer_with_flat(factory, 'Sebahattin')
    wanted = factory.payment(wanted_flat, D('10'), payment_date=JUNE)
    other = factory.payment(other_flat, D('10'), payment_date=JUNE)
    factory.commit()

    ok(client, '/payments?search=Hikmet')

    ids = {row[0] for row in render_context.latest('payments.html')['payments']}
    assert wanted in ids and other not in ids


def test_payments_search_matches_the_description(client, factory,
                                                 render_context):
    _, _, flat_id = customer_with_flat(factory, 'Aciklama')
    wanted = factory.payment(flat_id, D('10'), payment_date=JUNE,
                             description='kapora iadesi zxq')
    factory.commit()

    ok(client, '/payments?search=zxq')

    ids = {row[0] for row in render_context.latest('payments.html')['payments']}
    assert wanted in ids


def test_payments_search_works_with_the_other_filters(client, factory,
                                                      render_context):
    _, _, flat_id = customer_with_flat(factory, 'Filtreli')
    inside = factory.payment(flat_id, D('10'), payment_date=JUNE)
    outside = factory.payment(flat_id, D('10'), payment_date=date(2025, 1, 1))
    factory.commit()

    ok(client, '/payments?search=Filtreli&start_date=2026-01-01')

    ids = {row[0] for row in render_context.latest('payments.html')['payments']}
    assert inside in ids and outside not in ids


def test_payments_pager_links_keep_the_search(client, factory,
                                              render_context):
    _, _, flat_id = customer_with_flat(factory, 'Sayfali')
    for i in range(55):
        factory.payment(flat_id, D('1'), payment_date=JUNE)
    factory.commit()

    ok(client, '/payments?search=Sayfali')

    pager = render_context.latest('payments.html')['payments_pager']
    assert pager['has_next'] is True
    assert 'search=Sayfali' in pager['next_url']


# --- odd input -----------------------------------------------------------

def test_wildcard_characters_do_not_break_the_pages(client):
    """% and _ are ILIKE wildcards; they widen the match but never fail."""
    for url in ('/debts?search=%25', '/debts?search=_',
                '/payments?search=%25', "/payments?search='"):
        ok(client, url)


def test_an_empty_search_changes_nothing(client, render_context):
    ok(client, '/payments')
    everything = len(render_context.latest('payments.html')['payments'])
    ok(client, '/payments?search=')
    assert len(render_context.latest('payments.html')['payments']) == everything
