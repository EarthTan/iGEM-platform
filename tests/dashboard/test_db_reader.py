from unittest.mock import MagicMock, patch

import pytest

from src.setup.dashboard.db_reader import DbReader


def _fake_cursor(fetchall_rows=None, fetchone_values=None):
    cur = MagicMock()
    cur.__enter__ = lambda *a: cur
    cur.__exit__ = lambda *a: None
    if fetchall_rows is not None:
        cur.fetchall.return_value = fetchall_rows
    if fetchone_values is not None:
        cur.fetchone.side_effect = fetchone_values
    return cur


def _fake_conn(cur):
    conn = MagicMock()
    conn.__enter__ = lambda *a: conn
    conn.__exit__ = lambda *a: None
    conn.cursor.return_value = cur
    return conn


def test_read_coverage_happy():
    rows = [
        ("sodope", 7614185, 7614185),
        ("tipred", 8200000, 8200000),
        ("algpred2", 12671700, 20248885),
    ]
    cur = _fake_cursor(fetchall_rows=rows)
    conn = _fake_conn(cur)

    with patch("psycopg.connect", return_value=conn):
        db = DbReader("dsn_test")
        out = db.read_coverage()

    assert out[0] == {"tool": "sodope", "done": 7614185, "eligible": 7614185, "pct": 100.0, "status": "done"}
    assert out[1]["status"] == "done"
    assert out[2]["pct"] == pytest.approx(62.58, abs=0.01)
    assert out[2]["status"] == "pending"

    assert cur.execute.call_count == 1
    sql = cur.execute.call_args[0][0]
    assert "SELECT" in sql.upper()
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()


def test_read_db_stats_happy():
    cur = _fake_cursor(fetchone_values=[(12,), (1,), ("8.2 GB",)])
    conn = _fake_conn(cur)

    with patch("psycopg.connect", return_value=conn):
        db = DbReader("dsn_test")
        out = db.read_db_stats()

    assert out == {"active_connections": 12, "active_queries": 1, "db_size": "8.2 GB"}


def test_read_db_stats_returns_none_on_failure():
    import psycopg
    with patch("psycopg.connect", side_effect=psycopg.OperationalError("nope")):
        db = DbReader("dsn_test")
        assert db.read_db_stats() is None


def test_read_coverage_returns_empty_on_failure():
    import psycopg
    with patch("psycopg.connect", side_effect=psycopg.OperationalError("nope")):
        db = DbReader("dsn_test")
        assert db.read_coverage() == []
