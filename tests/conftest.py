"""Shared test fixtures.

Two rules hold everywhere in this folder:

1. Tests only ever run against the test database. A guard checks for a
   marker project before anything else and stops the whole run if it is
   missing, so a wrong .env cannot touch live data.
2. A test never edits rows it did not create. The factory below inserts its
   own rows, remembers their ids and deletes exactly those at the end. The
   existing sample projects (46, 47, 48, 32) are left alone.
"""
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
from app import get_connection  # noqa: E402

# A project that only exists in the test database.
TEST_DB_MARKER = '*** TEST VERITABANI ***'

# Every row this suite creates carries this prefix in its name, so leftovers
# from a crashed run are easy to spot.
PREFIX = 'PYTEST '

TODAY = date(2026, 6, 15)


@pytest.fixture(scope='session', autouse=True)
def guard_test_database():
    """Stop the run unless we are pointed at the test database."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM projects WHERE name = %s", (TEST_DB_MARKER,))
    found = cur.fetchone()[0]
    cur.close()
    conn.close()
    if not found:
        pytest.exit(
            "Refusing to run: the marker project was not found, so this does "
            "not look like the test database.", returncode=2)


class Factory:
    """Creates rows for one test and deletes them again afterwards."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self._created = []          # (table, pk_column, value) in creation order
        # Routes write rows we never asked for. Remember where audit_logs
        # stood before the test so cleanup can drop only the rows this test
        # caused, and nothing older.
        self.cur.execute("SELECT COALESCE(MAX(id), 0) FROM audit_logs")
        self._audit_baseline = self.cur.fetchone()[0]
        self._tokens = []

    # --- low level ---------------------------------------------------
    def _insert(self, table, values, pk='id'):
        cols = list(values.keys())
        sql = "INSERT INTO {t} ({c}) VALUES ({p}) RETURNING {pk}".format(
            t=table, c=", ".join(cols),
            p=", ".join(["%s"] * len(cols)), pk=pk)
        self.cur.execute(sql, tuple(values[c] for c in cols))
        new_id = self.cur.fetchone()[0]
        self._created.append((table, pk, new_id))
        return new_id

    def commit(self):
        """Make the rows visible to the application's own connection."""
        self.conn.commit()

    def new_token(self):
        """A fresh submission token, removed again at the end of the test."""
        value = str(uuid.uuid4())
        self._tokens.append(value)
        return value

    def cleanup(self):
        # A test that failed halfway leaves the transaction aborted, and every
        # statement after that would fail too. Rolling back first drops the
        # rows that were never committed and makes the connection usable for
        # deleting the ones that were.
        self.conn.rollback()
        for table, pk, value in reversed(self._created):
            self.cur.execute(
                "DELETE FROM {t} WHERE {pk} = %s".format(t=table, pk=pk),
                (value,))
        for value in self._tokens:
            self.cur.execute(
                "DELETE FROM submission_tokens WHERE token = %s", (value,))
        self.cur.execute("DELETE FROM audit_logs WHERE id > %s",
                         (self._audit_baseline,))
        self.conn.commit()
        self._created = []
        self._tokens = []

    # --- income side -------------------------------------------------
    def project(self, name='project', project_type='normal'):
        return self._insert('projects', {
            'name': PREFIX + name, 'project_type': project_type})

    def customer(self, first='Test', last='Musteri'):
        return self._insert('customers', {
            'first_name': PREFIX + first, 'last_name': last})

    def flat(self, project_id, owner_id=None, total_price=Decimal('100000'),
             flat_no=1, floor=1, block_name='A'):
        return self._insert('flats', {
            'project_id': project_id, 'owner_id': owner_id,
            'flat_no': flat_no, 'floor': floor, 'block_name': block_name,
            'total_price': total_price})

    def installment(self, flat_id, due_date, amount):
        return self._insert('installment_schedule', {
            'flat_id': flat_id, 'due_date': due_date, 'amount': amount})

    def check(self, customer_id, amount, status='portfoyde',
              due_date=None, issue_date=None):
        return self._insert('checks', {
            'customer_id': customer_id, 'amount': amount, 'status': status,
            'issue_date': issue_date or TODAY,
            'due_date': due_date or (TODAY + timedelta(days=30)),
            'bank_name': PREFIX + 'Banka', 'check_number': PREFIX + '1'})

    def payment(self, flat_id, amount, payment_method='nakit',
                check_id=None, payment_date=None, description='odeme'):
        return self._insert('payments', {
            'flat_id': flat_id, 'amount': amount,
            'payment_method': payment_method, 'check_id': check_id,
            'payment_date': payment_date or TODAY,
            'description': PREFIX + description})

    # --- expense side ------------------------------------------------
    def supplier(self, project_id=None, name='Tedarikci'):
        # suppliers.name is unique across the whole table, so a fixed name
        # breaks the second supplier in a test, or two test runs at once.
        return self._insert('suppliers', {
            'name': '%s%s %s' % (PREFIX, name, uuid.uuid4().hex[:8]),
            'project_id': project_id})

    def expense(self, project_id, supplier_id=None, amount=Decimal('10000'),
                title='gider', expense_date=None):
        return self._insert('expenses', {
            'project_id': project_id, 'supplier_id': supplier_id,
            'title': PREFIX + title, 'amount': amount,
            'expense_date': expense_date or TODAY})

    def expense_installment(self, expense_id, due_date, amount):
        return self._insert('expense_schedule', {
            'expense_id': expense_id, 'due_date': due_date, 'amount': amount})

    def outgoing_check(self, supplier_id, amount, status='verildi',
                       due_date=None, issue_date=None):
        return self._insert('outgoing_checks', {
            'supplier_id': supplier_id, 'amount': amount, 'status': status,
            'issue_date': issue_date or TODAY,
            'due_date': due_date or (TODAY + timedelta(days=30)),
            'bank_name': PREFIX + 'Banka', 'check_number': PREFIX + '2'})

    def supplier_payment(self, expense_id, supplier_id, amount,
                         payment_method='nakit', check_id=None,
                         payment_date=None):
        return self._insert('supplier_payments', {
            'expense_id': expense_id, 'supplier_id': supplier_id,
            'amount': amount, 'payment_method': payment_method,
            'check_id': check_id, 'payment_date': payment_date or TODAY,
            'description': PREFIX + 'odeme'})

    def petty_cash(self, project_id, amount, expense_date=None,
                   title='kucuk gider'):
        return self._insert('petty_cash_expenses', {
            'project_id': project_id, 'title': PREFIX + title,
            'amount': amount, 'expense_date': expense_date or TODAY})

    # --- readers -----------------------------------------------------
    def installment_state(self, flat_id):
        """Return [(due_date, amount, is_paid, paid_amount)] by due date."""
        self.cur.execute(
            "SELECT due_date, amount, is_paid, paid_amount "
            "FROM installment_schedule WHERE flat_id = %s "
            "ORDER BY due_date, id", (flat_id,))
        return self.cur.fetchall()

    def expense_state(self, expense_id):
        self.cur.execute(
            "SELECT due_date, amount, is_paid, paid_amount "
            "FROM expense_schedule WHERE expense_id = %s "
            "ORDER BY due_date, id", (expense_id,))
        return self.cur.fetchall()

    def paid_amounts(self, flat_id):
        return [row[3] for row in self.installment_state(flat_id)]

    def paid_flags(self, flat_id):
        return [row[2] for row in self.installment_state(flat_id)]

    def expense_paid_amounts(self, expense_id):
        return [row[3] for row in self.expense_state(expense_id)]

    def expense_paid_flags(self, expense_id):
        return [row[2] for row in self.expense_state(expense_id)]


