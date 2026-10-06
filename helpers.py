"""Small helpers shared by the routes: input checks, number format, audit
log, double submit tokens, paging, safe redirects and the login guards."""
from flask import request, redirect, url_for, session, jsonify
from datetime import datetime
import json
from decimal import Decimal
import uuid
from functools import wraps
from urllib.parse import urlsplit, urlunsplit
import random
from core import app


class ValidationError(ValueError):
    """An input problem with a message that is safe to show to the user.

    We raise this from our own checks, so the text is written for people and
    contains no technical detail. Every other exception may carry database or
    code internals, so it goes to the log and the user gets a short message
    that only says which operation failed.

    It extends ValueError to keep the old behaviour of any code that already
    catches ValueError.
    """


# Dates typed in the browser can carry a five digit year, because the date
# input has no upper bound of its own. Postgres stores such a year happily,
# but psycopg2 cannot read it back into a Python date (the limit is 9999) and
# every page that touches the row then fails. So every date coming from a form
# must pass through here before it reaches SQL.
MIN_FORM_YEAR = 2000
MAX_FORM_YEAR = 2100


def parse_form_date(value, label, required=True):
    """Turn a form date string into a date, or raise ValidationError.

    `label` names the field in the message the user sees.
    Returns None when the field is empty and not required.
    """
    value = (value or "").strip()
    if not value:
        if required:
            raise ValidationError("%s alanı zorunludur." % label)
        return None
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValidationError(
            "%s geçerli bir tarih değil. Lütfen gün/ay/yıl alanlarını "
            "kontrol edin." % label)
    if not (MIN_FORM_YEAR <= parsed.year <= MAX_FORM_YEAR):
        raise ValidationError(
            "%s için yıl %d ile %d arasında olmalı. Girilen yıl: %d."
            % (label, MIN_FORM_YEAR, MAX_FORM_YEAR, parsed.year))
    return parsed


