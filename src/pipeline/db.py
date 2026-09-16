"""DB helpers shared by pipeline modules.

A thin wrapper over psycopg2: read DSN from IGEM_PG_DSN (fallback to the local
igem_peptides instance described in postgresql.md). Uses `SET LOCAL
statement_timeout` so long-running queries are bounded.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import pandas as pd
import psycopg2
import psycopg2.extras


DEFAULT_DSN = (
    "host=127.0.0.1 port=5432 dbname=igem_peptides "
    "user=igem password=igem_local_2026"
)

# 单查询默认 60s 超时；20M 行上的 percentile_cont 偶发会更慢，但 timeout
# 不能无限大，否则一个错误查询会让脚本挂死。
DEFAULT_STATEMENT_TIMEOUT_MS = 60_000


def get_dsn() -> str:
    return os.environ.get("IGEM_PG_DSN", DEFAULT_DSN)


@contextmanager
def connect(readonly: bool = True, statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS):
    conn = psycopg2.connect(get_dsn())
    if readonly:
        conn.set_session(readonly=True, autocommit=False)
    cur = conn.cursor()
    cur.execute(f"SET statement_timeout = {statement_timeout_ms}")
    cur.execute("SET enable_seqscan = off")     # peptide_enrichment 97GB，planner
                                                # 经常错误选择 Seq Scan
    cur.execute("SET enable_bitmapscan = off")  # 强制走 (tool, score) index scan，
                                                # 单 tool 全扫从 bitmap 几十秒降到 1-3s
    cur.close()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def cursor(name: str | None = None, statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> Iterator[psycopg2.extras.DictCursor]:
    """Yield a DictCursor; for large scans pass a `name` to use a server-side cursor."""
    with connect(statement_timeout_ms=statement_timeout_ms) as conn:
        cur = conn.cursor(name=name, cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield cur
        finally:
            cur.close()


def query_df(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """Execute a (small) SELECT and return a DataFrame. Do NOT use for 20M-row scans."""
    with connect() as conn:
        return pd.read_sql(sql, conn, params=params)


def execute(sql: str, params: tuple | dict | None = None) -> None:
    """Execute a write (DDL / DML) and commit."""
    with connect(readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()