"""The daily summary e-mail: what falls due, what is late, and the cash
position for the next four weeks.

Reading the data only uses SELECT. Nothing is written to the database. The
mail server settings come from environment variables and are never kept in
the code.
"""
import os
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from flask import render_template

from cashflow import ZERO, monday_of, load_expected_flows, build_cashflow
from check_calendar import WAITING, load_calendar_checks
from overdue import load_overdue_installments, bucket_totals

SUMMARY_WEEKS = 4

# All of these must be set, or no mail is sent.
MAIL_SETTINGS = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD',
                 'MAIL_TO')
SMTP_TIMEOUT_SECONDS = 30


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
    """(settings, missing). `settings` is None unless every value is usable."""
    values = {name: (os.getenv(name) or '').strip() for name in MAIL_SETTINGS}
    missing = [name for name, value in values.items() if not value]
    try:
        port = int(values['SMTP_PORT'])
    except ValueError:
        port = None
        if 'SMTP_PORT' not in missing:
            missing.append('SMTP_PORT')
    if missing:
        return None, missing
    recipients = [a.strip() for a in values['MAIL_TO'].replace(';', ',').split(',')
                  if a.strip()]
    if not recipients:
        return None, ['MAIL_TO']
    return {
        'host': values['SMTP_HOST'], 'port': port,
        'user': values['SMTP_USER'], 'password': values['SMTP_PASSWORD'],
        'recipients': recipients,
    }, []


def build_message(summary, sender, recipients, site_root):
    message = EmailMessage()
    message['Subject'] = 'Günlük Özet – %s' % summary['today'].strftime('%d.%m.%Y')
    message['From'] = sender
    message['To'] = ', '.join(recipients)
    context = {'s': summary, 'site': site_root.rstrip('/')}
    message.set_content(render_template('emails/daily_summary.txt', **context))
    message.add_alternative(
        render_template('emails/daily_summary.html', **context), subtype='html')
    return message


def send_message(message, settings):
    """Send over TLS: port 465 is TLS from the start, anything else upgrades
    with STARTTLS. Raises smtplib/OS errors; the caller logs them."""
    context = ssl.create_default_context()
    if settings['port'] == 465:
        server = smtplib.SMTP_SSL(settings['host'], settings['port'],
                                  timeout=SMTP_TIMEOUT_SECONDS, context=context)
    else:
        server = smtplib.SMTP(settings['host'], settings['port'],
                              timeout=SMTP_TIMEOUT_SECONDS)
    with server:
        if settings['port'] != 465:
            server.starttls(context=context)
        server.login(settings['user'], settings['password'])
        server.send_message(message)