# Jinja filter: format numbers like Turkish style (e.g. 2600000 -> 2.600.000)
def format_thousands(value):
    """Format a number with dot as thousands separator and comma as decimal separator.

    - Accepts int, float, Decimal or numeric strings.
    - Returns an empty string for None.
    - Examples: 2600000 -> '2.600.000', 12345.67 -> '12.345,67'
    """
    if value is None:
        return ""
    try:
        # Use Decimal for stable representation
        d = Decimal(value)
    except Exception:
        try:
            d = Decimal(str(value))
        except Exception:
            return str(value)

    sign = '-' if d < 0 else ''
    d = abs(d)

    # integer and fractional parts
    int_part = int(d // 1)
    frac_part = d - int_part

    # format integer part with commas then replace with dots
    int_str = f"{int_part:,}".replace(",", ".")

    if frac_part == 0:
        return sign + int_str

    # get fractional digits (no scientific notation)
    frac_str = format(frac_part, 'f').split('.')[1]
    # remove trailing zeros
    frac_str = frac_str.rstrip('0')
    if not frac_str:
        return sign + int_str

    # Use comma as decimal separator (Turkish style)
    return sign + int_str + ',' + frac_str


# register filter for Jinja templates
app.jinja_env.filters['thousands'] = format_thousands

# Basit audit log helper
def log_audit(cur, user_id, action, entity_type, entity_id=None, details=None):
    """Kritik işlemleri audit_logs tablosuna yazar. Hata alırsa çağıran transaction ile beraber geri alınır."""
    cur.execute(
        """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, action, entity_type, entity_id, json.dumps(details) if details else None)
    )


# --- Double submit protection -------------------------------------------
# A form that creates a new record carries a one time token. The token is
# stored only after it is used, and always inside the same transaction as the
# record itself, so the token survives exactly when the record does.

# How long a used token is kept. It must be longer than the session lifetime
# (12 hours): a form older than the session cannot be submitted anyway,
# because its CSRF check fails first.
SUBMISSION_TOKEN_RETENTION = '48 hours'

# Chance of cleaning old tokens after a successful claim. Cleaning on every
# request would be wasted work, and a cron job would be one more moving part.
SUBMISSION_TOKEN_CLEANUP_CHANCE = 0.02


def submission_token():
    """Give a fresh token to a form. Called from templates."""
    return str(uuid.uuid4())


app.jinja_env.globals['submission_token'] = submission_token


def claim_submission_token(cur, token, endpoint):
    """Try to use a form token once. Return True the first time it is seen.

    Runs on the caller's cursor on purpose: the token is written in the same
    transaction as the record being created, so a failed save also releases
    the token and the user can try again.

    ON CONFLICT keeps the transaction usable when the token was already used.
    It also makes two requests that arrive together safe: the second insert
    waits for the first transaction to finish, then sees the conflict.
    """
    try:
        token = str(uuid.UUID(str(token)))
    except (ValueError, AttributeError, TypeError):
        # A page from the cache, or a form we have not updated yet. Never
        # block a real save because the token is missing or malformed.
        app.logger.warning('Submission token missing or invalid on %s', endpoint)
        return True

    cur.execute(
        """
        INSERT INTO submission_tokens (token, endpoint) VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        RETURNING token
        """,
        (token, endpoint)
    )
    if cur.fetchone() is None:
        app.logger.info('Duplicate submission blocked on %s', endpoint)
        return False

    if random.random() < SUBMISSION_TOKEN_CLEANUP_CHANCE:
        cur.execute(
            "DELETE FROM submission_tokens WHERE used_at < now() - %s::interval",
            (SUBMISSION_TOKEN_RETENTION,)
        )
    return True


PAGE_SIZE = 50


def get_page_number(arg_name='page'):
    """Read a page number out of the query string.

    Anything missing, negative or not a number means page 1, so a hand edited
    address can never produce an error page.
    """
    try:
        page = int(request.args.get(arg_name, 1))
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def page_window(page):
    """(limit, offset) for a page.

    The limit asks for one row more than a page holds. If that extra row comes
    back there is a next page, which saves a second COUNT query on every list.
    """
    return PAGE_SIZE + 1, (page - 1) * PAGE_SIZE


def build_pager(page, has_next, arg_name='page'):
    """The previous and next links for one list.

    Every other query parameter is carried over, so paging keeps the filters
    and the sort order the user picked.
    """
    args = request.args.to_dict(flat=True)

    def link(target_page):
        merged = dict(args)
        merged[arg_name] = target_page
        return url_for(request.endpoint, **merged)

    return {
        'page': page,
        'has_prev': page > 1,
        'has_next': has_next,
        'prev_url': link(page - 1) if page > 1 else None,
        'next_url': link(page + 1) if has_next else None,
    }


def split_page(rows, page, arg_name='page'):
    """Drop the extra row page_window asked for and build the links."""
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], build_pager(page, has_next, arg_name)


def safe_next(value):
    """Return a 'next' address only when it points into this site.

    'next' comes from the address bar or a form field, so anyone can put
    https://another-site in it, and redirecting there after a save would hand
    the user to a stranger. A path starting with a single slash is followed.
    A full address is followed only when it names this host (the referrer
    header looks like that), and then only its path part is kept. Anything
    else returns None and the caller falls back to its own default page.
    """
    if not value:
        return None
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        if parts.scheme not in ('http', 'https') or parts.netloc != request.host:
            return None
        value = urlunsplit(('', '', parts.path or '/', parts.query,
                            parts.fragment))
    if not value.startswith('/') or value.startswith('//') \
            or value.startswith('/\\'):
        return None
    return value


def login_required(view):
    """Send anyone without a session to the login page.

    Goes under @app.route, so the route registers this wrapper. wraps keeps
    the original function name, which Flask uses as the endpoint name, so
    every url_for call keeps working.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper


def json_login_required(payload):
    """Answer 401 with a JSON body instead of redirecting.

    For the endpoints the browser calls from JavaScript: a redirect to the
    login page would arrive as HTML where the caller expects JSON. The body
    is passed in because the existing endpoints do not all use the same one.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify(payload), 401
            return view(*args, **kwargs)
        return wrapper
    return decorator



def repair_out_of_range_dates(conn, cur, columns, page):
    """Reset dates whose year is far in the future, and log it if it happens.

    Postgres accepts a five digit year, but psycopg2 cannot read it back into
    a Python date, so one such row makes every page that reads it fail. This
    is a safety net, not the fix: forms now validate dates through
    parse_form_date, so nothing should reach the database in this state.

    When this net stays silent for a while, it can be removed. If a warning
    shows up in the log, some input path is still missing validation and the
    table name below says where to look.
    """
    try:
        for table, column in columns:
            cur.execute(
                "UPDATE {t} SET {c} = CURRENT_DATE "
                "WHERE EXTRACT(YEAR FROM {c}) > 3000".format(t=table, c=column))
            if cur.rowcount:
                app.logger.warning(
                    'Out of range dates repaired on %s: %s rows in %s.%s '
                    '(an input path is still missing date validation)',
                    page, cur.rowcount, table, column)
        conn.commit()
    except Exception:
        conn.rollback()
        app.logger.exception('Date repair failed on %s', page)
