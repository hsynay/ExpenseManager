"""/tasks/daily-summary: the token lock, the mail settings and the mail itself.

No real mail is ever sent: smtplib is replaced by a recorder. "Today" is
Wednesday 17 June 2026.
"""
import logging
import smtplib
from datetime import date, timedelta
from decimal import Decimal

import pytest

import app as app_module
import daily_summary
from daily_summary import gather_summary, read_mail_settings

D = Decimal
TODAY = date(2026, 6, 17)
TOKEN = 'test-token-0123456789'
PASSWORD = 'smtp-secret-password'


class FakeSMTP:
    """Records what the job does with the mail server."""
    instances = []
    fail_on_login = False
    uses_ssl = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.started_tls = False
        self.logged_in_as = None
        self.sent = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        if type(self).fail_on_login:
            raise smtplib.SMTPAuthenticationError(535, b'rejected')
        self.logged_in_as = (user, password)

    def send_message(self, message):
        self.sent.append(message)


class FakeSMTPSSL(FakeSMTP):
    uses_ssl = True


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    import core
    import routes.tasks
    core.limiter.reset()
    FakeSMTP.instances = []
    FakeSMTP.fail_on_login = False
    monkeypatch.setattr(daily_summary.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(daily_summary.smtplib, 'SMTP_SSL', FakeSMTPSSL)
    monkeypatch.setattr(routes.tasks, 'today_istanbul', lambda: TODAY)
    monkeypatch.setenv('TASK_TOKEN', TOKEN)
    configure_mail(monkeypatch)


def configure_mail(monkeypatch, **overrides):
    values = {'SMTP_HOST': 'smtp.example.test', 'SMTP_PORT': '587',
              'SMTP_USER': 'sender@example.test', 'SMTP_PASSWORD': PASSWORD,
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


def mails():
    return [m for smtp in FakeSMTP.instances for m in smtp.sent]


def html_of(message):
    return message.get_body(preferencelist=('html',)).get_content()


def text_of(message):
    return message.get_body(preferencelist=('plain',)).get_content()


# --- the token lock -------------------------------------------------------------

def test_wrong_or_missing_token_is_refused_and_nothing_is_sent(anonymous):
    assert anonymous.get('/tasks/daily-summary').status_code == 403
    assert call(anonymous, 'wrong').status_code == 403
    assert anonymous.get('/tasks/daily-summary?token=wrong').status_code == 403
    assert call(anonymous, TOKEN[:-1]).status_code == 403

    assert FakeSMTP.instances == []


def test_without_a_token_on_the_server_every_request_is_refused(anonymous,
                                                                 monkeypatch):
    monkeypatch.delenv('TASK_TOKEN')

    assert anonymous.get('/tasks/daily-summary?token=').status_code == 403
    assert call(anonymous, '').status_code == 403
    assert FakeSMTP.instances == []


def test_the_token_works_in_the_header_and_in_the_address_without_a_login(
        anonymous):
    header = call(anonymous)
    query = anonymous.get('/tasks/daily-summary?token=' + TOKEN)

    assert header.status_code == 200 and header.get_json()['sent'] is True
    assert query.status_code == 200 and query.get_json()['sent'] is True
    assert len(mails()) == 2


def test_guessing_the_token_is_rate_limited(anonymous):
    codes = [call(anonymous, 'guess%d' % n).status_code for n in range(25)]

    assert codes[:20] == [403] * 20
    assert 429 in codes[20:]


# --- mail settings ----------------------------------------------------------------

@pytest.mark.parametrize('missing', ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER',
                                     'SMTP_PASSWORD', 'MAIL_TO'])
def test_a_missing_setting_skips_the_mail_with_a_200_and_a_log_line(
        anonymous, monkeypatch, caplog, missing):
    configure_mail(monkeypatch, **{missing: None})

    with caplog.at_level(logging.WARNING):
        response = call(anonymous)

    assert response.status_code == 200
    assert response.get_json() == {'sent': False, 'reason': 'mail_not_configured'}
    assert FakeSMTP.instances == []
    assert missing in caplog.text
    assert PASSWORD not in caplog.text


def test_a_port_that_is_not_a_number_skips_the_mail(anonymous, monkeypatch):
    configure_mail(monkeypatch, SMTP_PORT='abc')

    response = call(anonymous)

    assert response.status_code == 200 and response.get_json()['sent'] is False
    assert FakeSMTP.instances == []


def test_recipients_may_be_separated_by_commas_or_semicolons(monkeypatch):
    configure_mail(monkeypatch, MAIL_TO=' a@x.test;b@x.test , c@x.test ')

    settings, missing = read_mail_settings()

    assert missing == []
    assert settings['recipients'] == ['a@x.test', 'b@x.test', 'c@x.test']
    assert settings['port'] == 587


# --- sending ------------------------------------------------------------------------

def test_the_mail_goes_out_over_starttls_to_every_recipient(anonymous):
    response = call(anonymous)

    assert response.get_json() == {'sent': True, 'recipients': 2}
    smtp, = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ('smtp.example.test', 587)
    assert smtp.started_tls and smtp.logged_in_as == ('sender@example.test', PASSWORD)
    message, = smtp.sent
    assert message['Subject'] == 'Günlük Özet – 17.06.2026'
    assert message['From'] == 'sender@example.test'
    assert message['To'] == 'boss@example.test, accountant@example.test'
    # A plain text part and an HTML part.
    assert 'GÜNLÜK ÖZET' in text_of(message)
    assert '<h2' in html_of(message)
    # The answer to the caller never carries the data or the settings.
    assert PASSWORD not in response.get_data(as_text=True)


def test_port_465_uses_tls_from_the_start(anonymous, monkeypatch):
    configure_mail(monkeypatch, SMTP_PORT='465')

    assert call(anonymous).status_code == 200

    smtp, = FakeSMTP.instances
    assert smtp.uses_ssl and not smtp.started_tls and len(smtp.sent) == 1


def test_a_mail_server_failure_gives_502_and_leaks_nothing(anonymous, caplog):
    FakeSMTP.fail_on_login = True

    with caplog.at_level(logging.ERROR):
        response = call(anonymous)

    assert response.status_code == 502
    assert response.get_json() == {'sent': False, 'reason': 'mail_error'}
    body = response.get_data(as_text=True)
    assert 'rejected' not in body and 'Traceback' not in body and PASSWORD not in body
    assert 'could not be sent' in caplog.text
    assert PASSWORD not in caplog.text


# --- what the mail says -----------------------------------------------------------------

def test_mail_lists_waiting_checks_due_today_and_tomorrow_only(anonymous, factory):
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

    html = html_of(mails()[0])
    assert '1.111 ₺' in html and '2.222 ₺' in html
    for hidden in ('3.333', '4.444', '5.555', '6.666'):
        assert hidden not in html
    assert 'PYTEST XlsxOzet Musteri' in html and 'PYTEST XlsxOzetci' in html


def test_names_in_the_mail_are_escaped(anonymous, factory):
    customer_id = factory.customer(first='<b>kalin</b>')
    factory.check(customer_id, D('9876'), due_date=TODAY)
    factory.commit()

    call(anonymous)

    html = html_of(mails()[0])
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
