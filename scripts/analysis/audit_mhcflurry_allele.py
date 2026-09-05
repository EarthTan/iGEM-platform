#!/usr/bin/env python3
"""
audit_mhcflurry_allele.py
一次性审计当前 DB 里 mhcflurry 的 allele 分布,产物写入 results/plots/mhcflurry/mhcflurry_allele_audit.txt。
让以后任何人查 wiki/结果时能直接看到 "本次跑批是 single-allele 口径"。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("audit-mhcflurry-allele")

DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="results/plots/mhcflurry")
    ap.add_argument("--dsn", default=DB_DSN)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1) distinct alleles + count + share
    cur.execute(
        """
        SELECT details->>'allele' AS allele,
               count(*)         AS n,
               round(100.0 * count(*) / sum(count(*)) OVER (), 4) AS pct
          FROM peptide_enrichment
         WHERE tool = 'mhcflurry'
         GROUP BY allele
         ORDER BY n DESC
        """
    )
    rows = cur.fetchall()

    # 2) 统计汇总
    cur.execute("SELECT count(*) AS n FROM peptide_enrichment WHERE tool='mhcflurry'")
    total = cur.fetchone()["n"]

    cur.execute(
        "SELECT count(*) FILTER (WHERE details->>'allele' IS NULL) AS null_allele "
        "FROM peptide_enrichment WHERE tool='mhcflurry'"
    )
    null_allele = cur.fetchone()["null_allele"]

    cur.execute(
        "SELECT min(scored_at) AS first, max(scored_at) AS last, "
        "       count(DISTINCT date_trunc('hour', scored_at)) AS active_hours "
        "FROM peptide_enrichment WHERE tool='mhcflurry'"
    )
    ts = cur.fetchone()

    # 3) 服务端默认 allele 推断(写报告用)
    n_distinct = len(rows)
    is_single_allele = n_distinct == 1

    cur.close()
    conn.close()

    out_path = out_dir / "mhcflurry_allele_audit.txt"
    with out_path.open("w") as f:
        f.write("=" * 72 + "\n")
        f.write("MHCflurry Allele Audit — iGEM-platform peptide_enrichment\n")
        f.write("=" * 72 + "\n")
        f.write(f"generated_at_utc:    {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
        f.write(f"tool:                mhcflurry\n")
        f.write(f"total_rows:          {total:,}\n")
        f.write(f"distinct_alleles:    {n_distinct}\n")
        f.write(f"null_allele_rows:    {null_allele:,}\n")
        f.write(f"first_scored_at:     {ts['first']}\n")
        f.write(f"last_scored_at:      {ts['last']}\n")
        f.write(f"active_hours:        {ts['active_hours']}\n")
        f.write("\n")
        f.write("-" * 72 + "\n")
        f.write("Allele distribution\n")
        f.write("-" * 72 + "\n")
        f.write(f"{'rank':<6}{'allele':<18}{'count':>12}{'pct_of_total':>16}\n")
        for i, r in enumerate(rows, 1):
            f.write(f"{i:<6}{str(r['allele']):<18}{r['n']:>12,}{r['pct']:>15}%\n")
        f.write("\n")
        f.write("-" * 72 + "\n")
        f.write("Interpretation\n")
        f.write("-" * 72 + "\n")
        if is_single_allele:
            sole = rows[0]["allele"]
            f.write(
                f"⚠  HISTORICAL BASELINE (tool='mhcflurry'): this 134,888-row batch was\n"
                f"   scored against a single allele ({sole}). Score/affinity are NOT\n"
                f"   aggregated across alleles — this is the pre-2026-08-25 baseline run.\n\n"
                f"   Root cause (historical, no longer applies):\n"
                f"     - server-side:  DEFAULT_ALLELE = \"{sole}\" (still the fallback)\n"
                f"     - client-side:  MhcflurryClient did not forward any allele parameter\n"
                f"     - request schema: BatchPredictRequest.sequences[].* only carried\n"
                f"                       sequence + peptide_id (no allele passthrough)\n\n"
                f"   CURRENT CAPABILITY (since 2026-08-25, refactor commit):\n"
                f"     - server-side:  BatchPredictRequest.allele (optional) is now read by\n"
                f"                       MHCflurryService; fallback only when None.\n"
                f"     - client-side:  enrich.py --alleles 'A*02:01,A*11:01,...' iterates;\n"
                f"                       each allele is stored under derived tool name\n"
                f"                       (mhcflurry-A0201, mhcflurry-A1101, ...).\n"
                f"     - schema:       PredictRequest/BatchPredictRequest both carry\n"
                f"                       optional allele field (backward compatible).\n\n"
                f"   Immunogenicity interpretation (baseline ONLY):\n"
                f"     - The 0.39% strong / 0.69% weak binder rates reflect binding to {sole}\n"
                f"       only — the most-frequent allele in European populations (~25-50%).\n"
                f"     - For East-Asian-targeting immunogenicity assessment (PeptiCraft\n"
                f"       primary market), a multi-allele panel run is required. The numbers\n"
                f"       here are NOT a population-level immunogenicity estimate.\n"
                f"     - For multi-allele panel results, look at tool='mhcflurry-A*' rows.\n"
            )
        else:
            f.write(
                "Multi-allele run: peptides were scored against multiple alleles.\n"
                "Each row above corresponds to one (peptide_id, allele) prediction.\n"
            )
        f.write("\n")
        f.write("-" * 72 + "\n")
        f.write("Reproduction\n")
        f.write("-" * 72 + "\n")
        f.write("  PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \\\n")
        f.write("    -c \"SELECT details->>'allele' AS allele, count(*) FROM peptide_enrichment\"\n")
        f.write("       -c \"WHERE tool='mhcflurry' GROUP BY allele ORDER BY count DESC;\"\n")
        f.write("\n")

    log.info("wrote %s", out_path)
    print(out_path)


if __name__ == "__main__":
    sys.exit(main())