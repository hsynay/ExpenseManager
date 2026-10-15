# Engineering notes

A case study of one application: a Flask system that worked and was in daily
use, but had never been reviewed. These notes describe what the review found,
what was changed, what was measured, and what did not go as planned.

The application handles money for a real company, so the order of work was
deliberate: first make the existing behaviour visible and protected, then
change it.

## 1. Starting point

When the review began, the application had:

- **one file**: `app.py` with 4,644 lines and 42 routes, including every
  query, every rule and every HTML helper;
- **no automated tests**;
- **no backups** of the database;
- **no CSRF protection** on any form;
- a session secret generated with `os.urandom(24)` at every start, so every
  deploy logged everybody out;
- unpinned dependencies, so a deploy installed whatever was newest that day;
- 37 copies of the same "is the user logged in?" check, one per route;
- about 37 places that opened their own database connection, with no pool.

None of this was unusual for a first version written quickly and shipped. The
point of the review was to make it safe to keep changing.

## 2. What was found, and how

| Problem | How it was found |
|---|---|
| Session secret regenerated on each start | Reading the configuration. |
| No CSRF protection; unlimited login attempts; exception text shown on screen | Reading the code with security in mind. Each item became its own commit. |
| Behind the host's proxy, every visitor had the proxy's address, so a login rate limit would have counted everybody as one person | Following the limiter's idea through to what `remote_addr` really holds. |
| Dependencies unpinned | Reading `requirements.txt`. |
| The same payment rule applied differently on different pages: some pages let an uncleared cheque close an instalment, others did not | Going through every page that adds up payments and checking it against one written rule. The history has one commit per page. |
| Two functions that rebuild an expense's schedule from its payments, with different cheque filters; one counted a cheque that was only handed over as paid | Writing characterization tests (section 6). The tests recorded it as "wrong on purpose" until it was removed. |
| A validation error path that flashed a message and then used a cursor its own `finally` had closed, giving the user a 500 | Writing tests for the plan forms. |
| A date typed with a five digit year (22026) made every page reading that table fail | Pages failing with a 500. One bad row in one table was enough. |
| Two GET routes that wrote data | Review. |
| Nothing stopped the same form from being submitted twice, so a double click could create two records | Review of the record-creating routes. |
| `/reports` was slow | Counting queries per request: 323. |
| Almost no indexes (only `audit_logs` had any) | Listing the filters and joins the code really uses and comparing with the indexes. |
| A positional unpack that did not match the query's column count in one branch | An error in the cooperative branch of the debts page. Positional tuple access is the cost of raw SQL, see section 4. |

A last one came much later, and deserves its own paragraph. A profitability
screen added in October showed a warning on three expenses of one project: two
had been paid exactly twice their plan, and one showed "paid" in the schedule
while every payment behind it was only a handed-over cheque. The doubled
payments were duplicate entries and were removed through the application. The
third was a stale schedule, and it existed only in the test copy of the data.
Looking into how a schedule can go stale turned up a route that could produce
exactly that state (section 4). Whether that route produced this particular
row, I do not know. A read-only query run on the production database later
returned no stale schedule at all, so the fix there is preventive, not a
repair.

## 3. Changes and measurements

### The reports page

`/reports` built each figure by issuing queries inside loops (an N+1 pattern).
Counting the queries per request, with a small fixture that records every SQL
statement a request runs, gave the baseline.

| | Before | After |
|---|---|---|
| Queries per request | 323 | 76 |
| Response time | 18.4 s | 4.3 s |

The times were taken against a database in another network, so they are
dominated by round trips. The query count is the portable number.

### Indexes

Only `audit_logs` had an index. The test database held a few hundred rows,
where Postgres always chooses a sequential scan and no index can show a
difference, so the measurement used a synthetic data set inside a transaction
that was rolled back afterwards: 4,000 flats, 60,000 payments, 120,000
instalments, 40,000 supplier payments, and so on. Each query was run twice
before and twice after, keeping the faster run (`EXPLAIN ANALYZE`, ms):

| Query | Before | After |
|---|---|---|
| Payments behind one cheque | 5.94 | 0.04 |
| Instalments of a flat | 8.53 | 0.09 |
| Schedule of an expense | 2.87 | 0.05 |
| Reconcile core, income side | 4.75 | 0.09 |
| Reconcile core, expense side | 3.27 | 0.06 |
| Overdue instalments of a project | 13.77 | 2.17 |
| Project income with the cheque rule | 12.07 | 3.49 |
| Flats by project | 0.37 | 0.11 |

Fifteen indexes were added. **Six candidates were measured and rejected**: one
was already covered by a unique constraint, one was never read by any query
(an index nothing reads is pure write cost), one could not serve its only
query, and so on. The migration file records each reason.

