"""Database connections, handed out from a small shared pool.

Opening a Postgres connection costs a network round trip and a login. Before
this file had a pool, every request paid that once or more. Now a few
connections stay open and are reused.

get_connection() keeps its old shape on purpose. The routes still call
conn.close() when they are done, and that now puts the connection back in
the pool instead of closing it. So none of the 37 places that open a
connection had to change.

Two safety nets:

* A connection that goes back to the pool is always rolled back first, so
  the next request never inherits a half finished transaction.
* Any connection a request forgot to give back is returned when the request
  ends. app.py wires release_request_connections() to Flask's
  teardown_appcontext for that.

One more thing the pool has to cope with: the database side can drop a
connection that sat idle, and the client only finds out on the next query.
A connection that has been idle for a while is therefore checked with a
cheap SELECT 1 before it is handed out, and replaced if that fails.
"""
import os
import threading
import time

import psycopg2
from psycopg2 import pool as pg_pool
from dotenv import load_dotenv

load_dotenv()

POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# A connection idle for longer than this is tested before it is reused.
IDLE_CHECK_SECONDS = 30

_pool = None
_pool_lock = threading.Lock()
_last_used = {}          # id(raw connection) -> time it went back to the pool


def _connect_params():
    return dict(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _get_pool():
    """Create the pool on first use.

    Creating it lazily matters under gunicorn: the master process forks the
    workers, and a pool opened before the fork would share its sockets with
    every worker. Opened on the first request, each worker gets its own.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    POOL_MIN, POOL_MAX, **_connect_params())
    return _pool


def _is_alive(raw):
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT 1")
        raw.rollback()
        return True
    except psycopg2.Error:
        return False


def _checkout():
    pool = _get_pool()
    for _ in range(3):
        raw = pool.getconn()
        idle = time.monotonic() - _last_used.get(id(raw), 0)
        if not raw.closed and (idle < IDLE_CHECK_SECONDS or _is_alive(raw)):
            return raw
        # Dead: drop it for good and try the next one.
        _last_used.pop(id(raw), None)
        pool.putconn(raw, close=True)
    # Three dead ones in a row means the database itself is unreachable.
    # Let the real error surface instead of looping.
    return pool.getconn()


class PooledConnection:
    """A connection from the pool that goes back to the pool on close().

    Everything else is passed straight through to the real psycopg2
    connection, so code that uses it cannot tell the difference.
    """

    def __init__(self, raw, pool):
        object.__setattr__(self, '_raw', raw)
        object.__setattr__(self, '_pool', pool)
        object.__setattr__(self, '_returned', False)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        # conn.autocommit = True and friends must reach the real connection.
        setattr(self._raw, name, value)

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, *exc):
        return self._raw.__exit__(*exc)

    @property
    def closed(self):
        """Same meaning as psycopg2's: 0 while usable."""
        return 1 if self._returned else self._raw.closed

    def close(self):
        """Give the connection back. Safe to call more than once."""
        if self._returned:
            return
        object.__setattr__(self, '_returned', True)
        raw = self._raw
        broken = bool(raw.closed)
        if not broken:
            try:
                # Never hand the next request a half finished transaction.
                raw.rollback()
            except psycopg2.Error:
                broken = True
        try:
            if broken:
                _last_used.pop(id(raw), None)
            else:
                _last_used[id(raw)] = time.monotonic()
            self._pool.putconn(raw, close=broken)
        except pg_pool.PoolError:
            # The pool was shut down underneath us; just close the socket.
            try:
                raw.close()
            except psycopg2.Error:
                pass


def _track_for_request(conn):
    """Remember the connection so the end of the request can return it."""
    try:
        from flask import g, has_app_context
    except ImportError:
        return
    if has_app_context():
        g.setdefault('_pooled_connections', []).append(conn)


def get_connection():
    conn = PooledConnection(_checkout(), _get_pool())
    _track_for_request(conn)
    return conn


def release_request_connections(exc=None):
    """Return every connection this request took and did not give back.

    For the routes that close their connection this does nothing; for the
    few that can leave one open on an error path, it stops the pool from
    running dry.
    """
    from flask import g
    for conn in g.pop('_pooled_connections', []):
        conn.close()


def close_pool():
    """Close every pooled connection. For tests and a clean shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            _last_used.clear()


def pool_stats():
    """(connections in use, connections idle in the pool). For tests."""
    if _pool is None:
        return 0, 0
    return len(_pool._used), len(_pool._pool)
