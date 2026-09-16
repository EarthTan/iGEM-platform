"""composite scoring — proposal §3 + §4.

三步：
  1. 在 Top 通道上算分量（plm4cpps / algpred2 / bepipred3 / aggrescan_a3v）：
        * 归一化到 [0,1]
        * 方向统一为"越高越好"
  2. 权重 = Winsorized std（区分度高的升权、低的降权）
  3. 对 4 种递送方式各算 composite = sum(w_k * comp_k * mult_k) * alpha

aggrescan_a3v 在这里实时计算（DB 没列），其它 3 个分量从 select.py 已enrich的
DataFrame 列直接读。

与 antioxidant 唯一区别：scoring 里增加 imfp_lg_amp 作为抗菌交叉确认信号（不进入
综合分，只作 ranking 上的 break tie / 加分项记录在 scores JSON 里）。
"""
from __future__ import annotations

import logging

import pandas as pd

from . import DELIVERY, AntibacterialConfig, aggrescan_a3v, to_component, winsorized_std

log = logging.getLogger("antibacterial.scoring")


def add_aggrescan(df: pd.DataFrame) -> pd.DataFrame:
    """实时算一维 AGGRESCAN a3v。约 250 行 × 30aa = <100ms。"""
    log.info("computing aggrescan_a3v for %d peptides ...", len(df))
    df = df.copy()
    df["aggrescan_a3v"] = df["sequence"].astype(str).map(aggrescan_a3v).astype("float32")
    return df


def compute_components(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, float]]:
    """算 4 个分量 + 权重。

    Returns:
        comps_df: 各分量列（0-1 区间，越高越好）
        comps:    dict[name -> Series]（传给 composite()）
        weights:  dict[name -> float]（归一化到 sum=1）
    """
    comps = {
        "cpp":      to_component(df["plm4cpps"],      True),    # 越高越好
        "sens":     to_component(df["algpred2"],      False),   # 越低越好 → 翻转
        "epitope":  to_component(df["bepipred3"],     False) if "bepipred3" in df.columns else pd.Series(0.5, index=df.index),
        "agg":      to_component(df["aggrescan_a3v"], False),   # 越高越促聚集 → 翻转
    }

    # 仅有 NaN 的分量给 0 区分度 → 权重自动归零
    weights = {k: winsorized_std(v) for k, v in comps.items()}
    total = sum(weights.values())
    if total == 0:
        # 全部 0 区分度（极少见：候选池里所有肽在某分量上数值相同）
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
    cfg: AntibacterialConfig,
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
    cfg: AntibacterialConfig,
) -> pd.DataFrame:
    """返回带 4 个 composite_<delivery> 列的 df。"""
    df = df.copy()
    for delivery in DELIVERY:
        df[f"composite_{delivery}"] = composite(df, comps, weights, delivery, cfg).astype("float32")
    return df


def imfp_lg_amp_confirm(df: pd.DataFrame) -> pd.Series:
    """iMFP-LG AMP 通道辅助确认：≥ 0.5 标记 1（全量覆盖，会更多肽命中）。"""
    if "imfp_lg_AMP" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int8")
    return (df["imfp_lg_AMP"].fillna(0) >= 0.5).astype("int8")