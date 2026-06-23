"""Phase 4 connection lifecycle: within a request a single pooled connection is
reused across queries and returned to the pool exactly once by teardown; outside
an app context each query borrows and returns its own. Also pins the _insert /
_execute split (lastrowid vs rowcount). A fake pool stands in for MySQL.
"""

from __future__ import annotations

import pytest

from data import db


class FakeCursor:
    def __init__(self, conn, dictionary=False):
        self.conn = conn
        self.lastrowid = 4242
        self.rowcount = 7

    def execute(self, sql, params=()):
        self.conn.queries.append(sql)

    def fetchall(self):
        return [{"ok": 1}]

    def close(self):
        pass


class FakeConn:
    def __init__(self, pool):
        self.pool = pool
        self.queries = []
        self.closed = 0

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary)

    def close(self):
        self.closed += 1
        self.pool.returned.append(self)


class FakePool:
    def __init__(self):
        self.handed_out = []
        self.returned = []

    def get_connection(self):
        conn = FakeConn(self)
        self.handed_out.append(conn)
        return conn


@pytest.fixture
def fake_pool(app, monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)
    return pool


def test_connection_is_reused_within_a_request(app, fake_pool):
    with app.test_request_context():
        db._query("SELECT 1")
        db._query("SELECT 2")
        # One connection served both queries...
        assert len(fake_pool.handed_out) == 1
        assert fake_pool.handed_out[0].queries == ["SELECT 1", "SELECT 2"]
        # ...and it is NOT returned to the pool mid-request.
        assert fake_pool.returned == []
    # Leaving the app context (teardown) returns it exactly once.
    assert len(fake_pool.returned) == 1
    assert fake_pool.handed_out[0].closed == 1


def test_connection_per_call_outside_app_context(fake_pool):
    # No app/request context (the news sidecar case): borrow-and-return each time.
    db._query("SELECT 1")
    db._query("SELECT 2")
    assert len(fake_pool.handed_out) == 2
    assert all(c.closed == 1 for c in fake_pool.handed_out)


def test_insert_returns_lastrowid_execute_returns_rowcount(app, fake_pool):
    with app.test_request_context():
        assert db._insert("INSERT ...") == 4242     # cur.lastrowid
        assert db._execute("DELETE ...") == 7        # cur.rowcount
