"""db.py — psycopg 封装,fetch_remaining / upsert_results / coverage."""
from __future__ import annotations

import os

import psycopg


DB_DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)


TOOL_LENGTH_RANGE: dict[str, tuple[int, int]] = {
    "toxinpred3": (1, 30),
    "tipred":     (1, 30),
    "algpred2":   (1, 30),
    "sodope":     (1, 30),
    "anoxpepred": (1, 30),  # legacy 命名, 2026-08-23 起改用 frs/chelating 两个 tool
    "anoxpepred-frs": (1, 30),
    "anoxpepred-chelating": (1, 30),
    "hemopi2":    (1, 40),
    "plm4cpps":   (1, 30),
    # temstapro: 上游服务在全长蛋白训练,未在 3-30aa 短肽校准;此处的 (1,30) 仅表示
    # 我们喂入的序列长度区间,与工具域内有效性无关。短肽上信号可信度见
    # results/plots/temstapro_score_definition.md 与评估报告 §3。
    "temstapro":  (1, 30),
    "mhcflurry":  (5, 15),
    "amp-esm":    (1, 30),
    "bepipred3":  (1, 30),  # 2026-08-24: ESM-2 + 5-model DenseNet ensemble,1-30aa 短肽
}


class DB:
    def __init__(self, dsn: str = DB_DSN):
        self.dsn = dsn
        self.conn: psycopg.Connection | None = None

    def __enter__(self):
        self.conn = psycopg.connect(self.dsn)
        return self

    def __exit__(self, *a):
        if self.conn and not self.conn.closed:
            self.conn.close()

    # --- 拉取待处理 ---------------------------------------------------------

    def fetch_remaining(
        self,
        tool: str,
        batch: int,
        after_id: int = 0,
        done_set: set | None = None,
    ) -> list[tuple[int, str]]:
        """返回 [(peptide_id, sequence), ...],已排除跑过此 tool 的。

        带 done_set 时走快路径(从 peptides ASC 取 buffer,在内存里 skip done 的),
        避免每次跟 peptide_enrichment 13M 行 anti-join。
        """
        if tool not in TOOL_LENGTH_RANGE:
            raise ValueError(f"unknown tool: {tool}")
        lo, hi = TOOL_LENGTH_RANGE[tool]
        if done_set is not None:
            pull = max(batch * 5, 2000)
            sql = """
                SELECT id, sequence FROM peptides
                 WHERE length BETWEEN %s AND %s
                   AND id > %s
                 ORDER BY id
                 LIMIT %s
            """
            with self.conn.cursor() as cur:
                cur.execute(sql, (lo, hi, after_id, pull))
                rows = cur.fetchall()
            out = []
            for pid, seq in rows:
                if pid in done_set:
                    continue
                out.append((pid, seq))
                if len(out) >= batch:
                    break
            return out
        # 旧路径(无 done_set,慢)
        sql = """
            SELECT p.id, p.sequence
              FROM peptides p
              LEFT JOIN peptide_enrichment e
                     ON e.peptide_id = p.id AND e.tool = %s
             WHERE e.peptide_id IS NULL
               AND p.length BETWEEN %s AND %s
               AND p.id > %s
             ORDER BY p.id
             LIMIT %s
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (tool, lo, hi, after_id, batch))
            return [(r[0], r[1]) for r in cur.fetchall()]

    def load_done_set(self, tool: str) -> set[int]:
        """一次性加载该 tool 已 done 的 peptide_id 到内存 set。"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT peptide_id FROM peptide_enrichment WHERE tool = %s", (tool,))
            return {r[0] for r in cur.fetchall()}

    # --- 写入结果 -----------------------------------------------------------

    def upsert_results(self, tool: str, rows: list[tuple]) -> int:
        if not rows:
            return 0
        sql = """
            INSERT INTO peptide_enrichment (peptide_id, tool, score, label, details, scored_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (peptide_id, tool) DO UPDATE
              SET score     = EXCLUDED.score,
                  label     = EXCLUDED.label,
                  details   = EXCLUDED.details,
                  scored_at = now()
        """
        with self.conn.cursor() as cur:
            cur.executemany(sql, [(r[0], tool, r[1], r[2], r[3]) for r in rows])
        self.conn.commit()
        return len(rows)

    # --- 观测 -------------------------------------------------------------

    def coverage(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT tool, done_count, eligible_count, coverage_pct "
                "FROM v_peptide_enrichment_coverage"
            )
            return [
                {"tool": r[0], "done": r[1], "eligible": r[2], "pct": float(r[3])}
                for r in cur.fetchall()
            ]

    def max_peptide_id(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id), 0) FROM peptides")
            return cur.fetchone()[0]

    def total_count_for_tool(self, tool: str) -> int:
        lo, hi = TOOL_LENGTH_RANGE[tool]
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM peptides WHERE length BETWEEN %s AND %s", (lo, hi))
            return cur.fetchone()[0]