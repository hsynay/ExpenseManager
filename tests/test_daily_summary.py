"""/tasks/daily-summary: the token lock, the mail settings and the mail itself.

No real request is ever made: requests.post is replaced by a recorder. "Today"
is Wednesday 17 June 2026.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
import requests

import app as app_module
import daily_summary
import db
from daily_summary import (
    gather_summary, read_mail_settings, mask_secret, BREVO_URL,
    CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS,
)

D = Decimal
TODAY = date(2026, 6, 17)
TOKEN = 'test-token-0123456789'
API_KEY = 'xkeysib-0123456789abcdef-SECRETPART'


class FakeBrevo:
    """Stands in for requests.post and records every call."""

    def __init__(self):
        self.calls = []
        self.status = 201
        self.body = '{"messageId": "<1@brevo>"}'
        self.raises = None
        self.on_call = None

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers,
                           'timeout': timeout})
        if self.on_call:
            self.on_call()
        if self.raises:
            raise self.raises
        return FakeResponse(self.status, self.body)


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


@pytest.fixture(autouse=True)
def brevo(monkeypatch):
    import core
    import routes.tasks
    core.limiter.reset()
    fake = FakeBrevo()
    monkeypatch.setattr(daily_summary.requests, 'post', fake.post)
    monkeypatch.setattr(routes.tasks, 'today_istanbul', lambda: TODAY)
    monkeypatch.setenv('TASK_TOKEN', TOKEN)
    configure_mail(monkeypatch)
    return fake


def configure_mail(monkeypatch, **overrides):
    values = {'BREVO_API_KEY': API_KEY,
              'MAIL_FROM': 'Konak İnşaat <bildirim@example.test>',
              'MAIL_TO': 'boss@example.test, accountant@example.test'}
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


@pytest.fixture
def anonymous():
    """A client with no login: the cron service has no session."""
    with app_module.app.test_client() as c:
        yield c


def call(client, token=TOKEN):
    return client.get('/tasks/daily-summary', headers={'X-Task-Token': token})


def payloads(brevo):
    return [c['json'] for c in brevo.calls]


# --- the token lock -------------------------------------------------------------

def test_wrong_or_missing_token_is_refused_and_nothing_is_sent(anonymous, brevo):
    assert anonymous.get('/tasks/daily-summary').status_code == 403
    assert call(anonymous, 'wrong').status_code == 403
    assert anonymous.get('/tasks/daily-summary?token=wrong').status_code == 403
    assert call(anonymous, TOKEN[:-1]).status_code == 403

    assert brevo.calls == []


def test_without_a_token_on_the_server_every_request_is_refused(anonymous, brevo,
                                                                 monkeypatch):
    monkeypatch.delenv('TASK_TOKEN')

    assert anonymous.get('/tasks/daily-summary?token=').status_code == 403
    assert call(anonymous, '').status_code == 403
    assert brevo.calls == []


def test_the_token_works_in_the_header_and_in_the_address_without_a_login(
        anonymous, brevo):
    header = call(anonymous)
    query = anonymous.get('/tasks/daily-summary?token=' + TOKEN)

    assert header.status_code == 200 and header.get_json()['sent'] is True
    assert query.status_code == 200 and query.get_json()['sent'] is True
    assert len(brevo.calls) == 2


def test_guessing_the_token_is_rate_limited(anonymous):
    codes = [call(anonymous, 'guess%d' % n).status_code for n in range(25)]

    assert codes[:20] == [403] * 20
    assert 429 in codes[20:]


# --- mail settings ----------------------------------------------------------------

@pytest.mark.parametrize('missing', ['BREVO_API_KEY', 'MAIL_FROM', 'MAIL_TO'])
def test_a_missing_setting_skips_the_mail_with_a_200_and_a_log_line(
        anonymous, brevo, monkeypatch, caplog, missing):
    configure_mail(monkeypatch, **{missing: None})

    with caplog.at_level(logging.WARNING):
        response = call(anonymous)

    assert response.status_code == 200
    assert response.get_json() == {'sent': False, 'reason': 'mail_not_configured'}
    assert brevo.calls == []
    assert missing in caplog.text
    assert API_KEY not in caplog.text


def test_all_missing_settings_are_named_in_the_log(anonymous, brevo, monkeypatch,
                                                   caplog):
    configure_mail(monkeypatch, BREVO_API_KEY=None, MAIL_FROM=None, MAIL_TO=None)

    with caplog.at_level(logging.WARNING):
        assert call(anonymous).status_code == 200

    for name in ('BREVO_API_KEY', 'MAIL_FROM', 'MAIL_TO'):
        assert name in caplog.text
    assert brevo.calls == []


def test_a_sender_without_an_address_skips_the_mail(anonymous, brevo, monkeypatch,
                                                    caplog):
    configure_mail(monkeypatch, MAIL_FROM='Konak Insaat')

    with caplog.at_level(logging.WARNING):
        response = call(anonymous)

    assert response.status_code == 200 and response.get_json()['sent'] is False
    assert 'MAIL_FROM' in caplog.text
    assert brevo.calls == []


def test_recipients_may_be_separated_by_commas_or_semicolons(monkeypatch):
    configure_mail(monkeypatch, MAIL_TO=' a@x.test;b@x.test , c@x.test ')

    settings, missing = read_mail_settings()

    assert missing == []
    assert settings['recipients'] == ['a@x.test', 'b@x.test', 'c@x.test']


def test_the_sender_may_be_a_name_with_an_address_or_just_an_address(monkeypatch):
    settings, _ = read_mail_settings()
    assert (settings['sender_name'], settings['sender_email']) == (
        'Konak İnşaat', 'bildirim@example.test')

    configure_mail(monkeypatch, MAIL_FROM='only@example.test')
    settings, _ = read_mail_settings()
    assert (settings['sender_name'], settings['sender_email']) == (
        '', 'only@example.test')


def test_the_old_smtp_settings_are_gone():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert 'smtplib' not in (root / 'daily_summary.py').read_text(encoding='utf-8')
    assert not hasattr(daily_summary, 'smtplib')
    example = (root / '.env.example').read_text(encoding='utf-8')
    assert 'SMTP_' not in example and 'BREVO_API_KEY' in example


# --- sending ------------------------------------------------------------------------

def test_the_mail_is_posted_to_brevo_with_the_key_in_the_header(anonymous, brevo):
    response = call(anonymous)

    assert response.get_json() == {'sent': True, 'recipients': 2}
    call_made, = brevo.calls
    assert call_made['url'] == BREVO_URL == 'https://api.brevo.com/v3/smtp/email'
    assert call_made['headers']['api-key'] == API_KEY
    body = call_made['json']
    assert body['sender'] == {'email': 'bildirim@example.test', 'name': 'Konak İnşaat'}
    assert body['to'] == [{'email': 'boss@example.test'},
                          {'email': 'accountant@example.test'}]
    assert body['subject'] == 'Günlük Özet – 17.06.2026'
    # The same two templates as before: a text body and an HTML body.
    assert 'GÜNLÜK ÖZET' in body['textContent']
    assert '<h2' in body['htmlContent']
    # The key is never in the body, and never in the answer to the caller.
    assert API_KEY not in str(body)
    assert API_KEY not in response.get_data(as_text=True)


def test_a_sender_without_a_name_sends_only_the_address(anonymous, brevo,
                                                        monkeypatch):
    configure_mail(monkeypatch, MAIL_FROM='only@example.test')

    call(anonymous)

    assert brevo.calls[0]['json']['sender'] == {'email': 'only@example.test'}


def test_the_request_has_a_timeout_that_cannot_freeze_the_worker(anonymous, brevo):
    call(anonymous)

    connect, read = brevo.calls[0]['timeout']
    assert (connect, read) == (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
    assert 0 < connect <= 10 and 0 < read <= 10
    # Gunicorn kills a worker after 30 seconds.
    assert connect + read < 30


def test_the_database_connection_is_given_back_before_the_http_call(anonymous,
                                                                     brevo):
    in_use_before = db.pool_stats()[0]
    seen = []
    brevo.on_call = lambda: seen.append(db.pool_stats()[0])

    assert call(anonymous).status_code == 200

    assert seen == [in_use_before]


@pytest.mark.parametrize('status', [400, 401, 402, 403, 429, 500, 503])
def test_an_api_error_gives_502_and_leaks_nothing(anonymous, brevo, caplog, status):
    # Even a provider that repeats our key back must not get it into the log.
    brevo.status = status
    brevo.body = '{"message": "Key not found: %s", "code": "unauthorized"}' % API_KEY

    with caplog.at_level(logging.ERROR):
        response = call(anonymous)

    assert response.status_code == 502
    assert response.get_json() == {'sent': False, 'reason': 'mail_error'}
    answer = response.get_data(as_text=True)
    assert 'unauthorized' not in answer and API_KEY not in answer
    assert 'Traceback' not in answer
    assert 'could not be sent' in caplog.text and str(status) in caplog.text
    assert API_KEY not in caplog.text and 'SECRETPART' not in caplog.text
    assert len(brevo.calls) == 1            # no retry


@pytest.mark.parametrize('error', [
    requests.exceptions.ConnectTimeout('connect timed out'),
    requests.exceptions.ReadTimeout('read timed out'),
    requests.exceptions.ConnectionError('network is unreachable'),
    requests.exceptions.SSLError('certificate problem'),
])
def test_a_network_failure_gives_502_and_one_attempt(anonymous, brevo, caplog,
                                                     error):
    brevo.raises = error

    with caplog.at_level(logging.ERROR):
        response = call(anonymous)

    assert response.status_code == 502
    assert response.get_json() == {'sent': False, 'reason': 'mail_error'}
    assert type(error).__name__ in caplog.text
    assert len(brevo.calls) == 1


def test_an_error_text_that_contains_the_key_is_masked_in_the_log(anonymous, brevo,
                                                                   caplog):
    brevo.raises = requests.exceptions.ConnectionError(
        'proxy rejected header api-key: %s' % API_KEY)

    with caplog.at_level(logging.DEBUG):
        response = call(anonymous)

    assert response.status_code == 502
    assert API_KEY not in caplog.text and 'SECRETPART' not in caplog.text
    assert API_KEY not in response.get_data(as_text=True)


def test_mask_secret():
    assert mask_secret('bad key %s here' % API_KEY, API_KEY) == 'bad key *** here'
    # Anything shaped like a Brevo key is hidden, even if it is another key.
    assert 'abc123' not in mask_secret('x xkeysib-abc123-def y', 'something-else')
    assert mask_secret('nothing secret', '') == 'nothing secret'


def test_the_timeouts_are_set_for_the_database_too():
    assert db._connect_params()['connect_timeout'] == db.CONNECT_TIMEOUT_SECONDS
    assert 0 < db.CONNECT_TIMEOUT_SECONDS <= 10


# --- what the mail says -----------------------------------------------------------------

def test_mail_lists_waiting_checks_due_today_and_tomorrow_only(anonymous, brevo,
                                                               factory):
    customer_id = factory.customer(first='XlsxOzet')
    supplier_id = factory.supplier(name='XlsxOzetci')
    factory.check(customer_id, D('1111'), due_date=TODAY)
    factory.outgoing_check(supplier_id, D('2222'), due_date=TODAY + timedelta(days=1))
    factory.check(customer_id, D('3333'), status='tahsil_edildi', due_date=TODAY)
    factory.check(customer_id, D('4444'), status='karsiliksiz', due_date=TODAY)
    factory.outgoing_check(supplier_id, D('5555'), status='odendi', due_date=TODAY)
    factory.check(customer_id, D('6666'), due_date=TODAY + timedelta(days=2))
    factory.commit()

    call(anonymous)

    html = payloads(brevo)[0]['htmlContent']
    text = payloads(brevo)[0]['textContent']
    assert '1.111 ₺' in html and '2.222 ₺' in html
    for hidden in ('3.333', '4.444', '5.555', '6.666'):
        assert hidden not in html and hidden not in text
    assert 'PYTEST XlsxOzet Musteri' in html and 'PYTEST XlsxOzetci' in html


def test_names_in_the_mail_are_escaped(anonymous, brevo, factory):
    customer_id = factory.customer(first='<b>kalin</b>')
    factory.check(customer_id, D('9876'), due_date=TODAY)
    factory.commit()

    call(anonymous)

    html = payloads(brevo)[0]['htmlContent']
    assert '&lt;b&gt;kalin&lt;/b&gt;' in html and '<b>kalin</b>' not in html


def test_summary_numbers_late_installments_and_four_weeks_of_cash(factory):
    cur = factory.cur
    before = gather_summary(cur, TODAY)

    project_id = factory.project('ozet')
    customer_id = factory.customer(first='XlsxOzetSayi')
    flat_id = factory.flat(project_id, owner_id=customer_id)
    factory.installment(flat_id, TODAY - timedelta(days=10), D('5000'))      # late
    factory.installment(flat_id, TODAY + timedelta(days=1), D('700'))        # week 1
    factory.installment(flat_id, TODAY + timedelta(days=22), D('300'))       # week 4
    factory.installment(flat_id, TODAY + timedelta(days=40), D('99999'))     # too far
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id)
    factory.expense_installment(expense_id, TODAY + timedelta(days=2), D('250'))
    after = gather_summary(cur, TODAY)

    assert after['late_total'] - before['late_total'] == D('5000')
    assert after['late_customers'] - before['late_customers'] == 1
    cash_in = after['cash']['total_in'] - before['cash']['total_in']
    cash_out = after['cash']['total_out'] - before['cash']['total_out']
    # The late 5000 is in the first row, so it is part of the four week total.
    assert cash_in == D('6000') and cash_out == D('250')
    assert len(after['cash']['weeks']) in (4, 5)         # 4 weeks (+ a late row)
    assert [w for w in after['cash']['weeks'] if not w['late']][0]['start'] == date(2026, 6, 15)
    assert len([w for w in after['cash']['weeks'] if not w['late']]) == 4


def test_the_job_only_reads_and_runs_three_queries(anonymous, query_counter):
    query_counter.clear()

    assert call(anonymous).status_code == 200

    statements = [' '.join(sql.split()).upper() for sql in query_counter]
    assert len(statements) == 3
    for statement in statements:
        assert statement.startswith(('SELECT', 'WITH'))
        for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER '):
            assert word not in statement


# --- how the mail looks ----------------------------------------------------------------
# Mail clients (Gmail, Outlook, Hotmail) ignore modern CSS, so the layout rules
# are checked on the finished HTML: tables, inline styles, 600 pixels.

def sample_summary(negative=True):
    weeks = [
        {'label': '15.06 - 21.06', 'incoming': D('700'), 'outgoing': D('250000'),
         'cumulative': D('-249300'), 'negative': True},
        {'label': '22.06 - 28.06', 'incoming': D('1250000'), 'outgoing': D('0'),
         'cumulative': D('1000700'), 'negative': False},
    ]
    if not negative:
        weeks = weeks[1:]
    return {
        'today': TODAY,
        'due_today': {'day': TODAY, 'checks': [
            {'kind': 'in', 'party': 'Ali', 'project_name': 'Proje', 'amount': D('1234567')}],
            'incoming': D('1234567'), 'outgoing': D('0')},
        'due_tomorrow': {'day': TODAY + timedelta(days=1), 'checks': [],
                         'incoming': D('0'), 'outgoing': D('0')},
        'late_total': D('5000'), 'late_customers': 2, 'late_oldest_days': 41,
        'cash': {'weeks': weeks, 'total_net': D('1000'),
                 'first_negative': weeks[0] if negative else None,
                 'negative_count': 1 if negative else 0},
        'weeks': 4,
    }


def render(summary):
    with app_module.app.test_request_context('/'):
        return daily_summary.render_mail(summary, 'https://site.test/')


def test_layout_is_table_based_inline_and_600_wide():
    html = render(sample_summary())['html']

    assert 'width="600"' in html and 'max-width:600px' in html
    assert html.count('<table') == html.count('</table>') >= 5
    # No style sheet and no layout built from div boxes: only inline styles.
    assert '<style' not in html and 'class=' not in html
    assert '<div' not in html.replace('<div style="display:none', '')
    assert '<link' not in html and '<script' not in html


def test_each_section_is_its_own_card():
    html = render(sample_summary())['html']

    for title in ('Bugün vadesi gelen çekler', 'Yarın vadesi gelen çekler',
                  'Vadesi geçmiş taksitler', 'nakit durumu'):
        assert title in html
    assert html.count('<h3') == 4        # one card per section
    assert '<h2' in html                 # the header block


def test_numbers_are_right_aligned_with_thousands_separator():
    html = render(sample_summary())['html']

    assert '1.234.567 ₺' in html and '1.250.000 ₺' in html
    assert 'align="right"' in html and 'text-align:right' in html


def test_only_a_negative_cumulative_is_red():
    html = render(sample_summary(negative=True))['html']
    assert 'color:#dc3545;">-249.300 ₺' in html
    assert 'color:#212529;">1.000.700 ₺' in html

    # With no negative week, the weekly table has no red number. The late
    # total is red on purpose, so look at the cash table only.
    cash = render(sample_summary(negative=False))['html'].split('nakit durumu')[1]
    assert '#dc3545' not in cash.split('Net:')[0]


def test_links_are_buttons_inside_a_coloured_box():
    mail = render(sample_summary())

    html = mail['html']
    assert html.count('<a ') == 2
    for url in ('https://site.test/overdue', 'https://site.test/cashflow'):
        assert 'href="%s"' % url in html
    # The box colour sits on the table cell (Outlook) and the link is padded.
    assert html.count('bgcolor="#667eea"') >= 3      # header + two buttons
    assert 'display:inline-block;padding:10px 20px' in html
    assert 'https://site.test/overdue' in mail['text']
    assert 'https://site.test/cashflow' in mail['text']


def test_plain_text_version_has_all_sections_and_marks_negative_weeks():
    text = render(sample_summary())['text']

    for line in ('GÜNLÜK ÖZET - 17.06.2026', 'BUGÜN VADESİ GELEN ÇEKLER',
                 'YARIN VADESİ GELEN ÇEKLER', 'VADESİ GEÇMİŞ TAKSİTLER',
                 'NAKİT DURUMU', 'Net: 1.000 TL'):
        assert line in text
    assert '-249.300 TL  (EKSİ)' in text
    assert '<table' not in text and '<td' not in text
