"""export_bepipred3.py — 从 PostgreSQL 导出 BepiPred-3.0 enrichment 数据。

采样策略:
  - 全量 20.25M,采样 1M 行(mgy:non-mgy 比例保持 ~94.6:5.4)
  - 另外随机 50k 用于 KDE 细节

Outputs:
  - bepipred3_full_sample.csv.gz : 1M rows, (peptide_id, source, length, score,
                                            label, avg_ep, max_ep, max_lin_ep)
  - bepipred3_pos_sample.csv     : 50k Epitope (score >= 0.1512)
  - bepipred3_neg_sample.csv     : 50k Non-epitope
  - bepipred3_summary.json       : 全表 n/min/max/mean/std/p1/p25/p50/p75/p99
"""
from __future__ import annotations

import csv
import gzip
import json
import logging
import sys
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("export-bp3")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "plots" / "bepipred3"
OUT.mkdir(parents=True, exist_ok=True)

DB_DSN = "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026"

FULL_SAMPLE_N = 1_000_000
KDE_SAMPLE_N = 50_000


def main():
    with psycopg.connect(DB_DSN) as conn:
        # ===== 全表统计 ===== (一次拿全,用 percentile_disc 在 (tool, score) 索引上可走 index-only)
        log.info("computing full-table summary stats ...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    count(*)                                          AS n,
                    round(min(score)::numeric, 6)                     AS min_s,
                    round(max(score)::numeric, 6)                     AS max_s,
                    round(avg(score)::numeric, 6)                     AS mean_s,
                    round(stddev_pop(score)::numeric, 6)              AS std_s,
                    count(*) FILTER (WHERE label='Epitope')            AS n_epi,
                    count(*) FILTER (WHERE label='Non-epitope')        AS n_non,
                    count(*) FILTER (WHERE label='ERROR')              AS n_err
                  FROM peptide_enrichment
                 WHERE tool='bepipred3'
            """)
            r = cur.fetchone()
            n_total = r[0]
            summary = {
                "tool": "bepipred3",
                "version": "0.0.12.7 (bp3 library, GPU bf16 runner)",
                "n_total": n_total,
                "score_min": float(r[1]),
                "score_max": float(r[2]),
                "score_mean": float(r[3]),
                "score_std": float(r[4]),
                "label_epitope": r[5],
                "label_non_epitope": r[6],
                "label_error": r[7],
                "threshold": 0.1512,
                "epitope_pct": round(100.0 * r[5] / n_total, 2),
            }

            # percentile 单独走 (也能用索引扫描但不全表)
            for q, pct in [("p01", 0.01), ("p25", 0.25), ("p50", 0.50),
                           ("p75", 0.75), ("p99", 0.99)]:
                cur.execute("""
                    SELECT round((percentile_cont(%s) WITHIN GROUP (ORDER BY score))::numeric, 6)
                      FROM peptide_enrichment WHERE tool='bepipred3'
                """, (pct,))
                summary[f"score_{q}"] = float(cur.fetchone()[0])

        (OUT / "bepipred3_summary.json").write_text(json.dumps(summary, indent=2))
        log.info(f"summary: {summary}")

        # ===== 1M 随机采样 =====
        # 用 TABLESAMPLE SYSTEM 拿 ~5% 再 LIMIT(快 20倍), 走顺序扫描 + 索引(无 random 排序)
        log.info(f"sampling {FULL_SAMPLE_N:,} rows via TABLESAMPLE ...")
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT p.id, p.source, p.length,
                       e.score, e.label,
                       (e.details->>'average_epitope_score')::float    AS avg_ep,
                       (e.details->>'max_epitope_score')::float       AS max_ep,
                       (e.details->>'max_linear_epitope_score')::float AS max_lin_ep
                  FROM peptide_enrichment e TABLESAMPLE SYSTEM (5)
                  JOIN peptides p ON p.id = e.peptide_id
                 WHERE e.tool='bepipred3' AND e.score IS NOT NULL
                 LIMIT {FULL_SAMPLE_N}
            """)
            rows = cur.fetchall()
        cols = ["peptide_id", "source", "length", "score", "label",
                "avg_ep", "max_ep", "max_lin_ep"]
        with gzip.open(OUT / "bepipred3_full_sample.csv.gz", "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        log.info(f"full sample saved: {len(rows):,} rows")

        # ===== Epitope / Non-epitope 各 50k 用于 KDE =====
        log.info(f"sampling {KDE_SAMPLE_N:,} rows each for Epitope / Non-epitope ...")
        for label, suffix in [("Epitope", "pos"), ("Non-epitope", "neg")]:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT p.id, p.source, p.length,
                           e.score, e.label,
                           (e.details->>'average_epitope_score')::float    AS avg_ep,
                           (e.details->>'max_epitope_score')::float       AS max_ep,
                           (e.details->>'max_linear_epitope_score')::float AS max_lin_ep
                      FROM peptide_enrichment e TABLESAMPLE SYSTEM (5)
                      JOIN peptides p ON p.id = e.peptide_id
                     WHERE e.tool='bepipred3' AND e.label=%s
                     LIMIT {KDE_SAMPLE_N}
                """, (label,))
                rs = cur.fetchall()
            with open(OUT / f"bepipred3_{suffix}_sample.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rs)
            log.info(f"  {label}: {len(rs):,} rows -> bepipred3_{suffix}_sample.csv")

        # ===== 按长度段计算 epitope rate (全表 GROUP BY peptides.length 不需 join) =====
        # details->>'sequence_length' 与 peptides.length 一致(都是 ESM-2 编码长度, 但为了准确用 peptides.length)
        log.info("computing epitope rate by length ...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.length,
                       count(*) AS n,
                       round(avg(e.score)::numeric, 4) AS mean_s,
                       round(100.0 * count(*) FILTER (WHERE e.label='Epitope') / count(*), 2) AS epi_pct
                  FROM peptide_enrichment e TABLESAMPLE SYSTEM (5)
                  JOIN peptides p ON p.id = e.peptide_id
                 WHERE e.tool='bepipred3'
                 GROUP BY p.length
                 ORDER BY p.length
            """)
            by_len = cur.fetchall()
        with open(OUT / "bepipred3_by_length.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["length", "n", "mean_score", "epitope_pct"])
            w.writerows(by_len)
        log.info(f"by-length table saved ({len(by_len)} rows)")

        # ===== 按 source 的 epitope rate =====
        log.info("computing epitope rate by source ...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.source,
                       count(*) AS n,
                       round(avg(e.score)::numeric, 4) AS mean_s,
                       round(100.0 * count(*) FILTER (WHERE e.label='Epitope') / count(*), 2) AS epi_pct
                  FROM peptide_enrichment e TABLESAMPLE SYSTEM (5)
                  JOIN peptides p ON p.id = e.peptide_id
                 WHERE e.tool='bepipred3'
                 GROUP BY p.source
                 ORDER BY n DESC
            """)
            by_src = cur.fetchall()
        with open(OUT / "bepipred3_by_source.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["source", "n", "mean_score", "epitope_pct"])
            w.writerows(by_src)
        log.info(f"by-source table saved ({len(by_src)} rows)")


if __name__ == "__main__":
    main()