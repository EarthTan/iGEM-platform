"""Anti-inflammatory pipeline — implements proposal §1–§7 with the data state proven
by check_score_coverage_aip.py on 2026-09-13:

实际可用工具（vs 提案 §1 假设）：

  imfp_lg_AIP      ~20.25M  rows, full coverage  ← **唯一可用的抗炎功能模型**
                                                  （提案 §0 标注：训练泄漏 + 碎片盲点）
                                                  P50 = 0.18, P90 = 0.87

  imfp_lg_ACP/ADP/AHP/AMP   ~20.25M rows, full coverage  ← 跨功能去混淆（提案 §4.2）

  aopxsvm          ~20.25M  rows, full coverage  ← 跨功能去混淆（抗氧化成分）
  amp-esm          ~20.25M  rows, full coverage  ← 跨功能去混淆（抗菌成分）
  toxinpred3       ~20.25M  rows, full coverage
  hemopi2          ~20.25M  rows, full coverage  ← 已升为硬门控
  mhcflurry        ~0.13M   rows (99.3% NULL → never disqualify)
  algpred2         ~20.25M  rows
  plm4cpps         ~20.25M  rows
  bepipred3        ~20.25M  rows（提案预期 bepipred；实际 tool 名为 bepipred3）
  netmhcipan_pctrank  NOT IN peptide_enrichment (column stays NULL)
                      but IN netmhc_score 1.475 亿行·131 allele·14.8M 肽
                      → from independent table like antibacterial v1
  aggrescan_a3v    computed on-the-fly per peptide

**关键偏差**（相对提案 §1–§4）：

  1. **`aip_v5`（L1 粗筛）和 `aip_esm2`（L2 主信号）实际不在 DB**——
     提案 §1 标为"全量（须核对）"，核对结果是 0 行。`imfp_lg_AIP` 是
     唯一在 DB 中可用的"抗炎"通道。

  2. **`blsam_tip_lr/gbm`（抗黑素跨功能去混淆）实际不在 DB**——
     与 antimelanin v1 一致。抗黑素跨功能去混淆分支降级，
     跨功能去混淆只用 iMFP-LG 五通道 + 抗氧化 + 抗菌。

  3. **`netmhcipan_pctrank` 列全 NULL，但独立表 `netmhc_score` 有 1.475 亿行**——
     与 antibacterial v1 一致，从独立表接入。

  4. **L1→L2 级联退化为单信号**——
     提案 §3 期望 L1 (`aip_v5`) 粗筛 → L2 (`aip_esm2`) 精排。L1+L2 都不在 DB。
     工程上：`imfp_lg_AIP` 既当 L1 候选池 cutoff，又当 L2 主排序。

  5. **方向标 WIP**（与 antimelanin 一致）——
     唯一可用模型 `imfp_lg_AIP` 有训练泄漏 + 碎片盲点；本产出不是
     "已筛出的抗炎肽"，而是"按 iMFP-LG AIP 弱信号 + 跨功能去混淆
     启发式排出的待湿实验验证短名单"。所有 construct 落 `status='WIP'`。

Run end-to-end with scripts/pipeline/run_antiinflammatory.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------- Hard safety thresholds (与另三方向一致) ------------------
TOX_THRESHOLD       = 0.38     # toxinpred3 < 0.38 → pass
HEMO_THRESHOLD      = 0.55     # hemopi2    < 0.55 → pass（全量，提案 §2）
MHC_I_THRESHOLD     = 0.50     # mhcflurry  < 0.50 → pass (99.3% NULL ⇒ soft)
MHC_I_INJECTION     = 0.35     # 注射收紧到 0.35
MHC_II_THRESHOLD    = 2.0      # netmhcipan %Rank 越低越严，方向相反；保留 2.0。

# 抗炎：imfp_lg_AIP 全库分位 P90 才进候选池（提案 §3 "与抗氧化/抗菌同套"）
# 实测：imfp_lg_AIP P50 = 0.1838, P90 = 0.8730；候选池 ≈ 2,024,889
FUNC_PERCENTILE     = 0.90

# 通道池大小（与另三方向一致）
TOP_N               = 150
BOTTOM_N            = 100

# iMFP-LG AIP 主信号阈值（候选池 cutoff 与 passed 落库都用 ≥ 0.5 弱确认，
# 仅用于交叉确认——iMFP-LG AIP 通道提案 §0 标注有训练泄漏，
# 不应作为单点拒绝门控）
IMFP_AIP_CONFIRM_THRESHOLD = 0.5

# 长度硬约束（提案 §2：min_len=11，无 ≤10 aa 训练样本；OOD AUC 0.498 随机）
LEN_MIN             = 11
LEN_MAX             = 30

# 长度分桶（提案 §4.1 去长度捷径：分层 Top-K）
LEN_BUCKETS         = [(11, 15), (16, 20), (21, 25), (26, 30)]

# Delivery 调权（与 antioxidant/antibacterial 完全相同）
DELIVERY = {
    "topical":      {"cpp": 1.4, "sol": 1.3, "thermal": 0.7, "immuno": "normal"},
    "nano":         {"cpp": 0.7, "sol": 1.4, "thermal": 1.0, "immuno": "normal"},
    "microneedle":  {"cpp": 1.0, "sol": 1.4, "thermal": 1.4, "immuno": "normal"},
    "injection":    {"cpp": 1.0, "sol": 1.0, "thermal": 1.0, "immuno": "tight"},
}


@dataclass(frozen=True)
class AntiinflammatoryConfig:
    alpha: float = 1.0
    p90_quantile: float = FUNC_PERCENTILE
    top_n: int = TOP_N
    bottom_n: int = BOTTOM_N
    tox_threshold: float = TOX_THRESHOLD
    hemo_threshold: float = HEMO_THRESHOLD
    mhci_threshold: float = MHC_I_THRESHOLD
    mhci_threshold_injection: float = MHC_I_INJECTION
    len_min: int = LEN_MIN
    len_max: int = LEN_MAX


# ---------------- Aggrescan a3v 一维实现（与另三方向共用） ------------------
# 一维 AGGRESCAN：每个残基 a3v 分数取自 Conchillo-Solé 2007 表，整条肽 a3v = mean。
AGGRESCAN_A3V = {
    'A': -0.448, 'R':  0.836, 'N':  0.776, 'D':  0.866, 'C': -0.359,
    'Q':  0.749, 'E':  0.851, 'G': -0.535, 'H':  0.066, 'I': -1.034,
    'L': -0.825, 'K':  0.869, 'M': -0.357, 'F': -0.655, 'P': -0.257,
    'S':  0.166, 'T':  0.122, 'W': -0.318, 'Y': -0.273, 'V': -0.804,
}


def aggrescan_a3v(seq: str) -> float:
    """Per-residue a3v mean across the sequence. Defined for AA letters."""
    if not seq:
        return 0.0
    return float(np.mean([AGGRESCAN_A3V.get(aa, 0.0) for aa in seq]))


# ---------------- Winsorized std + 权重（与另三方向共用） ------------------
def winsorized_std(s: pd.Series, lo: float = 0.05, hi: float = 0.95) -> float:
    """缩尾标准差：clip to [P5, P95]，再算 std。区分度自动决定权重。"""
    s = s.dropna()
    if len(s) < 2:
        return 0.0
    clipped = s.clip(s.quantile(lo), s.quantile(hi))
    return float(clipped.std())


def to_component(s: pd.Series, higher_better: bool) -> pd.Series:
    """归一化到 [0,1] 并按 higher_better 翻转方向。"""
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(0.5, index=s.index)
    n = (s - lo) / (hi - lo)
    return n if higher_better else 1 - n


# ---------------- 长度分桶（提案 §4.1 去长度捷径） ------------------
def length_bucket(L: int) -> str | None:
    """长度 ∈ [LEN_MIN, LEN_MAX] 的肽落到 (11-15)/(16-20)/(21-25)/(26-30)。"""
    for lo, hi in LEN_BUCKETS:
        if lo <= L <= hi:
            return f"{lo}-{hi}"
    return None
