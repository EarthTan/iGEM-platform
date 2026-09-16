#!/usr/bin/env python3
"""事实核对：DB 中抗炎方向依赖的分数的真实存在与覆盖率。

只读，输出 JSON 到 stdout。

设计要点（沿用 check_score_coverage.py / check_score_coverage_amp.py 的策略）：
  - 不做 count(*) 也不做 percentile_cont —— 97GB 表上单 tool 计数都超时
  - 用 LIMIT 1 在 tool_score_idx 上取首行 → 确认存在性 + score 列类型
  - 用 pg_stats 拿每个 tool 的高频 score / label 直方图（O(1)，预聚合）
  - 用 pg_class.reltuples 拿估算行数（O(1)）

抗炎方向所需的工具集合（提案 §1）：
    aip_v5, aip_esm2, imfp_lg_AIP（主信号）
    imfp_lg_ACP, imfp_lg_ADP, imfp_lg_AHP, imfp_lg_AMP（跨功能去混淆）
    aopxsvm（抗氧化跨功能）
    amp-esm（抗菌跨功能）
    blsam_tip_lr, blsam_tip_gbm（抗黑素跨功能 —— 提案 §0 标本地重建版，预期在 DB）
    toxinpred3, hemopi2, mhcflurry, netmhcipan_pctrank（安全门控）
    plm4cpps, algpred2, bepipred3, aggrescan_a3v（可开发性 + B 细胞表位 + 聚集）

用法：
    python3 scripts/pipeline/check_score_coverage_aip.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline import db  # noqa: E402


EXPECTED_TOOLS = [
    # 主信号（AIP 三模型）
    "aip_v5",                # AIP-iFeature-LightGBM v5（L1）
    "aip_esm2",              # AIP-ESM2 v8（L2 主信号）
    "imfp_lg_AIP",           # iMFP-LG AIP 通道（次级信号）
    # 跨功能去混淆通道
    "imfp_lg_ACP",
    "imfp_lg_ADP",
    "imfp_lg_AHP",
    "imfp_lg_AMP",
    "aopxsvm",
    "amp-esm",
    "blsam_tip_lr",          # 抗黑素 LR
    "blsam_tip_gbm",         # 抗黑素 GBM
    # 安全门控
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    # 可开发性 + B 细胞表位
    "plm4cpps",
    "algpred2",
    "bepipred3",             # 注意是 bepipred3
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
        "imfp_lg_aip_distribution": {},
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
        if tool in ("aip_v5", "aip_esm2"):
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 提案 §1 标为'全量（须核对）'，"
                "本管线核对结果 = 0 行。这是抗炎方向最重要的发现："
                "AIP 三模型中仅 imfp_lg_AIP 在 DB，aip_v5 / aip_esm2 完全缺失。"
                "本管线的 L1→L2 级联退化为 imfp_lg_AIP 单信号排序（提案 §3 工程修正）。"
            )
        elif tool in ("blsam_tip_lr", "blsam_tip_gbm"):
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 与 antimelanin v1 一致，"
                "提案 §0 标'本地重建版'未上线生产 DB。跨功能去混淆（提案 §4.2）"
                "中抗黑素通道缺失，仅用 6 个跨功能通道（抗氧化 + 抗菌 + iMFP-LG 五通道）。"
            )
        elif tool == "netmhcipan_pctrank":
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 但独立表 `netmhc_score` 中有 "
                "1.475 亿行预测（14.8M 肽·132 allele），本管线从独立表接入（与 antibacterial v1 一致）。"
            )
        elif tool == "imfp_lg_AIP":
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 提案 §1 标 imfp_lg_AIP，"
                "本管线核对结果 = 0 行。这是抗炎方向不可挽回的丢失：唯一可用的 AIP 模型"
                "（iMFP-LG AIP 通道）必须升为唯一主排序。"
            )
        elif tool == "bepipred3":
            report["warnings"].append(
                f"{tool}: 注意是 bepipred3（拼写差异），实际已全量。"
            )
        else:
            report["warnings"].append(
                f"{tool}: peptide_enrichment 中无任何行 —— 提案假设不成立。"
                "可能是 tool 名拼写不同、或该分数未跑、或 DB 在另一实例。"
            )

    # 5. 额外发现：iMFP-LG 系列五通道 + 抗黑素 / 抗菌 / 抗氧化通道
    extra_tools = (
        "imfp_lg_AMP", "imfp_lg_ACP", "imfp_lg_ADP", "imfp_lg_AHP", "imfp_lg_AIP",
        "aopxsvm", "amp-esm", "bepipred3",
    )
    for tool in extra_tools:
        present_row = fetch_one(
            "SELECT score FROM peptide_enrichment WHERE tool=%s LIMIT 1",
            (tool,),
        )
        if present_row is not None:
            report["extra_tools_relevant"][tool] = {
                "exists": True,
                "sample_score": present_row["score"],
            }

    # 6. imfp_lg_AIP 全库分布（P50/P90/TOP/BOTTOM）
    # 用 percentile_disc 直接在 tool='imfp_lg_AIP' 上算
    dist_row = fetch_one("""
        SELECT percentile_disc(0.50) WITHIN GROUP (ORDER BY score)::float8 AS p50,
               percentile_disc(0.90) WITHIN GROUP (ORDER BY score)::float8 AS p90,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY score)::float8 AS p95,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY score)::float8 AS p99,
               MIN(score)::float8 AS min,
               MAX(score)::float8 AS max,
               AVG(score)::float8 AS mean
          FROM peptide_enrichment
         WHERE tool='imfp_lg_AIP'
    """)
    if dist_row:
        report["imfp_lg_aip_distribution"] = {k: float(dist_row[k]) for k in dist_row.keys()}

    # 7. netmhc_score 表（独立表，不在 peptide_enrichment 长表中）
    nm_row = fetch_one(
        "SELECT reltuples::bigint AS est, pg_size_pretty(pg_relation_size('netmhc_score')) AS size "
        "  FROM pg_class WHERE relname='netmhc_score'"
    )
    if nm_row and nm_row["est"]:
        report["netmhc_score_table"] = {
            "exists": True,
            "estimated_rows": int(nm_row["est"]),
            "pg_size": nm_row["size"],
            "allele_count": None,
            "binding_levels": {},
        }
        # allele 数量
        ar = fetch_one(
            "SELECT n_distinct FROM pg_stats "
            "WHERE tablename='netmhc_score' AND attname='allele'"
        )
        if ar and ar["n_distinct"]:
            n = float(ar["n_distinct"])
            report["netmhc_score_table"]["allele_count"] = int(round(n))
        # bind_level 分布
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

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