Two honest caveats. The live tables are much smaller than the synthetic set,
so the live gain is smaller than the table suggests. And the reconcile queries
are the ones that matter, not the biggest ratios: they run on every payment,
every cheque status change and every plan edit.

### Connection pool

Every request opened its own connection, which costs a network round trip and
a login. `get_connection()` kept its signature and `close()` now returns the
connection to a `ThreadedConnectionPool` (1 to 10 connections), so none of the
call sites changed. Three details mattered more than the pool itself:

- a returned connection is always rolled back first, so a request can never
  inherit a half-finished transaction;
- a connection idle for more than 30 seconds is tested with `SELECT 1` before
  reuse, because the database side drops idle connections silently;
- a teardown hook returns any connection a route forgot to close.

I did not measure the pool's effect in isolation, and I will not claim a
number for it.

### Splitting the file

`app.py` went from 4,644 lines to a 15-line entry point plus a
`routes/` package of nine modules. The move was done after the tests existed
and was meant to be mechanical: the money functions were moved, not rewritten
(the header of `reconcile.py` says so). Two details are worth recording. The `app`
object lives in `core.py`, because routes importing it from `app.py` would get
a second copy under `python app.py`. And the 37 login checks became one
`login_required` decorator.

### Security hardening

Session secret from the environment (the app refuses to start without one),
hardened cookies, CSRF on every POST, a login rate limit that sees the real
client address, error details logged instead of displayed, open-redirect
protection on the login `next` parameter, validated date input, pinned
dependencies, a double-submit guard, and a daily database backup. The
double-submit guard stores a one-time token in the same transaction as the
record it protects, so even two simultaneous requests cannot both succeed.

## 4. Architecture decisions

### No ORM

This was a choice, not an omission. The valuable code in this application is
aggregation: payments against schedules, under a rule about cheques, across
four or five tables. Writing that SQL by hand kept it visible. It is also how
the query count above could be measured and reduced at all; an ORM would have
hidden exactly that.

The cost is real and I will state it. Rows are tuples and templates read them
by position (`p[9]`). A wrong index does not raise an error; it shows a
different column. The unpack mismatch in the table above is this exact failure.
The mitigation is tests that read the values a page hands to its template and
compare them with known numbers, not trust.

### No Blueprints

Every route is registered with `@app.route` on one shared app, split into
files by topic. Endpoint names stay plain function names, so templates did not
change when the file was split. Blueprints would have prefixed every endpoint
and forced edits to every `url_for` in the templates, in exchange for
namespacing that a single-developer application of this size does not need.
This decision is meant to be revisited if the number of developers grows.

### `expense_schedule` is a derived table, and that is a risk

The schedule table holds two different kinds of information:

- **the plan**: instalment amount and due date, typed in by a user. This is
  source data.
- **the payment state**: `paid_amount` and `is_paid`. This is derived from
  `supplier_payments` and rebuilt by one function, `reconcile_supplier_payments`.

Many screens read the derived columns directly, which is convenient and fast,
and it works as long as every code path that changes a payment also calls
reconcile. A path that forgets leaves a stale schedule, and nothing fails: a
page simply shows a wrong "paid" figure.

That is the failure mode, and the code had one instance of it. A bulk
supplier payment route spread a payment over several expenses by updating the
schedule itself, then recorded a single payment row against only one of them.
The other expenses looked paid with no payment behind them, and the next
reconcile on such an expense reset it to zero. The tests written for the fix
fail against the old route for exactly these reasons.

The fix was in three layers, because fixing the route alone only removes one
cause:

1. **Remove the cause.** The route now writes one payment row per expense and
   calls reconcile; it never writes the schedule. It also decides what is open
   from the real payments, not from the stored columns, so a stale schedule
   cannot hide a debt.
2. **Detect it.** A read-only detector recomputes what the schedule should say
   and compares. A hand-run SQL file does the same on production.
3. **Make it visible.** An expense card whose schedule disagrees with its
   payments shows a small warning, without changing the figure it shows, and a
   test fails if new drift appears in the test database.

The detector is detection, not prevention. Any future code that writes the
schedule directly would reintroduce the problem; what stands in the way is a
documented rule ("never UPDATE the schedule; write a payment and reconcile"),
a test, and a warning on the screen.

Related, and found in the same pass: "the plan of an expense" had two answers.
An expense entered with no instalments was missing from the commitment on the
reports, so what was still owed looked smaller and the forecast profit larger.
There is now one definition (the sum of its instalments, or the expense amount
when it has none) in one module, used by three screens, and a test that runs
the same scenario through all three.

## 5. Business rule: when does a cheque close a debt?

Cheques are common in this business, and a cheque is a promise, not money.

The rule, as written down and then applied to every page:

- a cheque closes a debt only after someone marks it by hand (cleared for an
  incoming cheque, paid for an outgoing one);
- its due date is information only; nothing happens automatically when it
  passes;