@pytest.fixture
def factory():
    conn = get_connection()
    f = Factory(conn)
    try:
        yield f
    finally:
        f.cleanup()
        f.cur.close()
        conn.close()


@pytest.fixture
def cur(factory):
    """The factory's cursor, for calling helpers that take one."""
    return factory.cur


@pytest.fixture(scope='session')
def user_id():
    """An existing user id, needed by routes that write an audit log."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM users LIMIT 1")
    row = c.fetchone()
    c.close()
    conn.close()
    return str(row[0]) if row else str(uuid.uuid4())


class RenderCapture(dict):
    """The values the last rendered template received, by template name."""

    def latest(self, template):
        return self[template][-1]


@pytest.fixture
def render_context(monkeypatch):
    """Record what each page hands to its template.

    Checking the numbers here is steadier than reading them back out of the
    finished HTML, and it says plainly which value is wrong when a test fails.
    """
    store = RenderCapture()
    original = app_module.render_template

    def recording(template_name, *args, **kwargs):
        store.setdefault(template_name, []).append(kwargs)
        return original(template_name, *args, **kwargs)

    monkeypatch.setattr(app_module, 'render_template', recording)
    return store


@pytest.fixture
def client(user_id):
    """A Flask test client that is already logged in. CSRF is off.

    TESTING stays off on purpose. With it on, Flask re-raises errors instead
    of returning a page, and we want to see the same 500 the user would see.
    """
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    app_module.app.config['TESTING'] = False
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['user_name'] = 'pytest'
        yield c
