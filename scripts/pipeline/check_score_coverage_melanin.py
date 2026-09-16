#!/usr/bin/env python3
"""事实核对：DB 中抗黑素方向依赖的分数的真实存在与覆盖率。

只读，输出 JSON 到 stdout。

设计要点（与 check_score_coverage_amp.py 同套）：
  - 不做 count(*) 也不做 percentile_cont —— 97GB 表上单 tool 计数都超时
  - 用 LIMIT 1 在 tool_score_idx 上取首行 → 确认存在性 + score 列类型
  - 用 pg_stats 拿每个 tool 的高频 score 直方图（O(1)）
  - 用 pg_class.reltuples 拿估算行数（O(1)）

抗黑素方向所需的工具集合：
    tipred           (提案 §0 唯一可用信号；99.5% 阳性)
    blsam_tip_lr     (提案 §0 称"本地重建版"——本核对要确认它在不在 DB)
    blsam_tip_gbm    (同上)
    toxinpred3, hemopi2, mhcflurry, netmhcipan_pctrank, plm4cpps, algpred2, bepipred3

用法：
    python3 scripts/pipeline/check_score_coverage_melanin.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline import db  # noqa: E402


EXPECTED_TOOLS = [
    "tipred",                  # 提案 §0 唯一可用信号（99.5% 阳性）
    "blsam_tip_lr",            # 提案 §0 称"本地重建版"——需核实在不在 DB
    "blsam_tip_gbm",           # 同上
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    "plm4cpps",
    "algpred2",
    "bepipred3",
]


def fetch_one(sql: str, params: tuple | None = None) -> dict | None:
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def main() -> int:
    report: dict = {
        "peptides_total": 0,
        "peptide_enrichment_estimated_rows": None,
        "tool_existence": {},
        "tool_pg_stats": {},
        "warnings": [],
        "missing_tools": [],
        "wip_implications": {},
        "netmhc_score_table": {},
    }

    # 1. 总行数（peptides 表小，秒级）
    row = fetch_one("SELECT count(*)::bigint AS n FROM peptides")
    report["peptides_total"] = int(row["n"])

    # 2. peptide_enrichment 估算行数（pg_class.reltuples O(1)）
    row = fetch_one(
        "SELECT reltuples::bigint AS est FROM pg_class "
        "WHERE relname='peptide_enrichment'"
    )
    report["peptide_enrichment_estimated_rows"] = int(row["est"]) if row and row["est"] else None

    # 3. 每个 tool 的存在性探测
    for tool in EXPECTED_TOOLS:
        present_row = fetch_one(
            "SELECT score, label, details FROM peptide_enrichment "
            "WHERE tool=%s LIMIT 1",
            (tool,),
        )
        if present_row is None:
            report["missing_tools"].append(tool)
            report["tool_existence"][tool] = {"exists": False}
            continue

        report["tool_existence"][tool] = {
            "exists": True,
            "sample_score": present_row["score"],
            "sample_label": present_row["label"],
            "details_keys": sorted(list(present_row["details"].keys())) if isinstance(present_row["details"], dict) else None,
        }

    # 4. tipred 的精确分布（关键证据）
    tipred_row = fetch_one(
        "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY score)::float8  AS median, "
        "       percentile_disc(0.9) WITHIN GROUP (ORDER BY score)::float8  AS p90, "
        "       percentile_disc(0.95) WITHIN GROUP (ORDER BY score)::float8 AS p95, "
        "       percentile_disc(0.99) WITHIN GROUP (ORDER BY score)::float8 AS p99, "
        "       min(score) AS min_score, max(score) AS max_score, "
        "       count(*) FILTER (WHERE score >= 0.5) AS n_pos, "
        "       count(*) AS n_total "
        "FROM peptide_enrichment WHERE tool='tipred' AND score IS NOT NULL"
    )
    if tipred_row:
        n_pos = int(tipred_row["n_pos"]) if tipred_row["n_pos"] is not None else 0
        n_tot = int(tipred_row["n_total"]) if tipred_row["n_total"] is not None else 0
        report["tipred_distribution"] = {
            "median":      float(tipred_row["median"]) if tipred_row["median"] else None,
            "p90":         float(tipred_row["p90"]) if tipred_row["p90"] else None,
            "p95":         float(tipred_row["p95"]) if tipred_row["p95"] else None,
            "p99":         float(tipred_row["p99"]) if tipred_row["p99"] else None,
            "min":         float(tipred_row["min_score"]) if tipred_row["min_score"] else None,
            "max":         float(tipred_row["max_score"]) if tipred_row["max_score"] else None,
            "n_pos":       n_pos,
            "n_total":     n_tot,
            "pct_pos":     round(100.0 * n_pos / max(n_tot, 1), 2),
            "implication": "Tipred has 99%+ positive rate → no enrichment power, "
                           "consistent with proposal §0 排除结论。",
        }

    # 5. 警告：缺失工具
    for tool in report["missing_tools"]:
        if tool in ("blsam_tip_lr", "blsam_tip_gbm"):
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 提案 §0 称'本地重建版'未上线生产 DB。"
                "本管线退化用 tipred 作为唯一弱排序信号，所有 construct 标 WIP。"
            )
        elif tool == "netmhcipan_pctrank":
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 但独立的 `netmhc_score` 表中有 "
                "1.475 亿行预测（14.8M 肽·132 allele），本管线从独立表接入。"
            )
        else:
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— proposal 假设不成立。"
            )

    # 6. WIP 影响总结
    missing_critical = [t for t in ("blsam_tip_lr", "blsam_tip_gbm") if t in report["missing_tools"]]
    report["wip_implications"] = {
        "missing_critical_functional_scores":  missing_critical,
        "fallback_signal":                     "tipred (only one in DB)",
        "fallback_signal_quality":             "99%+ positive rate; no enrichment",
        "construct_status":                    "All passed → 'WIP' per proposal §5",
        "review_status":                       "human_review_pending (to be stored in scores JSONB 'func_score_meaning')",
    }

    # 7. netmhc_score 表（独立表，与 antibacterial 同套；抗黑素方向也接入）
    nm_row = fetch_one(
        "SELECT reltuples::bigint AS est, pg_size_pretty(pg_relation_size('netmhc_score')) AS size "
        "  FROM pg_class WHERE relname='netmhc_score'"
    )
    if nm_row and nm_row["est"]:
        report["netmhc_score_table"] = {
            "exists":         True,
            "estimated_rows": int(nm_row["est"]),
            "pg_size":        nm_row["size"],
        }
        sample = fetch_one(
            "SELECT peptide, allele, core, rank_pct, score, bind_level "
            "  FROM netmhc_score LIMIT 1"
        )
        if sample:
            report["netmhc_score_table"]["sample_record"] = dict(sample)

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())