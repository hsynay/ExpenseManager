"""expense_schedule drift: the stored schedule against what the payments say.

expense_schedule is a derived table. supplier_payments is the truth, and
reconcile_supplier_payments() rebuilds the schedule from it. The schedule can
go stale when something writes it by hand or when a check changes status
without a reconcile (expense 107 in the live data).

Two things look for drift and both are covered here:
* queries/schedule_drift_check.sql, the read only query that is
  run by hand on the live database;
* schedule_drift.find_schedule_drift(), the permanent check the expense page
  uses.

Every test builds its own rows. Stale rows are made by writing the stored
columns of rows the test created itself, and no other row is touched.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import sys

from reconcile import reconcile_supplier_payments
from schedule_drift import find_schedule_drift

D = Decimal
JAN = date(2026, 1, 20)
FEB = date(2026, 2, 20)
MAR = date(2026, 3, 20)

SQL_FILE = (Path(__file__).resolve().parent.parent
            / 'queries' / 'schedule_drift_check.sql')

# Column positions of the query result.
ID, TITLE, PROJECT, TYPE, STORED, REAL, DIFF, BAD, PLAN, VALID = range(10)


def run_check(cur):
    cur.execute(SQL_FILE.read_text(encoding='utf-8'))
    return cur.fetchall()


def build_drift_scenario(factory):
    """Seven expenses. Five of them have a stale schedule.

    name  plan            valid payments         stored schedule
    A     3 x 1000        cash 1500              1000,1000,0 paid     (+500)
    B     2 x 1000        cash 1500              1000,500 (right)     listed: no
    C     2 x 1000        check 'verildi' 2000   1000,1000 paid       (+2000)
    D     1 x 1000        cash 3000 (overpaid)   1000 (capped, right) listed: no
    E     2 x 1000        cash 1000              0,1000 (wrong place) (0)
    F     2 x 1000        cash 2000              0,0                  (-2000)
    G     1 x 1000        cash 1000              1000 but is_paid off (0)
    """
    coop_id = factory.project('drift koop', project_type='cooperative')
    project_id = factory.project('drift normal')
    supplier_id = factory.supplier(project_id)
    ids = {}

    def expense(name, project, amounts, stored, flags):
        expense_id = factory.expense(project, supplier_id,
                                     amount=sum(amounts), title=name)
        for due, amount, paid, flag in zip((JAN, FEB, MAR), amounts, stored,
                                           flags):
            factory.expense_installment(expense_id, due, amount,
                                        paid_amount=paid, is_paid=flag)
        ids[name] = expense_id
        return expense_id

    a = expense('A', project_id, [D(1000)] * 3,
                [D(1000), D(1000), D(0)], [True, True, False])
    factory.supplier_payment(a, supplier_id, D(1500))

    b = expense('B', project_id, [D(1000)] * 2,
                [D(1000), D(500)], [True, False])
    factory.supplier_payment(b, supplier_id, D(1500))

    c = expense('C', coop_id, [D(1000)] * 2,
                [D(1000), D(1000)], [True, True])
    check_id = factory.outgoing_check(supplier_id, D(2000), status='verildi')
    factory.supplier_payment(c, supplier_id, D(2000), 'çek', check_id=check_id)

    d = expense('D', project_id, [D(1000)], [D(1000)], [True])
    factory.supplier_payment(d, supplier_id, D(3000))

    e = expense('E', project_id, [D(1000)] * 2,
                [D(0), D(1000)], [False, True])
    factory.supplier_payment(e, supplier_id, D(1000))

    f = expense('F', project_id, [D(1000)] * 2,
                [D(0), D(0)], [False, False])
    factory.supplier_payment(f, supplier_id, D(2000))

    g = expense('G', project_id, [D(1000)], [D(1000)], [False])
    factory.supplier_payment(g, supplier_id, D(1000))

    return ids


def mine(rows, ids):
    wanted = set(ids.values())
    return {row[ID]: row for row in rows if row[ID] in wanted}


# --- the read only query -----------------------------------------------------

def test_query_lists_exactly_the_stale_expenses(factory):
    ids = build_drift_scenario(factory)

    found = mine(run_check(factory.cur), ids)

    assert set(found) == {ids[n] for n in 'ACEFG'}


def test_query_figures_for_every_kind_of_drift(factory):
    ids = build_drift_scenario(factory)

    found = mine(run_check(factory.cur), ids)

    # (stored, real, diff, bad installments)
    expected = {
        'A': (D(2000), D(1500), D(500), 1),
        'C': (D(2000), D(0), D(2000), 2),
        'E': (D(1000), D(1000), D(0), 2),
        'F': (D(0), D(2000), D(-2000), 2),
        'G': (D(1000), D(1000), D(0), 1),
    }
    for name, values in expected.items():
        row = found[ids[name]]
        assert (row[STORED], row[REAL], row[DIFF], row[BAD]) == values, name


def test_query_names_project_and_type(factory):
    ids = build_drift_scenario(factory)

    found = mine(run_check(factory.cur), ids)

    assert found[ids['C']][TYPE] == 'cooperative'
    assert found[ids['A']][TYPE] == 'normal'
    assert found[ids['C']][PROJECT].startswith('PYTEST drift koop')
    assert found[ids['A']][TITLE] == 'PYTEST A'


def test_query_shows_overpayment_next_to_the_plan(factory):
    """Overpayment is not drift, but when an expense is listed anyway the
    uncapped valid payments are shown so the reader can tell the two apart."""
    project_id = factory.project('drift fazla')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D(1000))
    factory.expense_installment(expense_id, JAN, D(1000),
                                paid_amount=D(0), is_paid=False)
    factory.supplier_payment(expense_id, supplier_id, D(2500))

    row = mine(run_check(factory.cur), {'x': expense_id})[expense_id]

    assert row[PLAN] == D(1000)
    assert row[VALID] == D(2500)
    assert row[REAL] == D(1000)         # capped at the plan, like reconcile


def test_query_sorts_by_difference_and_ends_with_the_total(factory):
    build_drift_scenario(factory)

    rows = run_check(factory.cur)

    body, total = rows[:-1], rows[-1]
    sizes = [abs(row[DIFF]) for row in body]
    assert sizes == sorted(sizes, reverse=True)
    assert total[ID] is None
    assert total[TITLE].startswith('TOPLAM SAPMA')
    assert total[DIFF] == sum(abs(row[DIFF]) for row in body)
    assert ('(%d gider' % len(body)) in total[TITLE]


def test_query_total_is_zero_when_nothing_drifts(factory):
    """The total row must still be there, and it must say zero."""
    project_id = factory.project('drift temiz')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D(1000))
    factory.expense_installment(expense_id, JAN, D(1000))

    rows = run_check(factory.cur)

    assert expense_id not in {row[ID] for row in rows}
    assert rows[-1][TITLE].startswith('TOPLAM SAPMA')


def test_query_agrees_with_reconcile(factory):
    """The rule in the query and the rule in reconcile must be the same one.
    After reconcile has run on the stale expenses, none of them is listed."""
    ids = build_drift_scenario(factory)
    assert mine(run_check(factory.cur), ids)

    for expense_id in ids.values():
        reconcile_supplier_payments(factory.cur, expense_id)

    assert mine(run_check(factory.cur), ids) == {}


def test_query_writes_nothing(factory):
    ids = build_drift_scenario(factory)
    cur = factory.cur
    cur.execute("""SELECT COUNT(*), COALESCE(SUM(paid_amount), 0),
                          COUNT(*) FILTER (WHERE is_paid)
                   FROM expense_schedule""")
    before = cur.fetchone()

    run_check(cur)

    cur.execute("""SELECT COUNT(*), COALESCE(SUM(paid_amount), 0),
                          COUNT(*) FILTER (WHERE is_paid)
                   FROM expense_schedule""")
    assert cur.fetchone() == before
    assert ids


def test_query_text_has_no_write_statement():
    text = ' '.join(
        line for line in SQL_FILE.read_text(encoding='utf-8').splitlines()
        if not line.strip().startswith('--')).upper().strip()

    assert text.startswith('WITH')
    for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER ',
                 'TRUNCATE ', 'CREATE '):
        assert word not in text


# --- the permanent detector --------------------------------------------------

def detected(cur, ids, project_id=None):
    wanted = set(ids.values())
    return {d['expense_id']: d for d in find_schedule_drift(cur, project_id)
            if d['expense_id'] in wanted}


def test_detector_finds_the_same_expenses_as_the_query(factory):
    """The hand run query and the detector in the app must say the same."""
    ids = build_drift_scenario(factory)

    from_query = mine(run_check(factory.cur), ids)
    from_code = detected(factory.cur, ids)

    assert set(from_code) == set(from_query) == {ids[n] for n in 'ACEFG'}
    for expense_id, row in from_query.items():
        found = from_code[expense_id]
        assert (found['schedule_paid'], found['real_paid'], found['diff'],
                found['bad_installments']) == (row[STORED], row[REAL],
                                               row[DIFF], row[BAD])


def test_detector_can_look_at_one_project(factory):
    ids = build_drift_scenario(factory)
    factory.cur.execute("SELECT project_id FROM expenses WHERE id = %s",
                        (ids['C'],))
    coop_id = factory.cur.fetchone()[0]

    found = detected(factory.cur, ids, project_id=coop_id)

    assert set(found) == {ids['C']}
    assert found[ids['C']]['diff'] == D(2000)


def test_detector_returns_the_biggest_difference_first(factory):
    ids = build_drift_scenario(factory)

    found = [d for d in find_schedule_drift(factory.cur)
             if d['expense_id'] in set(ids.values())]

    sizes = [abs(d['diff']) for d in found]
    assert sizes == sorted(sizes, reverse=True)


def test_detector_writes_nothing(factory):
    build_drift_scenario(factory)
    cur = factory.cur
    sql = """SELECT COUNT(*), COALESCE(SUM(paid_amount), 0),
                    COUNT(*) FILTER (WHERE is_paid) FROM expense_schedule"""
    cur.execute(sql)
    before = cur.fetchone()

    find_schedule_drift(cur)

    cur.execute(sql)
    assert cur.fetchone() == before


# --- the expense page ----------------------------------------------------------

def card(render_context, expense_id):
    cards = render_context.latest('expenses.html')['expenses_data']
    return next(c for c in cards if c['expense_id'] == expense_id)


def open_expenses(client, factory, ids):
    factory.cur.execute("SELECT project_id FROM expenses WHERE id = %s",
                        (ids['A'],))
    project_id = factory.cur.fetchone()[0]
    factory.commit()
    response = client.get('/expenses?project_id=%d' % project_id)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_stale_cards_are_marked_and_clean_ones_are_not(client, factory,
                                                       render_context):
    ids = build_drift_scenario(factory)

    open_expenses(client, factory, ids)

    assert card(render_context, ids['A'])['drift']['diff'] == D(500)
    assert card(render_context, ids['E'])['drift']['bad_installments'] == 2
    assert card(render_context, ids['B'])['drift'] is None
    assert card(render_context, ids['D'])['drift'] is None


def test_the_mark_does_not_change_the_figures(client, factory, render_context):
    """The warning is only a mark. Card A must still show what is stored."""
    ids = build_drift_scenario(factory)

    open_expenses(client, factory, ids)

    shown = card(render_context, ids['A'])
    assert shown['total_paid'] == D(2000)            # stored, not the real 1500
    assert shown['remaining_due'] == D(3000) - D(2000)


def test_the_page_shows_the_warning_with_an_explanation(client, factory):
    ids = build_drift_scenario(factory)

    html = open_expenses(client, factory, ids)

    # Four stale cards on this project's page (A, E, F, G; C is in the
    # cooperative project). The flag sits next to two figures on each card.
    assert html.count('schedule-drift-flag') == 4 * 2
    assert 'uyuşmuyor' in html
    assert 'Rakamlar değiştirilmedi' in html


def test_a_project_without_drift_shows_no_warning(client, factory):
    project_id = factory.project('drift yok')
    supplier_id = factory.supplier(project_id)
    expense_id = factory.expense(project_id, supplier_id, amount=D(1000))
    factory.expense_installment(expense_id, JAN, D(1000), paid_amount=D(500))
    factory.supplier_payment(expense_id, supplier_id, D(500))
    factory.commit()

    response = client.get('/expenses?project_id=%d' % project_id)

    assert response.status_code == 200
    assert 'schedule-drift-flag' not in response.get_data(as_text=True)


def test_the_page_still_opens_when_the_check_fails(client, factory,
                                                   monkeypatch):
    ids = build_drift_scenario(factory)

    def broken(cur, project_id=None):
        raise RuntimeError('boom')

    monkeypatch.setattr(sys.modules['routes.expenses'], 'find_schedule_drift',
                        broken)

    html = open_expenses(client, factory, ids)

    assert 'schedule-drift-flag' not in html
    assert 'PYTEST A' in html


# --- the watch over the whole database ---------------------------------------------

# ============================================================================
# KNOWN_DRIFT: the ONLY place where existing drift is listed.
#
# These are stale schedules that are already in the TEST database. The live
# database is clean (schedule_drift_check.sql returned 0 rows there), so this
# list is not a list of live problems. It exists only because the test database
# is an older copy of the data, plus some old style sample rows.
#
#   {expense_id: (what the schedule says, what it should say)}
#     107            only in the test copy of the live data (a check handed
#                    over but never paid, schedule still shows it as paid)
#     112, 113, 114  old style sample rows of the sample cooperative / check
#                    projects (the schedule was typed in by hand)
#
# AFTER THE TEST DB IS REFRESHED FROM A LIVE BACKUP, THIS LIST MUST BE EMPTIED
# (test DB yenilendikten sonra bu liste bosaltilmali): set it to {}.
# test_known_drift_has_no_stale_entries fails until that is done.
#
# Never add an entry to make a red test green. New drift is a bug.
# ============================================================================
KNOWN_DRIFT = {
    107: (D('660000'), D('0')),
    112: (D('3000'), D('0')),
    113: (D('4000'), D('0')),
    114: (D('0'), D('1000')),
}


def real_drift(cur, own_rows=False):
    """Drift of the database. Rows named PYTEST are this suite's own (a crashed
    run can leave some behind), so they are left out unless own_rows is True."""
    return [d for d in find_schedule_drift(cur)
            if own_rows or not d['title'].startswith('PYTEST ')]


def unexpected_drift(cur, known, own_rows=False):
    """Drift found in the database that `known` does not account for.

    A row counts as unexpected when it is not in `known`, or when it is there
    with other figures (the drift changed, so something touched it again).
    """
    return [d for d in real_drift(cur, own_rows)
            if known.get(d['expense_id']) != (d['schedule_paid'], d['real_paid'])]


def describe(rows):
    return chr(10).join(
        "  expense #%d %r: schedule says %s, payments say %s (difference %s)"
        % (d['expense_id'], d['title'], d['schedule_paid'], d['real_paid'],
           d['diff'])
        for d in rows)


def test_no_new_schedule_drift_in_the_database(factory):
    """If this fails, some code (or a hand edit) left an expense schedule that
    does not match its payments. The message says which expense and by how
    much. A page that shows that expense is showing a wrong figure."""
    new = unexpected_drift(factory.cur, KNOWN_DRIFT)

    assert not new, 'New schedule drift:' + chr(10) + describe(new)


def test_known_drift_has_no_stale_entries(factory):
    """An entry that is no longer drifting must be removed. This is what makes
    the list empty itself after the test database is refreshed."""
    still_drifting = {d['expense_id'] for d in real_drift(factory.cur)}

    stale = sorted(set(KNOWN_DRIFT) - still_drifting)

    assert not stale, (
        'KNOWN_DRIFT lists expenses that no longer drift: %s. Remove them '
        '(after a test DB refresh the whole list should be {}).' % stale)


def test_the_baseline_check_is_meaningful_with_an_empty_list(factory):
    """With nothing excused, every stale schedule is reported. This is the
    state the suite will be in once the list is emptied."""
    ids = build_drift_scenario(factory)
    stale = {ids[n] for n in 'ACEFG'}

    reported = {d['expense_id']
                for d in unexpected_drift(factory.cur, {}, own_rows=True)}

    assert stale <= reported
    # Clean expenses are never reported, empty list or not.
    assert not ({ids['B'], ids['D']} & reported)


def test_the_baseline_check_excuses_only_what_is_listed(factory):
    ids = build_drift_scenario(factory)
    cur = factory.cur
    exact = {ids['A']: (D(2000), D(1500))}

    reported = {d['expense_id']
                for d in unexpected_drift(cur, exact, own_rows=True)}

    assert ids['A'] not in reported                     # listed, same figures
    assert ids['C'] in reported                         # not listed
    # Listed, but with other figures: the drift changed, so it is reported.
    changed = {ids['A']: (D(2000), D(1000))}
    assert ids['A'] in {d['expense_id']
                        for d in unexpected_drift(cur, changed, own_rows=True)}


def test_a_leftover_of_a_test_run_is_not_counted(factory):
    """Rows named PYTEST are this suite's own. They are never real drift."""
    build_drift_scenario(factory)

    assert all(not d['title'].startswith('PYTEST ')
               for d in real_drift(factory.cur))


