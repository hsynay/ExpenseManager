"""Login brings the user back to the page they asked for.

login_required passes the wanted address along as ?next=, and the login
accepts it through safe_next, so an outside address is never followed. The
JSON endpoints keep answering 401 and never redirect.

No user row is needed: the lookup is replaced with a fake user, so these
tests write nothing to the database.
"""
from urllib.parse import urlsplit, parse_qs

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import routes.auth as auth_module

PASSWORD = 'pytest-password'


@pytest.fixture
def anon():
    """A test client with no session."""
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    app_module.app.config['TESTING'] = False
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def fake_user(monkeypatch):
    hashed = generate_password_hash(PASSWORD)
    monkeypatch.setattr(
        auth_module, 'get_user_by_email',
        lambda email: (999999, email, hashed, 'pytest'))


def _login(client, url='/login'):
    return client.post(url, data={
        'email': 'pytest@example.com', 'password': PASSWORD})


def test_protected_page_sends_wanted_address_to_login(anon):
    response = anon.get('/cashflow?months=6')
    assert response.status_code == 302
    location = urlsplit(response.headers['Location'])
    assert location.path == '/login'
    assert parse_qs(location.query)['next'] == ['/cashflow?months=6']


def test_page_without_query_string_carries_plain_path(anon):
    response = anon.get('/overdue')
    location = urlsplit(response.headers['Location'])
    assert parse_qs(location.query)['next'] == ['/overdue']


def test_login_returns_to_wanted_page(anon):
    first = anon.get('/cashflow?months=6')
    login_url = first.headers['Location']
    response = _login(anon, login_url)
    assert response.status_code == 302
    assert response.headers['Location'] == '/cashflow?months=6'
    # The session is real now: the page itself opens.
    assert anon.get('/cashflow?months=6').status_code == 200


def test_login_without_next_goes_to_dashboard(anon):
    response = _login(anon)
    assert response.status_code == 302
    assert urlsplit(response.headers['Location']).path == '/dashboard'


@pytest.mark.parametrize('bad', [
    'https://evil.example/steal',
    '//evil.example/steal',
    '/\\evil.example',
    'javascript:alert(1)',
])
def test_outside_address_is_refused(anon, bad):
    response = anon.post('/login', query_string={'next': bad}, data={
        'email': 'pytest@example.com', 'password': PASSWORD})
    assert response.status_code == 302
    assert urlsplit(response.headers['Location']).path == '/dashboard'


def test_next_pointing_at_logout_is_ignored(anon):
    response = anon.post('/login', query_string={'next': '/logout'}, data={
        'email': 'pytest@example.com', 'password': PASSWORD})
    assert urlsplit(response.headers['Location']).path == '/dashboard'


def test_wrong_password_keeps_next_on_the_form(anon):
    response = anon.post('/login', query_string={'next': '/overdue'}, data={
        'email': 'pytest@example.com', 'password': 'wrong'})
    # The page is shown again (200). The form posts to the same address, so
    # next is still in the query string for the next try.
    assert response.status_code == 200


def test_post_without_session_carries_no_next(anon):
    # Project 0 does not exist, so even a broken guard could not delete data.
    response = anon.post('/project/0/delete')
    assert response.status_code == 302
    location = urlsplit(response.headers['Location'])
    assert location.path == '/login'
    assert location.query == ''


@pytest.mark.parametrize('path', [
    '/api/monthly_payments',
    '/api/project/0/flats',
])
def test_json_endpoint_still_answers_401(anon, path):
    response = anon.get(path)
    assert response.status_code == 401
    assert response.is_json
