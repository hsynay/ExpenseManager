"""The connection pool in db.py.

Routes still call conn.close(); with the pool that means "give it back".
These tests pin down what giving back has to guarantee: the connection is
reusable, carries no leftover transaction, a broken one is thrown away, and
a request that forgets to close still returns its connection at the end.
"""
import app as app_module
import db
from db import get_connection


def in_use():
    return db.pool_stats()[0]


def test_close_gives_the_connection_back():
    get_connection().close()          # make sure the pool exists
    before = in_use()
    conn = get_connection()
    assert in_use() == before + 1

    conn.close()

    assert in_use() == before


def test_closing_twice_is_harmless():
    conn = get_connection()
    conn.close()
    conn.close()
    assert conn.closed


def test_a_returned_connection_carries_no_open_transaction():
    """Uncommitted work is rolled back before the next borrower sees it."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS pytest_pool_probe (x int)")
    cur.execute("INSERT INTO pytest_pool_probe VALUES (1)")
    conn.close()                              # no commit

    again = get_connection()
    cur = again.cursor()
    # The temp table was created inside the transaction that close() rolled
    # back, so it must be gone. (Referring to the table itself would fail
    # when it is missing, so only its name is looked up.)
    cur.execute("SELECT to_regclass('pg_temp.pytest_pool_probe') IS NULL")
    assert cur.fetchone()[0] is True
    again.close()


def test_a_broken_connection_is_thrown_away_not_reused():
    conn = get_connection()
    raw = conn._raw
    raw.close()                               # simulate a dropped socket
    conn.close()

    fresh = get_connection()
    assert fresh._raw is not raw
    cur = fresh.cursor()
    cur.execute("SELECT 1")
    assert cur.fetchone()[0] == 1
    fresh.close()


def test_the_wrapper_passes_everything_else_through():
    conn = get_connection()
    try:
        conn.autocommit = True
        assert conn._raw.autocommit is True
        conn.autocommit = False
        assert conn.encoding                  # an attribute read
        with conn.cursor() as cur:            # a method call
            cur.execute("SELECT 2")
            assert cur.fetchone()[0] == 2
    finally:
        conn.close()


def test_the_end_of_a_request_returns_a_forgotten_connection():
    """What teardown_appcontext is for: an error path that never closed."""
    get_connection().close()
    before = in_use()
    with app_module.app.app_context():
        leaked = get_connection()
        assert in_use() == before + 1
    # leaving the context ran the teardown
    assert leaked.closed
    assert in_use() == before


def test_many_requests_do_not_run_the_pool_dry(client):
    """Well past POOL_MAX requests in a row, none left holding a connection."""
    get_connection().close()
    before = in_use()
    for _ in range(db.POOL_MAX + 5):
        assert client.get('/customers').status_code == 200
    assert in_use() == before
