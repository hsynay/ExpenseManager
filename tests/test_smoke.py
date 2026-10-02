"""Smoke tests: every page still answers.

These do not check numbers. They walk the routes and fail if any of them
returns a server error, which catches the kind of breakage that a rename or
a missing variable causes.

They read the sample projects that already live in the test database and
create nothing of their own.

One note: opening /expenses or a project overview runs the out of range date
repair, which can write. It only touches rows with a year above 3000 and
there are none, so these tests stay effectively read only. That repair is
pending item 4 in CLAUDE.md.
"""
import pytest

import app as app_module

# Routes that deliberately do something other than render a page.
SKIP_ENDPOINTS = {'static', 'logout', 'login'}


def sample_ids(conn):
    """Real ids from the test database, or None when a table is empty."""
    cur = conn.cursor()
    out = {}
    queries = {
        'project_id': "SELECT id FROM projects ORDER BY id LIMIT 1",
        'flat_id': "SELECT id FROM flats WHERE owner_id IS NOT NULL "
                   "ORDER BY id LIMIT 1",
        'customer_id': "SELECT id FROM customers ORDER BY id LIMIT 1",
        'expense_id': "SELECT id FROM expenses ORDER BY id LIMIT 1",
        'installment_id': "SELECT id FROM expense_schedule ORDER BY id LIMIT 1",
        'payment_id': "SELECT id FROM payments ORDER BY id LIMIT 1",
        'item_id': "SELECT id FROM petty_cash_expenses ORDER BY id LIMIT 1",
        'supplier_id': "SELECT id FROM suppliers ORDER BY id LIMIT 1",
        'check_id': "SELECT id FROM checks ORDER BY id LIMIT 1",
        'id': "SELECT id FROM projects ORDER BY id LIMIT 1",
    }
    for key, sql in queries.items():
        cur.execute(sql)
        row = cur.fetchone()
        out[key] = row[0] if row else None
    cur.close()
    return out


@pytest.fixture(scope='module')
def ids():
    from app import get_connection
    conn = get_connection()
    try:
        return sample_ids(conn)
    finally:
        conn.close()


def buildable_get_routes(ids):
    """(endpoint, url) for every GET route whose arguments we can fill."""
    adapter = app_module.app.url_map.bind('localhost')
    for rule in app_module.app.url_map.iter_rules():
        if 'GET' not in rule.methods or rule.endpoint in SKIP_ENDPOINTS:
            continue
        args = {}
        missing = False
        for name in rule.arguments:
            value = ids.get(name)
            if value is None:
                missing = True
                break
            args[name] = value
        if missing:
            continue
        yield rule.endpoint, adapter.build(rule.endpoint, args)


def test_no_get_route_returns_a_server_error(client, ids):
    failures = []
    for endpoint, url in buildable_get_routes(ids):
        status = client.get(url).status_code
        if status >= 500:
            failures.append((endpoint, url, status))
    assert failures == []


@pytest.mark.parametrize('url', [
    '/dashboard',
    '/customers',
    '/payments',
    '/checks',
    '/debts',
    '/reports',
    '/expenses',
    '/audit-logs',
])
def test_main_pages_load(client, url):
    assert client.get(url).status_code == 200


def test_project_pages_load(client, ids):
    project_id = ids['project_id']
    for path in ('/project/%d/overview', '/project/%d/transactions',
                 '/expenses?project_id=%d'):
        assert client.get(path % project_id).status_code == 200


def test_a_broken_project_id_does_not_crash_the_expense_page(client):
    """Regression: this used to raise UnboundLocalError and return 500."""
    response = client.get('/expenses?project_id=abc')
    assert response.status_code == 200


def test_an_unknown_project_id_does_not_crash_the_expense_page(client):
    assert client.get('/expenses?project_id=999999').status_code == 200


def test_the_monthly_chart_api_answers(client):
    response = client.get('/api/monthly_payments')
    assert response.status_code == 200
    body = response.get_json()
    assert len(body['labels']) == 12
    assert len(body['data']) == 12


def test_pages_require_a_login():
    """Without a session every page sends the user to the login form."""
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    with app_module.app.test_client() as anonymous:
        for url in ('/dashboard', '/customers', '/payments', '/reports'):
            response = anonymous.get(url)
            assert response.status_code == 302
            assert '/login' in response.headers['Location']