- a cheque in the portfolio, or handed over but not yet paid, is shown and
  counted nowhere;
- a bounced cheque is counted nowhere at all;
- one exception: forecast screens treat a pending cheque as expected money on
  its due date, because they show expectations.

The rule in SQL is short:

```sql
-- money that really moved on the income side
WHERE p.payment_method = 'nakit' OR c.status = 'tahsil_edildi'
```

The hard part was not writing it but making it true everywhere. It was applied
page by page (project overview, monthly chart, debt statement, cooperative
report, a monthly payments API), and each application was its own commit.
Several pages had applied their own variant of it. What finally made it hold
was moving the rule into tests that run every page that
applies it, and into a test that runs the real reconcile functions next to the
read-only SQL copies and compares them.

A smaller lesson from the same rule: the cooperative report used to read a
column that nothing ever filled, which defaulted to "cash", so every expense
looked paid. It now reads the payments themselves.

## 6. Test strategy

### Characterization first

The first suite was written before any structural change, and it describes what
the code did, not what it should do. 92 tests in the first version, covering
the reconcile functions, the plan forms, cheque status changes, pagination,
the double-submit guard and a smoke test of every GET route. Bugs it found were
recorded as tests named so ("WRONG...", "KNOWN_BUG...") that were expected to
fail once the bug was fixed, which made the fix visible when it landed.

Why before refactoring: for money code, "the numbers did not change" is the
only acceptance criterion that matters, and a refactor with no test is a
refactor with no definition of done.

### Real database, small guard rails

Tests run against a real PostgreSQL database, because the rules live in SQL and
mocks would test the mock. Two safeguards:

- a session fixture looks for a marker project and stops the run if it is not
  there, so a wrong `.env` cannot reach production;
- tests only delete what they created, identified by a name prefix.

Page-level tests read the values a view hands to its template instead of
parsing HTML. A failure then names the wrong number directly.

### Proving the tests protect

Writing a test that passes proves little; the question is whether it fails
when it should. For the most recent work I ran the new tests against the old
code, and against 14 deliberately broken versions of the new code (a detector
that counts a handed-over cheque as paid, one that forgets the cap at the plan,
a report that lists the wrong expenses, and so on). Every one turned at least
one test red. That check found no test that failed to fail, but it did change
how I read the suite: several tests turned out to be guarding one behaviour
twice, and a few behaviours were guarded by exactly one test.

The suite is now 354 tests. It takes about 13 minutes, nearly all of it round
trips to a remote database.

## 7. What I learned

**What worked**

- Tests first. Everything after, including the split of a 4,600-line file, was
  possible because "same numbers as before" could be checked.
- Counting before optimising. The query counter made the N+1 impossible to
  argue with, and made it clear when to stop.
- Measuring indexes on realistic volume, and rejecting some of them.
- One written rule for the cheque logic, and tests that tie screens together.
  The most dangerous bugs here were not crashes but two pages giving
  different, plausible numbers.

**What did not work, or not at first**

- **My first fix for the cooperative report lost data.** I rewrote the expense
  side to read real payments. It was correct for expenses with payments, and
  silently dropped expenses that had neither a plan nor a payment: a sample
  project went from six expense rows to two. No test caught it. I caught it by
  comparing the report before and after on a sample project, and only then
  wrote the tests. Lesson: for a rewrite of a report, compare old and new
  output on real-shaped data before trusting the new tests.
- **Characterization tests that encode a bug are awkward.** They need
  explanation, and someone has to delete them at the right time. They were
  worth it, but they are a debt with a due date.
- **Fixing the cause did not fix the data.** Correcting the route did nothing
  for rows already damaged. That needs its own detection and a deliberate,
  separate repair. In this case the production database turned out to be clean,
  but I would not have known without the query.
- **A feature built on the wrong assumption about the host.** The daily summary
  was first sent over SMTP, and the free host blocks outgoing SMTP ports. It was
  rewritten to use an HTTP mail API. Checking what the platform allows before
  building would have saved a rewrite.
- **The backup workflow took three attempts.** The runner's `pg_dump` was older
  than the server, which refuses a newer server. The first version failed; the
  fix was installing a matching client and verifying the version in the
  workflow itself.
- **Documentation next to code goes stale.** The test README still describes a
  state before the connection pool and mentions a test file that no longer
  exists. Nothing checks prose, and I only noticed while writing this.
- **A slow suite changes behaviour.** At 13 minutes people run it less. The
  pool removes connection setup, but the cost that remains is a network round
  trip per statement. A local test database would be the real fix and has not
  been done.

**Still open**

- All users share one login, so the audit log cannot say who changed what.
- The base schema is not in the repository; only incremental migrations are.
- The login rate limiter and pool assume one worker.
- Nothing prevents new code from writing a derived table directly except a
  rule, a test and a warning.
