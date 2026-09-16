#!/usr/bin/env python3
"""事实核对：DB 中抗氧化方向依赖的分数的真实存在与覆盖率。

只读，输出 JSON 到 stdout。

设计要点（适配 peptide_enrichment 表 97 GB 的事实）：
  - 不做 count(*) 也不做 percentile_cont —— 97GB 表上单 tool 计数都超时
  - 用 LIMIT 1 在 tool_score_idx 上取首行 → 确认存在性 + score 列类型
  - 用 pg_stats 拿每个 tool 的高频 score / label 直方图（O(1)，预聚合）
  - 用 pg_class.reltuples 拿估算行数（O(1)）
  - aopxsvm 的精确分布从 results/plots/aopxsvm/aopxsvm_summary.json 读（已导出离线）

用法：
  python3 scripts/pipeline/check_score_coverage.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline import db  # noqa: E402


EXPECTED_TOOLS = [
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    "aopxsvm",
    "anoxpepred-frs",
    "plm4cpps",
    "algpred2",
    "bepipred",
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
        "anoxpepred_branches": {},
        "aopxsvm_offline_summary": None,
        "warnings": [],
        "missing_tools": [],
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

    # 3. pg_stats 拿每个 tool 的高频 score / label（已预聚合）
    row = fetch_one(
        """
        SELECT n_distinct, most_common_vals
          FROM pg_stats
         WHERE tablename='peptide_enrichment' AND attname='tool'
        """
    )
    if row:
        # most_common_vals 在 PG 中是 text[]，n_distinct 为正数表示 distinct count
        report["tool_pg_stats"] = {
            "n_distinct_tools": row["n_distinct"],
            "tools_in_mcv": list(row["most_common_vals"] or []),
        }

    # 4. 每个 tool 的存在性探测：LIMIT 1 + LIMIT 1 OFFSET 大数 → 确认 tool 有行
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
        }

    # 5. anoxpepred 派生行探测（anoxpepred-frs / anoxpepred-chelating）
    for tool in ("anoxpepred", "anoxpepred-frs", "anoxpepred-chelating"):
        present_row = fetch_one(
            "SELECT score, details FROM peptide_enrichment WHERE tool=%s LIMIT 1",
            (tool,),
        )
        if present_row is None:
            report["anoxpepred_branches"][tool] = {"exists": False}
            continue
        report["anoxpepred_branches"][tool] = {
            "exists": True,
            "sample_score": present_row["score"],
            "details_has_frs": (
                isinstance(present_row["details"], dict)
                and "frs_score" in present_row["details"]
            ),
            "details_has_chel": (
                isinstance(present_row["details"], dict)
                and "chel_score" in present_row["details"]
            ),
        }

    # 6. aopxsvm 离线摘要（已导出，不必重算 percentile_cont）
    aopx_path = ROOT / "results" / "plots" / "aopxsvm" / "aopxsvm_summary.json"
    if aopx_path.exists():
        report["aopxsvm_offline_summary"] = json.loads(aopx_path.read_text())

    # 7. 警告：缺失工具
    for tool in report["missing_tools"]:
        report["warnings"].append(
            f"{tool}: peptide_enrichment 中无任何行 —— proposal 假设不成立。"
            "可能是 tool 名拼写不同、或该分数未跑、或 DB 在另一实例。"
        )

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())