# --- expenses without a plan ----------------------------------------------------------

PLAN_FILE = (Path(__file__).resolve().parent.parent
             / 'queries' / 'expenses_without_plan_check.sql')


def test_plan_query_lists_expenses_without_or_off_the_plan(factory):
    project_id = factory.project('plan yok')
    supplier_id = factory.supplier(project_id)
    no_plan = factory.expense(project_id, supplier_id, D(900), title='NOPLAN')
    off_plan = factory.expense(project_id, supplier_id, D(1500), title='OFF')
    factory.expense_installment(off_plan, JAN, D(1000))
    fine = factory.expense(project_id, supplier_id, D(1000), title='FINE')
    factory.expense_installment(fine, JAN, D(1000))
    factory.supplier_payment(no_plan, supplier_id, D(400), 'nakit')

    factory.cur.execute(PLAN_FILE.read_text(encoding='utf-8'))
    rows = {r[0]: r for r in factory.cur.fetchall()}

    assert fine not in rows
    # gider_id, baslik, proje_adi, proje_tipi, gider_tutari, plan_toplami,
    # fark, odeme_satiri, gecerli_odeme, eski_yontem, eski_cek_id, neden
    assert rows[no_plan][4:9] == (D(900), D(0), D(900), 1, D(400))
    assert rows[no_plan][11] == 'plan yok'
    assert rows[off_plan][4:7] == (D(1500), D(1000), D(500))
    assert rows[off_plan][11] == 'plan tutarla uyusmuyor'


def test_plan_query_text_has_no_write_statement():
    text = ' '.join(
        line for line in PLAN_FILE.read_text(encoding='utf-8').splitlines()
        if not line.strip().startswith('--')).upper().strip()

    assert text.startswith('WITH')
    for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER ',
                 'TRUNCATE ', 'CREATE '):
        assert word not in text
