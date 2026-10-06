"""The Flask app object and the settings every request depends on.

The app is created here and not in app.py so that the route modules can
import it. "python app.py" loads app.py under the name __main__; if the
routes imported app.py they would get a second copy of it and register on an
app nobody serves.
"""
from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    jsonify,
)
from db import release_request_connections
import os
from datetime import timedelta
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix


# The name 'app' and the explicit root_path keep the logger name and the
# templates and static folders exactly as they were when this line lived
# in app.py.
app = Flask('app', root_path=os.path.dirname(os.path.abspath(__file__)))

# Read the .env file before we use any environment variable below.
load_dotenv()

# The secret key signs the session cookie, so it must stay the same after a
# restart and be shared by all gunicorn workers. A random key here would log
# users out at random times.
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Add it to .env for local development, "
        "or to the environment variables of the host in production."
    )

# Session cookie hardening.
# SESSION_COOKIE_SECURE tells the browser to send the cookie over HTTPS only.
# It must be true in production, but false for local http development.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

# CSRF protection for every POST request. Templates must send the token in a
# hidden "csrf_token" field, or in the "X-CSRFToken" header for fetch() calls.
csrf = CSRFProtect(app)

# The host runs the app behind a proxy, so request.remote_addr is the address
# of the proxy, not of the visitor. ProxyFix reads the real client address
# from the rightmost value of X-Forwarded-For, which only the proxy can set.
# Without this the rate limit below would count all visitors as one person.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Rate limiting. There is no global limit on purpose: only /login is limited,
# so normal work is never slowed down. Counters live in memory, which is fine
# because the app is kept awake and we do not want to run Redis.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri='memory://',
)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    """Show a clear message instead of a raw 400 page.

    This usually happens when the session expired and the page was left open
    for a long time.
    """
    app.logger.warning('CSRF validation failed: %s', e.description)
    # fetch() calls expect JSON, not a redirect.
    if request.is_json or request.accept_mimetypes.best == 'application/json':
        return jsonify({'error': 'csrf_failed'}), 400
    flash('Güvenlik doğrulaması başarısız oldu. Oturumunuz zaman aşımına '
          'uğramış olabilir, lütfen sayfayı yenileyip tekrar deneyin.', 'danger')
    # Do not redirect to request.referrer: that header is attacker controlled
    # and would create an open redirect.
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.errorhandler(429)
def handle_rate_limit(e):
    """Too many failed login attempts from the same address.

    WARNING: login.html is hardcoded here. This is correct only while /login
    is the single route with a rate limit. If you add a limit to any other
    route, this handler must choose the page by request.endpoint, otherwise
    the user gets the login page after, say, a blocked report request.
    """
    app.logger.warning('Rate limit reached on %s from %s',
                       request.path, get_remote_address())
    flash('Çok fazla başarısız giriş denemesi yapıldı. Güvenlik nedeniyle '
          'bir süre beklemeniz gerekiyor. Lütfen 1 dakika sonra tekrar '
          'deneyin.', 'danger')
    return render_template('login.html'), 429


@app.teardown_appcontext
def return_db_connections(exc):
    """Put back any pooled connection this request did not close itself."""
    release_request_connections(exc)
