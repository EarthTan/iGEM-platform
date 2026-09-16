"""Antibacterial pipeline — implements proposal §1–§5 with the data state proven
by check_score_coverage_amp.py on 2026-09-12:

    amp-esm         ~20.25M  rows, full coverage (AMPlify v0.1.0 原版,
                                        TF/Keras BiLSTM + Multi-Head + Context Attention)
    imfp_lg_AMP     ~20.25M  rows, full coverage (iMFP-LG 多标签分类器的 AMP 通道，
                                        用于与 amp-esm 交叉验证)
    toxinpred3      ~20.25M  rows, full coverage
    hemopi2         ~20.25M  rows, full coverage（提案 §0 误估为 500K；
                                          实际已全量，比预期更好）
    mhcflurry       ~0.13M   rows (99.3% NULL → never disqualify)
    algpred2        ~20.25M  rows
    plm4cpps        ~20.25M  rows
    bepipred3       ~20.25M  rows（提案 §0 写 bepipred；实际 tool 名为 bepipred3）
    netmhcipan_pctrank   NOT IN DB (column stays NULL in MV; treated as soft signal)
    aggrescan_a3v   computed on-the-fly per peptide

对照提案 `docs/proposal/管线-抗菌.md` §0 的偏差：
  - AMPlify 实际不是 TSV 复用，而是全 20.25M 已存在（tool 名 = amp-esm，score 即 AMPlify score）
  - iMFP-LG AMP 通道作为抗菌辅助确认（提案 §2 仅建议在 MVP 之后并行）
  - hemopi2 已全量，覆盖比提案预期高
  - bepipred3 已全量（提案预期 bepipred），覆盖比预期高
  - **netMHCIIpan 实际不是缺失**：独立表 `netmhc_score` 有 1.475 亿行预测结果（131 个 allele，
    14.8M 独立肽覆盖），只是不在 `peptide_enrichment` 长表里。本管线直接 JOIN `netmhc_score`，
    阈值以 SB（Strong Binder，%Rank ≤ 2）为门控。
  - mhcflurry 5–15 aa 长度限制仍只有 0.7% 覆盖，与提案一致

Run end-to-end with scripts/pipeline/run_antibacterial.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------- Hard safety thresholds (与 antioxidant 一致) ------------------
TOX_THRESHOLD       = 0.38     # toxinpred3 < 0.38 → pass
HEMO_THRESHOLD      = 0.55     # hemopi2    < 0.55 → pass (全量后仍保守用 0.55)
MHC_I_THRESHOLD     = 0.50     # mhcflurry  < 0.50 → pass (99.3% NULL ⇒ soft)
MHC_I_INJECTION     = 0.35     # 注射收紧到 0.35
MHC_II_THRESHOLD    = 2.0      # netmhcipan_pctrank > 2 → pass (列全 NULL ⇒ soft)
MHC_II_INJECTION    = 2.0      # NetMHCIIpan %Rank 越低越严，方向相反；保留 2.0。

# 抗菌：amp-esm 全库分位 P90 才进候选池（与抗氧化 aopxsvm 一致）
# amp-esm P50 = 0.04, P90 = 0.49；若固定 0.5 阈值仅得 1.94M 候选 (≈10%)，
# 与提案"接近全库前 10%"对齐——故改用 P90。
FUNC_PERCENTILE     = 0.90

# 通道池大小
TOP_N               = 150
BOTTOM_N            = 100

# AMPlify iMFP-LG AMP 通道辅助确认分
IMFP_AMP_CONFIRM_THRESHOLD = 0.5

# Delivery 调权（与 antioxidant 完全相同）
DELIVERY = {
    "topical":      {"cpp": 1.4, "sol": 1.3, "thermal": 0.7, "immuno": "normal"},
    "nano":         {"cpp": 0.7, "sol": 1.4, "thermal": 1.0, "immuno": "normal"},
    "microneedle":  {"cpp": 1.0, "sol": 1.4, "thermal": 1.4, "immuno": "normal"},
    "injection":    {"cpp": 1.0, "sol": 1.0, "thermal": 1.0, "immuno": "tight"},
}


@dataclass(frozen=True)
class AntibacterialConfig:
    alpha: float = 1.0
    p90_quantile: float = FUNC_PERCENTILE
    top_n: int = TOP_N
    bottom_n: int = BOTTOM_N
    tox_threshold: float = TOX_THRESHOLD
    hemo_threshold: float = HEMO_THRESHOLD
    mhci_threshold: float = MHC_I_THRESHOLD
    mhci_threshold_injection: float = MHC_I_INJECTION


# ---------------- Aggrescan a3v 一维实现（与 antioxidant 共用） ------------------
# 与 antioxidant/__init__.py 中的表同源（Conchillo-Solé 2007）。
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


# ---------------- Winsorized std + 权重（与 antioxidant 一致） ------------------
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