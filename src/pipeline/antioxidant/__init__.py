"""Antioxidant pipeline — implements proposal §1–§5 with the data state proven
by check_score_coverage.py on 2026-09-05:

    aopxsvm      ~20.25M  rows, full coverage, P90 = proposal-default cutoff
    toxinpred3   ~20.25M  rows, full coverage
    hemopi2      ~0.5M    rows (97.5% NULL → never disqualify)
    mhcflurry    ~0.13M   rows (99.3% NULL → never disqualify)
    algpred2     ~20.25M  rows
    plm4cpps     ~20.25M  rows
    anoxpepred-frs     ~20.25M rows (derived from anoxpepred.details->>'frs_score')
    anoxpepred-chel    ~20.25M rows (derived from anoxpepred.details->>'chel_score')
    netmhcipan_pctrank  NOT IN DB (column stays NULL in MV; treated as soft signal)
    bepipred            NOT IN DB (column stays NULL in MV; treated as soft signal)

Run end-to-end with scripts/pipeline/run_antioxidant.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------- Hard safety thresholds (from README §三 第五项) ------------------
TOX_THRESHOLD       = 0.38     # toxinpred3 < 0.38 → pass
HEMO_THRESHOLD      = 0.55     # hemopi2    < 0.55 → pass (97.5% NULL ⇒ soft)
MHC_I_THRESHOLD     = 0.50     # mhcflurry  < 0.50 → pass (99.3% NULL ⇒ soft)
MHC_I_INJECTION     = 0.35     # 注射收紧到 0.35
MHC_II_THRESHOLD    = 2.0      # netmhcipan_pctrank > 2 → pass (列全 NULL ⇒ soft)
MHC_II_INJECTION    = 2.0      # NetMHCIIpan %Rank 阈值本身就是越低越严，方向相反；
                                # 免疫收紧不能简单套用 0.35；保持 2.0（即 < 2 才 SB）。

# 抗氧化：aopxsvm 全库分位 P90 才进候选池
FUNC_PERCENTILE     = 0.90

# 通道池大小
TOP_N               = 150
BOTTOM_N            = 100

# AnOxPePred FRS 辅助确认分
ANOX_CONFIRM_THRESHOLD = 0.5

# Delivery 调权（README §二 + README §三 第四项）
DELIVERY = {
    "topical":      {"cpp": 1.4, "sol": 1.3, "thermal": 0.7, "immuno": "normal"},
    "nano":         {"cpp": 0.7, "sol": 1.4, "thermal": 1.0, "immuno": "normal"},
    "microneedle":  {"cpp": 1.0, "sol": 1.4, "thermal": 1.4, "immuno": "normal"},
    "injection":    {"cpp": 1.0, "sol": 1.0, "thermal": 1.0, "immuno": "tight"},
}


@dataclass(frozen=True)
class AntioxidantConfig:
    alpha: float = 1.0
    p90_quantile: float = FUNC_PERCENTILE
    top_n: int = TOP_N
    bottom_n: int = BOTTOM_N
    tox_threshold: float = TOX_THRESHOLD
    hemo_threshold: float = HEMO_THRESHOLD
    mhci_threshold: float = MHC_I_THRESHOLD
    mhci_threshold_injection: float = MHC_I_INJECTION


# ---------------- Aggrescan a3v 一维实现 ------------------
# 一维 AGGRESCAN：每个残基 a3v 分数取自 Conchillo-Solé 2007 表（按氨基酸亲疏水性
# 与静电荷），整条肽 a3v = mean(a3v_i)。该值覆盖 100%、与三维无关，可作为肽级聚集
# 倾向代理（替代需三维结构的 A3D）。

# 表来自 AGGRESCAN 论文 Fig. 2 / Table 1（已公开多年）
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


# ---------------- Winsorized std + 权重 ------------------
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