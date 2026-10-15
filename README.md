# ExpenseManager

A finance and pre-accounting web application for construction companies and
building cooperatives. It tracks apartment sales and customer instalments,
supplier expenses, cheques, petty cash and monthly cooperative accounts, and
answers three questions at any time: what has actually been paid, what is still
owed, and what is expected to come in or go out next.

It was written for a single construction company, which has used it for its
real accounts. The user interface is in Turkish; the code, comments, tests and
documentation are in English.

## Features

- **Projects**: a project is either a normal sales project or a cooperative;
  flats are grouped by block and floor and can be assigned to customers.
- **Income**: instalment plans per flat, cash and cheque payments, a printable
  debt statement per flat, an income list with filters and Excel export.
- **Expenses**: expenses with their own payment plans, suppliers, supplier
  payments (one at a time or spread over several expenses), petty cash.
- **Cheques**: incoming and outgoing cheques with a status workflow. A cheque
  closes a debt only when it is marked as cleared (see
  [Business rule](#the-cheque-rule)).
- **Reports**: dashboard, monthly income/expense charts, a monthly report for
  cooperatives (carried balance, paid, still owed), project overview, project
  profitability.
- **Forecasts** (read only): cash flow for 3/6/12 months by week, a cheque
  calendar, and ageing of overdue instalments (0-30, 31-60, 61-90, 90+ days).
- **Daily summary e-mail**, triggered by an external scheduler.
- **Excel export** of the four list pages; filters and sorting carry over.
- **Audit log** of record-changing actions.

## Technology and why

| Choice | Reason |
|---|---|
| Python 3.11, Flask 3 | Small, explicit and easy to read; one developer, one deployable process. |
| PostgreSQL (hosted on Supabase, used only as a database) | Money data needs transactions and constraints. Supabase Auth, Storage and the data API are not used. |
| **Raw SQL with psycopg2, no ORM** | Deliberate. Most screens are aggregations over several tables (payments against schedules, with a rule about cheques). Having the exact SQL in view made the numbers auditable and made it possible to count and tune queries. An ORM would hide the query count that mattered most here. |
| **Modules instead of Blueprints** | Deliberate. Every route is `@app.route` on one shared app object, split into files by topic. Endpoint names stay plain function names, so `url_for('list_expenses')` did not change when the 4,600-line file was split. Blueprints would have meant renaming every `url_for` call in the templates for no gain at this size. |
| Jinja2, Bootstrap 5, Chart.js, vanilla JS | Server-rendered pages. Filtering, sorting and paging happen in the backend, so a page and its Excel export share the same query code. |
| Flask-WTF (CSRF), Flask-Limiter (login rate limit) | Standard, small, and enough for the threat model. |
| gunicorn, one worker | The app runs on a free-tier host. The in-memory rate limiter and the connection pool are sized for a single worker. |

Pinned dependencies are in `requirements.txt` (tested with Python 3.11.9).

## Architecture

### Files

| Path | Role |
|---|---|
| `app.py` | Entry point (`gunicorn app:app`). Imports `routes` so every route registers. |
| `core.py` | The Flask `app` object, configuration, CSRF, rate limiter, error handlers. |
| `db.py` | Connection pool (`ThreadedConnectionPool`), stale-connection check, connect timeout. |
| `helpers.py` | `login_required`, `safe_next`, date parsing, paging, audit logging, double-submit tokens. |
| `reconcile.py` | Rebuilds instalment schedules from payments. The core of the money logic. |
| `expense_plan.py` | One definition of "the plan of an expense", shared by three screens. |
| `schedule_drift.py` | Read-only detector for stale schedules; also finds open instalments for bulk payments. |
| `profitability.py`, `cashflow.py`, `check_calendar.py`, `overdue.py` | Read-only calculations behind the forecast and profitability screens. |
| `daily_summary.py` | Builds and sends the daily e-mail through an HTTP mail API. |
| `excel_export.py` | Workbook generation, with protection against formula injection. |
| `routes/` | `auth`, `projects`, `income`, `expenses`, `checks`, `reports`, `exports`, `forecast`, `tasks`. |
| `templates/`, `static/` | Jinja2 templates, static assets. |
| `migrations/` | Numbered SQL files, applied by hand. |
| `queries/` | Read-only diagnostic SQL, meant to be run by hand against production. |
| `maintenance/` | One-off repair script (kept for the record). |
| `tests/` | The test suite; see `tests/README.md`. |
| `.github/workflows/backup.yml` | Daily database backup. |

### How a request flows

```
browser -> host proxy (TLS) -> gunicorn -> Flask app (core.py)
        -> ProxyFix (real client address)
        -> CSRF check on POST
        -> route function (routes/*.py), wrapped by @login_required
        -> get_connection(): a pooled psycopg2 connection
        -> parameterised SQL, business rules in Python
        -> Jinja2 template -> HTML
```

A connection is returned to the pool when the route calls `close()`; a
teardown hook returns any that a request forgot, and a returned connection is
always rolled back first.

### How money moves

Payments are the source of truth. The instalment tables are derived from them:

```
payments / supplier_payments         (what really happened)
        |
        |  reconcile_customer_payments / reconcile_supplier_payments
        |  (after every payment, cheque status change and plan edit)
        v
installment_schedule / expense_schedule   (derived: paid_amount, is_paid)
```

Reconcile clears the schedule and spreads the valid total over the instalments,
oldest due date first. Read-only screens never call it; they repeat the same
rule in SQL, and tests run both side by side so they cannot drift apart.

### The cheque rule

A cheque closes a debt only after it has been marked by hand: an incoming
cheque as cleared, an outgoing cheque as paid. The due date is information
only. A cheque still in the portfolio, or handed over but not yet paid, is
shown but counted nowhere; a bounced cheque is counted nowhere at all.
Forecast screens are the one exception: there, a pending cheque is treated as
money expected on its due date, because they show expectations, not facts.

### Data model

Sixteen tables: `users`, `customers`, `projects`, `flats`; income side
`installment_schedule`, `payments`, `checks`; expense side `suppliers`,
`expenses`, `expense_schedule`, `supplier_payments`, `outgoing_checks`,
`petty_cash_expenses`; and `cooperative_monthly_summary`, `audit_logs`,
`submission_tokens`.

## Security

- Passwords are stored as Werkzeug hashes; the session secret comes from the
  environment and the app refuses to start without it.
- Session cookie: `HttpOnly`, `SameSite=Lax`, `Secure` in production, 12 hour
  lifetime.
- CSRF protection on every POST, including `fetch()` calls (header token).
- Login rate limit of 10 failed attempts per minute per client address, using
  the real address behind the proxy.
- Redirect targets after login are validated; addresses outside the site are
  dropped (no open redirect).
- All SQL values are query parameters. The few dynamic parts (sort columns)
  come from fixed whitelists. Template autoescaping is on.
- Exception details are logged, never shown to the user.
- Date inputs are bounded at the source (a stray year such as 22026 once made
  pages fail), and money columns have fixed precision and `CHECK` constraints
  on status values.
- Record-creating forms carry a one-time token claimed in the same transaction
  as the record, so a double click cannot create two records.
- The scheduled-job endpoint is protected by a secret token (compared in
  constant time) and its own rate limit, and refuses everything when no token
  is configured.
- Excel export neutralises cells that begin with `=`.
- Outgoing HTTP calls and database connections have timeouts, so one slow
  dependency cannot hold the single worker.

## Tests

**354 tests**, run against a real PostgreSQL test database rather than mocks.

```
pip install -r requirements-dev.txt
python -m pytest
```

What they protect:

- the reconcile functions and the cheque rule, including on every page that
  applies it (project overview, cooperative report, debt statement);
- plan rewriting and redistribution, bulk supplier payments, double-submit
  protection;
- agreement between screens: the cooperative report, the profitability screen
  and the project overview must give the same paid and still-owed figures;
- a watch over stale schedules, and a smoke test that every GET route answers.

Safety: before anything runs, a session fixture looks for a marker project in
the database and stops the whole run if it is missing, so a wrong `.env` cannot
reach production data. Tests only delete rows they created themselves (named
with a `PYTEST ` prefix). More detail, and what is not covered (CSRF, the login
form, the rate limit), is in [tests/README.md](tests/README.md).

The suite is slow (about 13 minutes) because every test talks to a remote
database.

## Setup

Requirements: Python 3.11 and a PostgreSQL database.

```bash
git clone <repository-url>
cd <repository-folder>

python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
cp .env.example .env             # then fill in the values
```

In `.env`:

- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`: your database.
- `SECRET_KEY`: a long random string, for example
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- `SESSION_COOKIE_SECURE=false` for local `http`.
- `TASK_TOKEN`, `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_TO`: only for the daily
  e-mail; without them the job simply skips sending.

Database: create the tables (see [Limitations](#limitations)), apply the files
in `migrations/` in order with `psql -f`, and insert a first user with a
password hash from `werkzeug.security.generate_password_hash`.

```bash
python app.py                    # development server with debug on
```

## Deployment

- **Host**: Render, started with `gunicorn app:app` on a single worker. A
  scheduler pings `/ping` to keep the free instance awake and calls
  `/tasks/daily-summary` once a day with the task token.
- **Database**: Supabase Postgres through the session pooler (port 5432), which
  the connection pool needs.
- **Backups**: a GitHub Actions workflow dumps the `public` schema every day
  at 02:00 UTC and keeps the file as an artifact for 30 days. A second, full
  dump is attempted on a best-effort basis.
- **Mail**: sent through an HTTP mail API, because the free host blocks
  outgoing SMTP ports.
- **Migrations** are plain SQL, applied by hand; there is no migration tool.

## Limitations

Stated plainly, for anyone evaluating the project:

- The base schema is not in the repository. `migrations/` holds only the
  changes made after the first version, so a fresh database cannot be built
  from the repository alone yet.
- No continuous integration for the test suite; it needs a populated test
  database with the marker project.
- The login rate limiter and the connection pool live in memory, which is
  correct for one worker only.
- All users currently share one login, so the audit log cannot tell who made a
  change. Per-user roles are not implemented.
- The application sets no security headers (CSP, HSTS) itself; transport
  security is left to the host.
- Receipt uploads, customer-facing statements and server-side PDF generation
  are not implemented.

See [docs/engineering-notes.md](docs/engineering-notes.md) for how the code got
to its current shape.
