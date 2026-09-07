# Tests

Characterization tests for the money code. They describe what the
application does **today**, so that a later change either keeps that
behaviour or shows plainly what it altered.

## Running them

```
pip install -r requirements-dev.txt
python -m pytest
```

A single file, or a single test:

```
python -m pytest tests/test_reconcile_customer_payments.py
python -m pytest -k portfolio
```

The whole suite takes about nine and a half minutes. Most of that is opening a new
database connection per test; there is no connection pool yet (pending item
19).

## Safety

**These tests only run against the test database.** Before anything else, a
session fixture looks for the marker project `*** TEST VERITABANI ***`. If it
is not there, the entire run stops with a message instead of touching data.
So a wrong `.env` cannot reach live.

**Tests never edit rows they did not create.** The `factory` fixture inserts
its own rows, remembers their ids and deletes exactly those at the end. The
sample projects already in the test database (46, 47, 48 and Hayriye
Mahallesi) are only ever read. Everything the factory creates is named with a
`PYTEST ` prefix, so anything left behind by a crashed run is easy to find:

```sql
SELECT id, name FROM projects WHERE name LIKE 'PYTEST %';
```

Rows that routes create on their own are cleaned up too: `submission_tokens`
by value, and `audit_logs` rows newer than the id the test started at.

## What is covered

| File | Covers |
|---|---|
| `test_reconcile_customer_payments.py` | Income reconcile: cash, cleared, portfolio and bounced checks, distribution order, overpayment, isolation between flats |
| `test_reconcile_supplier_payments.py` | Expense reconcile, the version with the correct check rule |
| `test_reconcile_expense_payments.py` | The second expense reconcile function, **which is wrong on purpose** — see below |
| `test_installment_plan.py` | Creating and rewriting a payment plan and an expense plan, redistribution, flat totals, refused input |
| `test_check_status_transitions.py` | `/check/update_status` for both check directions, and the effect on the debt |
| `test_submission_tokens.py` | The double submit guard, as a helper and through a route |
| `test_check_rule_on_pages.py` | The check rule as `project_overview` and the cooperative report apply it |
| `test_pagination.py` | Page size, prev/next links, filters and sorting surviving a page turn, the two-list pages turning independently, totals staying whole |
| `test_smoke.py` | Every GET route answers without a server error; login is required |

### Two groups of tests describe known bugs

Their names say so, and they are expected to **fail once the bug is fixed**.
That is the signal that the fix landed and what it changed.

1. `test_reconcile_expense_payments.py`, the tests starting with `WRONG` —
   `reconcile_expense_payments` filters on `oc.status != 'karsiliksiz'`, so a
   check that was only handed over counts as paid. Its twin
   `reconcile_supplier_payments` filters on `= 'odendi'` and is right. Pending
   item 3 in CLAUDE.md. When it is fixed, delete these tests; the twin file
   already describes the rule that should hold.

2. `test_installment_plan.py`, the tests starting with `KNOWN_BUG` —
   `manage_payment_plan` catches a validation error, flashes a message and
   then falls through into its own GET branch using a cursor its `finally`
   block already closed. The user gets a 500 instead of the message. The plan
   itself is not damaged. Found while writing these tests; recorded as
   pending item 5b in CLAUDE.md.

## What is not covered

- **Reports and dashboard arithmetic.** `/reports` and `/dashboard` are only
  smoke tested. Their numbers come from around 900 queries per page and would
  need their own fixtures.
- **`/debts` and `/expenses` totals.** The reconcile functions behind them are
  tested directly, the pages themselves only for status.
- **Templates.** No HTML is asserted. Page level tests read the values handed
  to the template through the `render_context` fixture instead, which is
  steadier and points straight at the wrong number.
- **The login form, CSRF and the rate limit.** CSRF is switched off in the
  client fixture.
- **Postgres specific behaviour** such as concurrent writes to the same
  token.
- **Anything against live data.** By design.

## Fixtures

| Fixture | What it gives |
|---|---|
| `factory` | Creates and cleans up rows. See `conftest.py` for the methods. |
| `cur` | The factory's cursor, for helpers that take one |
| `client` | A logged in Flask test client, CSRF off, `TESTING` off so a real 500 is visible |
| `render_context` | Records what each page passes to its template |
| `user_id` | An existing user id, needed by routes that write an audit log |
