"""Returning to the same place in a list after an edit.

Edit links carry a 'next' address that ends in an anchor such as #flat-12.
After a save the route sends the user back to that address, and the list
page has an element with that id to land on.

safe_next also stops 'next' from sending the user to another site.
"""
from datetime import date
from decimal import Decimal

import app as app_module
from app import safe_next

D = Decimal
JAN = date(2026, 1, 20)


def location(response):
    return response.headers.get('Location', '')


# --- safe_next -----------------------------------------------------------

def check(value):
    with app_module.app.test_request_context('/', base_url='http://localhost'):
        return safe_next(value)


def test_a_local_path_with_an_anchor_is_kept():
    assert check('/debts?project_id=3#flat-12') == '/debts?project_id=3#flat-12'


def test_another_site_is_refused():
    assert check('https://example.com/steal') is None


def test_a_protocol_relative_address_is_refused():
    assert check('//example.com/steal') is None


def test_a_backslash_trick_is_refused():
    assert check('/\\example.com') is None


def test_a_script_address_is_refused():
    assert check('javascript:alert(1)') is None


def test_an_empty_value_is_none():
    assert check('') is None and check(None) is None


def test_a_full_address_on_this_host_becomes_a_path():
    """The referrer header arrives as a full address."""
    assert check('http://localhost/checks?in_page=2') == '/checks?in_page=2'


def test_a_full_address_on_another_host_is_refused():
    assert check('http://example.com/checks') is None


# --- the redirect after a save keeps the anchor --------------------------

def test_a_saved_payment_plan_returns_to_the_flat(client, factory):
    project_id = factory.project('donus plan')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.commit()
    target = '/debts?project_id=%d#flat-%d' % (project_id, flat_id)

    response = client.post('/flat/%d/manage_plan' % flat_id, data={
        'plan_json': '[{"due_date": "2026-01-10", "amount": "100"}]',
        'next': target})

    assert response.status_code == 302
    assert location(response).endswith('#flat-%d' % flat_id)


def test_a_next_pointing_elsewhere_falls_back_to_the_default(client, factory):
    project_id = factory.project('donus dis site')
    flat_id = factory.flat(project_id, total_price=D('0'))
    factory.commit()

    response = client.post('/flat/%d/manage_plan' % flat_id, data={
        'plan_json': '[{"due_date": "2026-01-10", "amount": "100"}]',
        'next': 'https://example.com/steal'})

    assert response.status_code == 302
    assert 'example.com' not in location(response)
    assert location(response).endswith('/debts')


def expense_setup(factory, status='odendi'):
    project_id = factory.project('donus gider')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('1000'))
    factory.expense_installment(expense_id, JAN, D('1000'))
    payment_id = factory.supplier_payment(expense_id, supplier_id, D('1000'),
                                          'nakit')
    factory.commit()
    return project_id, expense_id, payment_id


def test_an_edited_supplier_payment_returns_to_the_expense(client, factory):
    project_id, expense_id, payment_id = expense_setup(factory)
    target = '/expenses?project_id=%d#exp-%d' % (project_id, expense_id)

    response = client.post('/supplier_payment/%d/edit' % payment_id, data={
        'amount': '1000', 'payment_date': '2026-06-15',
        'description': 'PYTEST', 'next': target})

    assert response.status_code == 302
    assert location(response).endswith('#exp-%d' % expense_id)


def test_a_deleted_supplier_payment_returns_to_the_expense(client, factory):
    project_id, expense_id, payment_id = expense_setup(factory)
    target = '/expenses?project_id=%d#exp-%d' % (project_id, expense_id)

    response = client.post('/supplier_payment/%d/delete' % payment_id, data={
        'project_id': str(project_id), 'next': target})

    assert response.status_code == 302
    assert location(response).endswith('#exp-%d' % expense_id)


def test_an_edited_petty_cash_item_returns_to_its_row(client, factory):
    project_id = factory.project('donus kucuk gider')
    item_id = factory.petty_cash(project_id, D('50'))
    factory.commit()
    target = '/expenses?project_id=%d#pc-%d' % (project_id, item_id)

    response = client.post('/petty_cash/%d/edit' % item_id, data={
        'title': 'PYTEST', 'amount': '50', 'expense_date': '2026-06-15',
        'description': '', 'project_id': str(project_id), 'next': target})

    assert response.status_code == 302
    assert location(response).endswith('#pc-%d' % item_id)


def test_a_check_status_change_returns_to_the_check_row(client, factory):
    project_id = factory.project('donus cek')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    check_id = factory.check(customer_id, D('100'), status='portfoyde')
    factory.payment(flat_id, D('100'), 'çek', check_id=check_id)
    factory.commit()

    response = client.post('/check/update_status', data={
        'check_id': str(check_id), 'check_type': 'incoming',
        'new_status': 'tahsil_edildi',
        'next': '/checks?in_page=1#chk-in-%d' % check_id})

    assert response.status_code == 302
    assert location(response).endswith('#chk-in-%d' % check_id)


# --- the list pages have something to land on ----------------------------

def test_the_expense_page_has_anchors_for_cards_and_petty_rows(client,
                                                               factory):
    project_id = factory.project('donus anchor')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D('1000'))
    item_id = factory.petty_cash(project_id, D('10'))
    factory.commit()

    body = client.get('/expenses?project_id=%d' % project_id).get_data(
        as_text=True)

    assert 'id="exp-%d"' % expense_id in body
    assert 'id="pc-%d"' % item_id in body
    # the petty cash edit link carries the way back
    assert '%23pc-' + str(item_id) in body


def test_the_check_page_has_anchors_and_sends_next(client, factory):
    project_id = factory.project('donus cek anchor')
    customer_id = factory.customer()
    check_id = factory.check(customer_id, D('100'), status='portfoyde')
    factory.commit()

    body = client.get('/checks').get_data(as_text=True)

    assert 'id="chk-in-%d"' % check_id in body
    assert '#chk-in-%d"' % check_id in body


def test_the_debts_page_links_carry_the_flat_anchor(client, factory):
    project_id = factory.project('donus borc')
    customer_id = factory.customer()
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, JAN, D('100'))
    factory.commit()

    body = client.get('/debts?project_id=%d' % project_id).get_data(
        as_text=True)

    assert 'id="flat-%d"' % flat_id in body
    assert '%23flat-' + str(flat_id) in body
