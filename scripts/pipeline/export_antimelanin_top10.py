#!/usr/bin/env python3
"""导出抗黑素方向 Top 10 + Bottom 10 (对照组) 到 CSV。

与抗菌/抗氧化的差异：
  - 按 composite_topical 取 Top（功能已在 §1 退化处理；rank 列已经在
    constructs 表里由 pipeline 写入）
  - 含 func_score_meaning 列（WIP 方向特有的语义标记）
  - 含 tipred 作为功能分（抗菌用 amp_esm、抗氧化用 aopxsvm）

用法：
    python3 scripts/pipeline/export_antimelanin_top10.py
输出：
    results/pipeline/antimelanin_v1/top10.csv
    results/pipeline/antimelanin_v1/bottom10.csv
    results/pipeline/antimelanin_v1/top10_bottom10.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline import db  # noqa: E402


def main() -> int:
    out_dir = ROOT / "results" / "pipeline" / "antimelanin_v1"
    out_dir.mkdir(parents=True, exist_ok=True)

    queries = [
        ("top10.csv",
         "WHERE c.direction = 'antimelanin' AND c.channel = 'top' AND c.rank <= 10\n"
         "ORDER BY c.rank",
         False),
        ("bottom10.csv",
         "WHERE c.direction = 'antimelanin' AND c.channel = 'bottom'\n"
         "  AND c.peptide_id IN (\n"
         "    SELECT peptide_id FROM constructs\n"
         "     WHERE direction = 'antimelanin' AND channel = 'bottom' AND status = 'WIP'\n"
         "     ORDER BY (scores->>'tipred')::float8 ASC LIMIT 10)\n"
         "ORDER BY (c.scores->>'tipred')::float8 ASC",
         True),
        ("top10_bottom10.csv",
         "WHERE c.direction = 'antimelanin'\n"
         "  AND ( (c.channel = 'top'    AND c.rank <= 10)\n"
         "     OR (c.channel = 'bottom' AND c.peptide_id IN (\n"
         "           SELECT peptide_id FROM constructs\n"
         "            WHERE direction = 'antimelanin' AND channel = 'bottom' AND status = 'WIP'\n"
         "            ORDER BY (scores->>'tipred')::float8 ASC LIMIT 10) ) )\n"
         "ORDER BY c.channel DESC, c.rank NULLS LAST, (c.scores->>'tipred')::float8 ASC",
         True),
    ]

    cols_select = (
        "c.id, c.channel, c.status, c.rank, c.peptide_id, p.sequence, p.length, p.source,\n"
        "       (c.scores->>'tipred')::float8                              AS tipred,\n"
        "       (c.scores->>'func_score_meaning')::text                   AS func_score_meaning,\n"
        "       (c.scores->>'toxinpred3')::float8                         AS toxinpred3,\n"
        "       (c.scores->>'hemopi2')::float8                            AS hemopi2,\n"
        "       (c.scores->>'mhcflurry')::float8                          AS mhcflurry,\n"
        "       (c.scores->>'netmhcipan_min_rank_pct')::float8            AS netmhcipan_min_rank_pct,\n"
        "       (c.scores->>'netmhcipan_min_sb_rank_pct')::float8         AS netmhcipan_min_sb_rank_pct,\n"
        "       (c.scores->>'netmhcipan_has_sb')::bool                    AS netmhcipan_has_sb,\n"
        "       (c.scores->>'netmhcipan_n_alleles')::int                  AS netmhcipan_n_alleles,\n"
        "       (c.scores->>'plm4cpps')::float8                           AS plm4cpps,\n"
        "       (c.scores->>'algpred2')::float8                           AS algpred2,\n"
        "       (c.scores->>'bepipred3')::float8                          AS bepipred3,\n"
        "       (c.scores->>'aggrescan_a3v')::float8                      AS aggrescan_a3v,\n"
        "       (c.delivery_scores->>'topical')::float8                   AS composite_topical,\n"
        "       (c.delivery_scores->>'nano')::float8                     AS composite_nano,\n"
        "       (c.delivery_scores->>'microneedle')::float8              AS composite_microneedle,\n"
        "       (c.delivery_scores->>'injection')::float8                AS composite_injection,\n"
        "       c.assessed_hemo, c.assessed_mhci, c.assessed_mhcii"
    )

    for filename, where, want_split in queries:
        out_path = out_dir / filename
        sql = f"SELECT {cols_select}\n  FROM constructs c JOIN peptides p ON p.id = c.peptide_id\n  {where}"
        with db.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]

        with out_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in rows:
                w.writerow([row[c] for c in cols])

        if want_split:
            n_top = sum(1 for r in rows if r.get("channel") == "top")
            n_bot = sum(1 for r in rows if r.get("channel") == "bottom")
            print(f"wrote {len(rows)} rows ({n_top} top + {n_bot} bottom) to {out_path}")
        else:
            print(f"wrote {len(rows)} rows to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())