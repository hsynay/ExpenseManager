"""Routes for scheduled jobs. A cron service calls them, not a logged in user,
so they are protected by a secret token instead of a session."""
import hmac
import os

from flask import jsonify, request
from db import get_connection
from core import app, limiter
from helpers import today_istanbul
from daily_summary import (
    gather_summary, read_mail_settings, render_mail, build_payload, send_mail,
    MailSendError,
)


def task_token_is_valid():
    """True when the request carries the secret from TASK_TOKEN.

    The token may come in the X-Task-Token header (better: addresses end up in
    server logs) or in the ?token= query parameter. If TASK_TOKEN is not set
    on the server, every request is refused, so a missing setting can never
    leave the job open.
    """
    expected = os.getenv('TASK_TOKEN') or ''
    given = request.headers.get('X-Task-Token') or request.args.get('token') or ''
    if not expected:
        return False
    return hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


@app.route('/tasks/daily-summary')
# The token is the only lock on this address, so guessing it is slowed down.
@limiter.limit('20 per minute')
def daily_summary_task():
    if not task_token_is_valid():
        app.logger.warning('Daily summary refused: bad or missing task token')
        return jsonify({'error': 'forbidden'}), 403

    settings, missing = read_mail_settings()
    if settings is None:
        # Not an error: a fresh install has no mail account yet.
        app.logger.warning('Daily summary skipped, mail settings missing: %s',
                           ', '.join(missing))
        return jsonify({'sent': False, 'reason': 'mail_not_configured'}), 200

    conn = get_connection()
    cur = conn.cursor()
    try:
        summary = gather_summary(cur, today_istanbul())
    except Exception:
        app.logger.exception('Daily summary: could not read the data')
        return jsonify({'sent': False, 'reason': 'data_error'}), 500
    finally:
        cur.close()
        conn.close()

    try:
        mail = render_mail(summary, request.url_root)
        send_mail(build_payload(mail, settings), settings['api_key'])
    except MailSendError as exc:
        # The text already has the API key masked out. The caller only learns
        # that it failed.
        app.logger.error('Daily summary: the mail could not be sent: %s', exc)
        return jsonify({'sent': False, 'reason': 'mail_error'}), 502
    except Exception:
        app.logger.exception('Daily summary: the mail could not be prepared')
        return jsonify({'sent': False, 'reason': 'mail_error'}), 502

    app.logger.info('Daily summary sent to %d recipient(s)',
                    len(settings['recipients']))
    return jsonify({'sent': True, 'recipients': len(settings['recipients'])})
