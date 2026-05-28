"""
方差感知权重引擎 — stages4 可变权重机制。

两层权重:
  1. SD 驱动: w_base_i = σ_i / Σσ_j（数据驱动基础权重）
  2. 手动调节: w_final_i = w_base_i × α_i，再归一化（领域知识介入）

用法:
    from main.stages4.s4_analytics import compute_variance_weights

    weights = compute_variance_weights(
        db, table="round3_scores",
        score_columns=["bepipred3_score", "temstapro_score", ...],
        stage_name="round3",
        manual_coefficients={"anoxpepred_score": 1.3, "temstapro_score": 1.0},
    )

基于 stages3/analytics.py 重构，新增手动系数 α 支持。
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any


# ────────────────────────────────────────────────────────────────
# 统计工具
# ────────────────────────────────────────────────────────────────


def winsorized_stddev(values: list[float], tails: float = 0.01) -> float:
    """Winsorized 标准差：截尾上下 tails 极端值后再计算。"""
    if len(values) < 10:
        return statistics_stddev(values)

    n = len(values)
    sorted_vals = sorted(values)
    lower_idx = max(0, int(n * tails))
    upper_idx = min(n - 1, int(n * (1 - tails)) - 1)
    lower_bound = sorted_vals[lower_idx]
    upper_bound = sorted_vals[upper_idx]

    clipped = [max(lower_bound, min(v, upper_bound)) for v in values]
    return statistics_stddev(clipped)


def statistics_stddev(values: list[float]) -> float:
    """总体标准差。"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def percentile(values: list[float], p: float) -> float:
    """百分位数（线性插值）。"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (p / 100.0) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def compute_distribution(values: list[float]) -> dict[str, float]:
    """分数向量的完整分布统计。"""
    if not values:
        return {}
    sv = sorted(values)
    n = len(sv)
    return {
        "count": n,
        "mean": sum(values) / n,
        "stddev": statistics_stddev(values),
        "min": sv[0],
        "p01": percentile(values, 1),
        "p05": percentile(values, 5),
        "p25": percentile(values, 25),
        "p50": percentile(values, 50),
        "p75": percentile(values, 75),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": sv[-1],
        "winsorized_stddev": winsorized_stddev(values),
    }


# ────────────────────────────────────────────────────────────────
# 核心：主权重计算
# ────────────────────────────────────────────────────────────────


def compute_variance_weights(
    db,
    table: str,
    score_columns: list[str],
    stage_name: str,
    id_column: str = "candidate_id",
    excluded_prefixes: tuple[str, ...] = ("toxinpred3", "hemopi2", "algpred2", "mhcflurry"),
    manual_coefficients: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    计算方差感知权重，支持手动调节系数。

    Args:
        db: PipelineDB 实例
        table: 评分表名
        score_columns: 参与权重计算的分数列名
        stage_name: 阶段名 (如 "round3", "round7")
        id_column: 主键列名
        excluded_prefixes: 不参与权重计算的列前缀（硬过滤条件）
        manual_coefficients: 手动调节系数 {col_name: α}，默认 α=1.0

    Returns:
        {
            "sd_weights": {"bepipred3_score": 0.31, ...},
            "manual_coefficients": {"bepipred3_score": 1.0, ...},
            "final_weights": {"bepipred3_score": 0.31, ...},
            "distribution": {...},
            "total_candidates": 12345,
            "formula": "winsorized_stddev_normalized",
            "adjustment_reason": "...",
        }
    """
    conn = db.connect()

    # 筛选参与权重计算的列
    weight_columns = [
        c for c in score_columns
        if not any(c.startswith(prefix) for prefix in excluded_prefixes)
        and c.endswith("_score")
    ]

    if not weight_columns:
        raise ValueError("没有可用的评分列用于权重计算")

    # 1. 从数据库读取所有分数
    total_candidates = 0
    distributions: dict[str, dict] = {}
    raw_values: dict[str, list[float]] = {}

    for col in weight_columns:
        rows = conn.execute(f"""
            SELECT {col} FROM {table}
            WHERE {col} IS NOT NULL
        """).fetchall()
        values = [float(r[0]) for r in rows if r[0] is not None]
        if not values:
            print(f"  [analytics] {col}: 无有效数据，跳过")
            continue
        raw_values[col] = values
        total_candidates = max(total_candidates, len(values))
        distributions[col] = compute_distribution(values)

    if not raw_values:
        raise ValueError("所有评分列均无有效数据")

    # 2. SD 驱动基础权重
    stddevs = {
        col: distributions[col].get("winsorized_stddev", 0)
        for col in raw_values
    }

    total_std = sum(stddevs.values())
    if total_std == 0:
        raise ValueError("所有服务标准差均为 0，无法计算差异化权重")

    sd_weights = {
        col: std / total_std
        for col, std in stddevs.items()
    }

    # 3. 手动调节
    mc = manual_coefficients or {}
    final_weights_raw = {}
    for col in sd_weights:
        alpha = mc.get(col, 1.0)
        if alpha < 0:
            raise ValueError(f"手动系数不能为负数: {col}={alpha}")
        final_weights_raw[col] = sd_weights[col] * alpha

    # 再归一化
    total_final = sum(final_weights_raw.values())
    if total_final == 0:
        raise ValueError("手动调节后所有权重为 0")
    final_weights = {
        col: w / total_final
        for col, w in final_weights_raw.items()
    }

    # 4. 写入 score_distribution 表
    for col in raw_values:
        dist = distributions[col]
        conn.execute("""
            INSERT INTO score_distribution
                (stage_name, service_name, count, mean, stddev,
                 min, p01, p05, p25, p50, p75, p95, p99, max,
                 winsorized_stddev, sd_weight, manual_coefficient, final_weight)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            stage_name, col, dist["count"], dist["mean"], dist["stddev"],
            dist["min"], dist["p01"], dist["p05"], dist["p25"],
            dist["p50"], dist["p75"], dist["p95"], dist["p99"],
            dist["max"], dist["winsorized_stddev"],
            sd_weights.get(col, 0.0),
            mc.get(col, 1.0),
            final_weights.get(col, 0.0),
        ])

    # 5. 写入 weight_config 表
    reason = mc.get("_reason", "默认配置，无手动调节")
    conn.execute("""
        INSERT INTO weight_config
            (stage_name, total_candidates, weight_formula,
             sd_weights, manual_coefficients, final_weights,
             adjustment_reason, distribution_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        stage_name, total_candidates, "winsorized_stddev_normalized",
        json.dumps(sd_weights),
        json.dumps({k: v for k, v in mc.items() if not k.startswith("_")}),
        json.dumps(final_weights),
        reason,
        json.dumps({col: distributions[col] for col in raw_values}, default=str),
    ])

    # 6. 打印报告
    print(f"\n  [analytics] === {stage_name} 可变权重 ===")
    print(f"  [analytics] 参与候选: {total_candidates:,}")
    print(f"  [analytics] 公式: winsorized_stddev_normalized + manual_coefficient")
    for col in sorted(final_weights, key=final_weights.get, reverse=True):
        sd = sd_weights[col]
        fw = final_weights[col]
        alpha = mc.get(col, 1.0)
        bar = "█" * max(1, int(fw * 100))
        info = f"α={alpha}" if alpha != 1.0 else ""
        print(f"  [analytics]   {col}: {fw:.4f} (σ={sd:.4f}) {bar} {info}")

    return {
        "sd_weights": sd_weights,
        "manual_coefficients": {k: v for k, v in mc.items() if not k.startswith("_")},
        "final_weights": final_weights,
        "distribution": distributions,
        "total_candidates": total_candidates,
        "formula": "winsorized_stddev_normalized",
        "adjustment_reason": reason,
    }


