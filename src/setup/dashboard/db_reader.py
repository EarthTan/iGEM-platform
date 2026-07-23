"""db_reader.py — read-only PG queries for the dashboard.

Owns its own psycopg connection. Never imports from enrich.lib to avoid
circular dependencies. DSN comes from IGEM_PG_DSN env, same fallback as enrich.
"""
from __future__ import annotations

import os
from typing import Optional

import psycopg


DEFAULT_DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)


_COVERAGE_SQL = """
SELECT tool, done_count, eligible_count
  FROM v_peptide_enrichment_coverage
 ORDER BY tool
""".strip()


_STATS_SQL = [
    "SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL",
    "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'",
    "SELECT pg_size_pretty(pg_database_size('igem_peptides'))",
]


class DbReader:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or DEFAULT_DSN

    def _connect(self):
        return psycopg.connect(self.dsn)

    def read_coverage(self) -> list[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(_COVERAGE_SQL)
                    rows = cur.fetchall()
        except Exception:
            return []

        out = []
        for tool, done, eligible in rows:
            pct = (done / eligible * 100.0) if eligible else 0.0
            if pct >= 100.0:
                status = "done"
            else:
                status = "pending"
            out.append({
                "tool": tool,
                "done": int(done),
                "eligible": int(eligible),
                "pct": round(pct, 2),
                "status": status,
            })
        return out

    def read_db_stats(self) -> Optional[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(_STATS_SQL[0])
                    total = cur.fetchone()[0]
                    cur.execute(_STATS_SQL[1])
                    active = cur.fetchone()[0]
                    cur.execute(_STATS_SQL[2])
                    size = cur.fetchone()[0]
            return {
                "active_connections": int(total),
                "active_queries": int(active),
                "db_size": str(size),
            }
        except Exception:
            return None
