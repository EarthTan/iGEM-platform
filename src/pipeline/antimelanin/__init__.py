"""Antimelanin pipeline — implements proposal §0–§5 with the data state proven
by check_score_coverage_melanin.py on 2026-09-13:

    tipred          ~20.25M  rows, full coverage
                         **99.5% 阳性**（median 0.927, p95 0.943）
                         —— 提案 §0 已明确"已排除"，无富集力
    blsam_tip_lr    NOT IN DB（提案 §0 称"本地重建版"，未上线生产 DB）
    blsam_tip_gbm   NOT IN DB（同上）
    toxinpred3      ~20.25M  rows, full coverage
    hemopi2         ~20.25M  rows, full coverage
    mhcflurry       ~0.13M   rows (99.3% NULL → never disqualify)
    algpred2        ~20.25M  rows
    plm4cpps        ~20.25M  rows
    bepipred3       ~20.25M  rows
    netmhcipan_pctrank   NOT IN DB (column stays NULL in MV; treated as soft signal)
    netmhcipan  → 独立表 `netmhc_score` 1.475 亿行 / 132 allele / 14.8M 独立肽
    aggrescan_a3v   computed on-the-fly per peptide

对照提案 `docs/proposal/管线-抗黑素.md` §0–§5 的关键差异：
  - **TIPred 已在提案中排除**（99.5% 阳性、p95 0.943、无富集力），但 DB 里**没有**提案所
    称的 BLSAM-TIP（LR/GBM 双模型）——"本地重建版"未上线生产库，所以本管线**只能**用
    TIPred 作为唯一的弱排序信号。这是"提案预期 vs DB 现状"的核心偏差。
  - 因 TIPred 无富集力，"候选池"在功能上等同于全库；本管线**不**做功能预筛，直接
    在全库上做安全门控（可空缺性）+ tipred 全库排序取 Top/Bottom。这样产出仍可
    落库，但其**唯一价值是数据流闭环** + 演示安全/可开发性/递送权重的计算；
    真正的功能排序需等 BLSAM-TIP 重建并校准后才能做（提案 §"已知限制与升级路径"）。
  - **所有 construct 标 `status='WIP'`** + `review_status='human_review_pending'` +
    `score_meaning='ranking_only_not_probability'`，与提案 §5 "全方向标 WIP" 完全一致。

Run end-to-end with scripts/pipeline/run_antimelanin.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------- Hard safety thresholds（与抗菌/抗氧化一致） ------------------
TOX_THRESHOLD       = 0.38     # toxinpred3 < 0.38 → pass
HEMO_THRESHOLD      = 0.55     # hemopi2    < 0.55 → pass
MHC_I_THRESHOLD     = 0.50     # mhcflurry  < 0.50 → pass (99.3% NULL ⇒ soft)
MHC_I_INJECTION     = 0.35     # 注射收紧到 0.35
MHC_II_THRESHOLD    = 2.0      # netmhcipan %Rank > 2 → pass（< 2 是 SB → 不通过）

# 抗黑素：TIPred 99.5% 阳性无富集力，所以**不**做功能预筛。
# 候选池=全库（≈ 20.25M），安全门控后取 tipred 排序的 Top/Bottom。
# 提案 §1 推荐 `blsam_tip_lr >= 0.50 AND blsam_tip_gbm >= 0.50` 作为"双模型共识候选池"，
# 但这两个分数不在 DB 里——这里**退化**为「全库 → 安全门控 → tipred 排序切片」。
SKIP_FUNC_PRESCREEN = True

# 通道池大小
TOP_N               = 150
BOTTOM_N            = 100

# TIPred 双模型共识替代物（提案 §2 推荐 ≥0.5）
# DB 里只有 tipred 一个分数，所以这里用"tipred ≥ 0.5"作为**名义共识门槛**——实际是
# 全库 99.5% 都会过，所以这个门槛没有任何富集效果，只是保留 SQL 接口以备 BLSAM-TIP
# 上线后一行替换。
TIPRED_CONSENSUS_THRESHOLD = 0.5

# Delivery 调权（与抗菌/抗氧化完全相同）
DELIVERY = {
    "topical":      {"cpp": 1.4, "sol": 1.3, "thermal": 0.7, "immuno": "normal"},
    "nano":         {"cpp": 0.7, "sol": 1.4, "thermal": 1.0, "immuno": "normal"},
    "microneedle":  {"cpp": 1.0, "sol": 1.4, "thermal": 1.4, "immuno": "normal"},
    "injection":    {"cpp": 1.0, "sol": 1.0, "thermal": 1.0, "immuno": "tight"},
}

# WIP 标注（提案 §5）
REVIEW_STATUS_DEFAULT  = "human_review_pending"
SCORE_MEANING_DEFAULT  = "ranking_only_not_probability"


@dataclass(frozen=True)
class AntimelaninConfig:
    alpha: float = 1.0
    top_n: int = TOP_N
    bottom_n: int = BOTTOM_N
    tox_threshold: float = TOX_THRESHOLD
    hemo_threshold: float = HEMO_THRESHOLD
    mhci_threshold: float = MHC_I_THRESHOLD
    mhci_threshold_injection: float = MHC_I_INJECTION
    mhcii_threshold: float = MHC_II_THRESHOLD
    tipred_consensus_threshold: float = TIPRED_CONSENSUS_THRESHOLD


# ---------------- Aggrescan a3v 一维实现（与抗菌/抗氧化共用） ------------------
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


# ---------------- Winsorized std + 权重（与抗菌/抗氧化共用） ------------------
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