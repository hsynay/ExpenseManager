"""The daily summary e-mail: what falls due, what is late, and the cash
position for the next four weeks.

Reading the data only uses SELECT. Nothing is written to the database. The
mail goes out through the Brevo HTTP API, not through SMTP: the host blocks
outgoing SMTP ports, and a blocked connection would freeze the only worker.
The API key and the addresses come from environment variables and are never
kept in the code.
"""
import os
import re
from datetime import timedelta
from email.utils import parseaddr

import requests
from flask import render_template

from cashflow import ZERO, monday_of, load_expected_flows, build_cashflow
from check_calendar import WAITING, load_calendar_checks
from overdue import load_overdue_installments, bucket_totals

SUMMARY_WEEKS = 4

# All of these must be set, or no mail is sent.
MAIL_SETTINGS = ('BREVO_API_KEY', 'MAIL_FROM', 'MAIL_TO')

BREVO_URL = 'https://api.brevo.com/v3/smtp/email'
# Never wait longer than this for the mail API. Connecting and reading are
# timed separately, so the worst case is about 15 seconds: well below the 30
# seconds after which gunicorn kills a worker. No retries.
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10


class MailSendError(Exception):
    """The mail API did not take the mail. The text is safe to log: the API
    key has been masked out of it."""


def gather_summary(cur, today):
    """Everything the mail shows. Three SELECT queries."""
    tomorrow = today + timedelta(days=1)
    waiting = [c for c in load_calendar_checks(cur) if c['status'] in WAITING]

    def due_on(day):
        rows = [c for c in waiting if c['due_date'] == day]
        return {
            'day': day,
            'checks': rows,
            'incoming': sum((c['amount'] for c in rows if c['kind'] == 'in'), ZERO),
            'outgoing': sum((c['amount'] for c in rows if c['kind'] == 'out'), ZERO),
        }

    late = load_overdue_installments(cur, today)
    late_totals = bucket_totals(late)

    start = monday_of(today)
    end_exclusive = start + timedelta(days=7 * SUMMARY_WEEKS)
    cash = build_cashflow(load_expected_flows(cur, end_exclusive), start,
                          end_exclusive)

    return {
        'today': today,
        'due_today': due_on(today),
        'due_tomorrow': due_on(tomorrow),
        'late_total': late_totals['total'],
        'late_customers': len(late),
        'late_oldest_days': max((r['oldest_days'] for r in late), default=0),
        'cash': cash,
        'weeks': SUMMARY_WEEKS,
    }


def read_mail_settings():
    """(settings, missing). `settings` is None unless every value is usable.

    MAIL_FROM is "Name <address>" or just an address; MAIL_TO is one address
    or several separated by commas.
    """
    values = {name: (os.getenv(name) or '').strip() for name in MAIL_SETTINGS}
    missing = [name for name, value in values.items() if not value]

    sender_name, sender_email = parseaddr(values['MAIL_FROM'])
    if values['MAIL_FROM'] and '@' not in sender_email:
        missing.append('MAIL_FROM')
    recipients = [a.strip() for a in values['MAIL_TO'].replace(';', ',').split(',')
                  if a.strip()]
    if values['MAIL_TO'] and not recipients:
        missing.append('MAIL_TO')

    if missing:
        return None, missing
    return {
        'api_key': values['BREVO_API_KEY'],
        'sender_name': sender_name, 'sender_email': sender_email,
        'recipients': recipients,
    }, []


def render_mail(summary, site_root):
    """The subject and the two bodies, from the existing mail templates."""
    context = {'s': summary, 'site': site_root.rstrip('/')}
    return {
        'subject': 'Günlük Özet – %s' % summary['today'].strftime('%d.%m.%Y'),
        'text': render_template('emails/daily_summary.txt', **context),
        'html': render_template('emails/daily_summary.html', **context),
    }


def build_payload(mail, settings):
    """The JSON body the Brevo API expects."""
    sender = {'email': settings['sender_email']}
    if settings['sender_name']:
        sender['name'] = settings['sender_name']
    return {
        'sender': sender,
        'to': [{'email': address} for address in settings['recipients']],
        'subject': mail['subject'],
        'htmlContent': mail['html'],
        'textContent': mail['text'],
    }


def mask_secret(text, secret):
    """Hide the API key (and anything shaped like one) in text before it is
    logged or put in an error."""
    text = str(text)
    if secret:
        text = text.replace(secret, '***')
    return re.sub(r'xkeysib-[\w-]+', '***', text)


def send_mail(payload, api_key):
    """Post the mail to Brevo. Raises MailSendError on any failure."""
    headers = {'api-key': api_key, 'accept': 'application/json'}
    try:
        response = requests.post(
            BREVO_URL, json=payload, headers=headers,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
    except requests.RequestException as exc:
        # `from None`: do not carry the original exception along into logs.
        raise MailSendError('%s: %s' % (
            type(exc).__name__, mask_secret(exc, api_key))) from None
    if not 200 <= response.status_code < 300:
        raise MailSendError('Brevo answered HTTP %d: %s' % (
            response.status_code, mask_secret(response.text[:300], api_key)))
