"""composite scoring — proposal §5 + §6.

与 antioxidant/antibacterial/antimelanin 的差异：

  1. **主信号改为 aip_specific**（提案 §4.2 + §6）：
     `aip_specific = aip_imfp_rank − max(other_function_rank)`
     而不是 raw aip_imfp——这样扣掉"该肽像通用功能肽"的部分，剩下
     才是抗炎特有信号。

  2. **分层 Top-K**（提案 §4.1 + §6）：
     每个长度桶各取 K，合并成 Top 150，避免长度捷径（AIP 与长度 Spearman ρ +0.53）。
     Top150 候选池内按 composite 排名（composite 用 passed 后的子集），
     但 rank 是"分层前"的全局 rank——这样长肽分布也更均匀。

  3. **注射下 MHC 收紧**（与 antibacterial 一致）：
     MHC-I ≥ 0.35 或 netMHC-II SB → 注射分置 0。

三步（与 antibacterial 同套）：
  1. 在 Top 通道上算 5 个分量：
       - aip_specific（主，rank pct 后归一化）
       - cpp / sens / epitope / agg（可开发性，与另三方向同套）
  2. 权重 = Winsorized std（区分度高的升权、低的降权）
  3. 对 4 种递送方式各算 composite = sum(w_k * comp_k * mult_k) * alpha
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import (
    DELIVERY,
    AntiinflammatoryConfig,
    aggrescan_a3v,
    length_bucket,
    to_component,
    winsorized_std,
)

log = logging.getLogger("antiinflammatory.scoring")


def add_aggrescan(df: pd.DataFrame) -> pd.DataFrame:
    """实时算一维 AGGRESCAN a3v。约 250 行 × 30aa = <100ms。"""
    log.info("computing aggrescan_a3v for %d peptides ...", len(df))
    df = df.copy()
    df["aggrescan_a3v"] = df["sequence"].astype(str).map(aggrescan_a3v).astype("float32")
    return df


def add_length_bucket(df: pd.DataFrame) -> pd.DataFrame:
    """加长度分桶列（提案 §4.1 去长度捷径）。"""
    df = df.copy()
    df["len_bucket"] = df["length"].astype(int).map(length_bucket)
    return df


def compute_components(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, float]]:
    """算 5 个分量 + 权重。

    Returns:
        comps_df: 各分量列（0-1 区间，越高越好）
        comps:    dict[name -> Series]（传给 composite()）
        weights:  dict[name -> float]（归一化到 sum=1）
    """
    # aip_specific 归一化（范围 [-1, +1]）
    if "aip_specific" in df.columns and df["aip_specific"].notna().any():
        aip_spec = to_component(df["aip_specific"], True)
    else:
        log.warning("aip_specific missing or all-NaN; use 0.5 placeholder")
        aip_spec = pd.Series(0.5, index=df.index)

    comps = {
        "aip_specific": aip_spec,                                       # 主信号（跨功能去混淆后）
        "cpp":      to_component(df["plm4cpps"],      True),            # 越高越好
        "sens":     to_component(df["algpred2"],      False),           # 越低越好 → 翻转
        "epitope":  to_component(df["bepipred3"],     False) if "bepipred3" in df.columns else pd.Series(0.5, index=df.index),
        "agg":      to_component(df["aggrescan_a3v"], False),           # 越高越促聚集 → 翻转
    }

    # 仅有 NaN 的分量给 0 区分度 → 权重自动归零
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
    cfg: AntiinflammatoryConfig,
) -> pd.Series:
    """计算指定递送方式下每条肽的综合分。"""
    mult = DELIVERY[delivery]
    # 注射下免疫阈值收紧：两个条件任一成立则综合分置 0
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
    cfg: AntiinflammatoryConfig,
) -> pd.DataFrame:
    """返回带 4 个 composite_<delivery> 列的 df。"""
    df = df.copy()
    for delivery in DELIVERY:
        df[f"composite_{delivery}"] = composite(df, comps, weights, delivery, cfg).astype("float32")
    return df


def imfp_lg_aip_confirm(df: pd.DataFrame) -> pd.Series:
    """iMFP-LG AIP 主信号辅助确认：≥ 0.5 标记 1。

    注：iMFP-LG AIP 通道提案 §0 标注有训练泄漏 + 碎片盲点，
    不应作为单点拒绝门控；仅作 ranking 上的 break tie / 加分项
    记录在 scores JSON 里。
    """
    if "aip_imfp" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int8")
    return (df["aip_imfp"].fillna(0) >= 0.5).astype("int8")