# ────────────────────────────────────────────────────────────────
# 应用权重并排名
# ────────────────────────────────────────────────────────────────


def apply_weights_and_rank(
    db,
    table: str,
    score_columns: list[str],
    weights: dict[str, float],
    stage_name: str,
    rank_table: str | None = None,
    id_column: str = "candidate_id",
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """
    应用权重计算综合得分并排名。

    Args:
        db: PipelineDB 实例
        table: 评分表名
        score_columns: 所有分数列名
        weights: {col_name: weight} — compute_variance_weights 返回的 final_weights
        stage_name: 阶段名
        rank_table: 可选，排名写入目标表
        id_column: 主键列名
        top_n: 可选，只返回 Top N

    Returns:
        [(candidate_id, composite_score, rank), ...]
    """
    conn = db.connect()

    weight_exprs = []
    for col, w in weights.items():
        if w > 0 and col in score_columns:
            weight_exprs.append(f"COALESCE({col}, 0) * {w}")

    if not weight_exprs:
        raise ValueError("没有有效的加权表达式")

    composite_expr = " + ".join(weight_exprs)
    limit_clause = f"LIMIT {top_n}" if top_n else ""

    rows = conn.execute(f"""
        SELECT {id_column}, {composite_expr} AS composite_score
        FROM {table}
        WHERE {' AND '.join(f'{c} IS NOT NULL' for c in weights if weights[c] > 0)}
        ORDER BY composite_score DESC
        {limit_clause}
    """).fetchall()

    results = []
    for rank, (cid, score) in enumerate(rows, start=1):
        score_val = float(score) if score is not None else 0.0
        results.append({
            id_column: int(cid),
            "composite_score": round(score_val, 6),
            "rank": rank,
        })

    if rank_table:
        conn.execute(f"DELETE FROM {rank_table} WHERE 1=1")
        conn.executemany(f"""
            INSERT INTO {rank_table} ({id_column}, composite_score, rank)
            VALUES (?, ?, ?)
        """, [
            (r[id_column], r["composite_score"], r["rank"])
            for r in results
        ])

    print(f"  [analytics] {stage_name} 排名: {len(results):,} 候选")
    if results:
        print(f"  [analytics]   第 1 名: ID={results[0][id_column]}, "
              f"score={results[0]['composite_score']:.4f}")
        print(f"  [analytics]   第 {len(results)} 名: ID={results[-1][id_column]}, "
              f"score={results[-1]['composite_score']:.4f}")

    return results
