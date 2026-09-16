"""composite scoring — proposal §3 + §4.

与 antibacterial/antioxidant 完全相同的可开发性加权逻辑：
  - 4 个分量：plm4cpps (cpp) / algpred2 (sens) / bepipred3 (epitope) / aggrescan_a3v (agg)
  - 权重 = Winsorized std（区分度高的升权、低的降权）
  - 4 种递送调权 + 注射下免疫阈值收紧 → composite

抗黑素方向独有的两个标记（提案 §5）：
  - `func_score_meaning='ranking_only_not_probability'`（每条肽都标）
  - `status='WIP'`（落库时把 'passed' 改成 'WIP'，在 assemble.py 里做）

aggrescan_a3v 在这里实时计算，其它 3 个分量从 select.py 已enrich的 DataFrame 列读。
"""
from __future__ import annotations

import logging

import pandas as pd

from . import DELIVERY, AntimelaninConfig, aggrescan_a3v, to_component, winsorized_std

log = logging.getLogger("antimelanin.scoring")


def add_aggrescan(df: pd.DataFrame) -> pd.DataFrame:
    """实时算一维 AGGRESCAN a3v。约 250 行 × 30aa = <100ms。"""
    log.info("computing aggrescan_a3v for %d peptides ...", len(df))
    df = df.copy()
    df["aggrescan_a3v"] = df["sequence"].astype(str).map(aggrescan_a3v).astype("float32")
    return df


def compute_components(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, float]]:
    """算 4 个分量 + 权重（与抗菌/抗氧化同套）。"""
    comps = {
        "cpp":      to_component(df["plm4cpps"],      True),    # 越高越好
        "sens":     to_component(df["algpred2"],      False),   # 越低越好 → 翻转
        "epitope":  to_component(df["bepipred3"],     False) if "bepipred3" in df.columns else pd.Series(0.5, index=df.index),
        "agg":      to_component(df["aggrescan_a3v"], False),   # 越高越促聚集 → 翻转
    }

    weights = {k: winsorized_std(v) for k, v in comps.items()}
    total = sum(weights.values())
    if total == 0:
        weights = {k: 1.0 / len(weights) for k in weights}
    else:
        weights = {k: w / total for k, w in weights.items()}

    comps_df = pd.concat(comps, axis=1)
    comps_df.columns = comps_df.columns.get_level_values(0)
    return comps_df, comps, weights


def composite(
    df: pd.DataFrame,
    comps: dict[str, pd.Series],
    weights: dict[str, float],
    delivery: str,
    cfg: AntimelaninConfig,
) -> pd.Series:
    """计算指定递送方式下每条肽的综合分。"""
    mult = DELIVERY[delivery]
    # 注射下免疫阈值收紧：两个条件任一成立则 failed_safety → 0 分
    #   1. mhcflurry ≥ 0.35（短肽 MHC-I 强结合）
    #   2. netMHCIIpan SB（任何 allele 为 Strong Binder，%Rank ≤ 2）
    if delivery == "injection":
        mhci_fail = (
            df["mhcflurry"].notna() & (df["mhcflurry"] >= cfg.mhci_threshold_injection)
            if "mhcflurry" in df.columns else pd.Series(False, index=df.index)
        )
        mhcii_fail = (
            df["netmhcipan_has_sb"].fillna(False).astype(bool)
            if "netmhcipan_has_sb" in df.columns else pd.Series(False, index=df.index)
        )
        fail_mask = mhci_fail | mhcii_fail
    else:
        fail_mask = pd.Series(False, index=df.index)

    score = pd.Series(0.0, index=df.index, dtype="float64")
    no_fail = ~fail_mask
    if no_fail.any():
        for k, w in weights.items():
            score.loc[no_fail] += w * comps[k].loc[no_fail] * mult.get(k, 1.0)
        score.loc[no_fail] *= cfg.alpha
    return score


def score_all_deliveries(
    df: pd.DataFrame,
    comps: dict[str, pd.Series],
    weights: dict[str, float],
    cfg: AntimelaninConfig,
) -> pd.DataFrame:
    """返回带 4 个 composite_<delivery> 列的 df。"""
    df = df.copy()
    for delivery in DELIVERY:
        df[f"composite_{delivery}"] = composite(df, comps, weights, delivery, cfg).astype("float32")
    return df


def func_score_meaning_series(df: pd.DataFrame, meaning: str = "ranking_only_not_probability") -> pd.Series:
    """每条肽都标 func_score 的语义（提案 §5）。"""
    return pd.Series(meaning, index=df.index, dtype="object")