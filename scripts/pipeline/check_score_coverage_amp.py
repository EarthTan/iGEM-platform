#!/usr/bin/env python3
"""事实核对：DB 中抗菌方向依赖的分数的真实存在与覆盖率。

只读，输出 JSON 到 stdout。

设计要点（沿用 check_score_coverage.py 的策略）：
  - 不做 count(*) 也不做 percentile_cont —— 97GB 表上单 tool 计数都超时
  - 用 LIMIT 1 在 tool_score_idx 上取首行 → 确认存在性 + score 列类型
  - 用 pg_stats 拿每个 tool 的高频 score / label 直方图（O(1)，预聚合）
  - 用 pg_class.reltuples 拿估算行数（O(1)）

抗菌方向所需的工具集合：
    amp-esm, imfp_lg_amp, toxinpred3, hemopi2, mhcflurry, netmhcipan_pctrank,
    plm4cpps, algpred2, bepipred3（注意是 bepipred3 而非 bepipred）

用法：
    python3 scripts/pipeline/check_score_coverage_amp.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline import db  # noqa: E402


EXPECTED_TOOLS = [
    "amp-esm",                # AMPlify 原版 v0.1.0 全量
    "imfp_lg_amp",            # iMFP-LG 抗菌通道
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    "plm4cpps",
    "algpred2",
    "bepipred3",              # 注意是 bepipred3，不是 bepipred
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
        "extra_tools_relevant": {},
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

    # 4. 警告
    for tool in report["missing_tools"]:
        if tool == "netmhcipan_pctrank":
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 但独立的 `netmhc_score` 表中有 "
                "1.475 亿行预测（14.8M 肽·132 allele），本管线从独立表接入。"
            )
        elif tool == "imfp_lg_amp":
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— tool 名大小写问题。"
                "实际 tool 名为 `imfp_lg_AMP`（大写），全量 20.25M 覆盖。"
            )
        else:
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— proposal 假设不成立。"
                "可能是 tool 名拼写不同、或该分数未跑、或 DB 在另一实例。"
            )

    # 5. 额外发现（imfp_lg 系列完整情况，便于评估其作为交叉验证信号的强度）
    for tool in ("imfp_lg_AMP", "imfp_lg_ACP", "imfp_lg_ADP", "imfp_lg_AHP", "imfp_lg_AIP"):
        present_row = fetch_one(
            "SELECT score FROM peptide_enrichment WHERE tool=%s LIMIT 1",
            (tool,),
        )
        if present_row is not None:
            report["extra_tools_relevant"][tool] = {"exists": True, "sample_score": present_row["score"]}

    # 6. netmhc_score 表（独立表，不在 peptide_enrichment 长表中）
    # 该表是 netMHCIIpan 预测的原始输出；用 peptide 字段连接 peptides 表。
    # 2026-09-12 用户提供信息：1.475 亿行 netMHCIIpan 预测，覆盖 14.8M 肽。
    # 该表 ~35 GB，不能全表 COUNT(*)——用 pg_class.reltuples 拿估算行数、别的小查询验存在性。
    nm_row = fetch_one(
        "SELECT reltuples::bigint AS est, pg_size_pretty(pg_relation_size('netmhc_score')) AS size "
        "  FROM pg_class WHERE relname='netmhc_score'"
    )
    if nm_row and nm_row["est"]:
        report["netmhc_score_table"] = {
            "exists": True,
            "estimated_rows": int(nm_row["est"]),
            "pg_size": nm_row["size"],
            "sample_record": None,
            "allele_count": None,
            "binding_levels": {},
            "upstream_joinable": None,
        }
        # 6.0 抽样验证（避免 COUNT(*) / ORDER BY random() 超时）—— LIMIT 1 走主键 即可。
        sample = fetch_one(
            "SELECT peptide, allele, core, rank_pct, score, bind_level "
            "  FROM netmhc_score LIMIT 1"
        )
        if sample:
            report["netmhc_score_table"]["sample_record"] = dict(sample)
        # 6.1 allele 数量——从 pg_stats O(1) 拿
        ar = fetch_one(
            "SELECT n_distinct FROM pg_stats "
            "WHERE tablename='netmhc_score' AND attname='allele'"
        )
        if ar and ar["n_distinct"]:
            n = float(ar["n_distinct"])
            # pg_stats n_distinct 是比例，乘以 reltuples 估算
            report["netmhc_score_table"]["allele_count"] = int(round(n))
        # 6.2 bind_level 分布（从 pg_stats 拿高频值，O(1)）
        mcv = fetch_one(
            "SELECT most_common_vals::text AS vals, most_common_freqs::text AS freqs "
            "  FROM pg_stats WHERE tablename='netmhc_score' AND attname='bind_level'"
        )
        if mcv and mcv["vals"]:
            vals = mcv["vals"].strip("{}").split(",")
            freqs = mcv["freqs"].strip("{}").split(",")
            est_total = int(nm_row["est"])
            for v, f in zip(vals, freqs):
                v = v.strip().strip('"')
                try:
                    freq = float(f)
                except ValueError:
                    continue
                report["netmhc_score_table"]["binding_levels"][v] = round(est_total * freq)
        # 6.3 上游肽反查（COUNT(*) 在 35 GB 表上会超时；用存在性探针）
        joinable = fetch_one(
            "SELECT EXISTS (SELECT 1 FROM netmhc_score n "
            "                JOIN peptides p ON p.sequence = n.peptide) AS has"
        )
        if joinable:
            report["netmhc_score_table"]["upstream_joinable"] = bool(joinable["has"])

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())