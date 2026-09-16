"""end-to-end orchestration — implements proposal §1–§5 in a single function.

抗黑素方向的工作流（与 antibacterial/antioxidant 同样的 IO 策略，但功能预筛退化）：

  1. SQL 端在 peptide_enrichment(tipred) 上 ORDER BY score LIMIT 250
     （实测 2.1 秒；旧版 LEFT JOIN peptides 全表 461 秒）
  2. 对 Top150 + Bottom100 共约 250 个 id，在 peptide_enrichment_pk 主键上
     一次性查 7 个 tool 的 score 列（每个 tool ~1s，共 ~10s）
  3. 对 Top/Bottom 共约 250 id，在 netmhc_score 独立表上查 MHC-II（≈ 3s）
  4. 安全门控（可空缺性）+ 4 项硬门控
  5. 一维 AGGRESCAN 实时算
  6. Winsorized std 权重 + 4 种递送综合分（在 Top 通道内按 composite_topical 排名）
  7. 组装成 construct，落 constructs 表（direction='antimelanin'），
     所有 passed 一律改成 'WIP' + 记 `func_score_meaning='ranking_only_not_probability'`
"""
from __future__ import annotations

import json
import logging
import time

import pandas as pd

from . import AntimelaninConfig
from .assemble import (
    ConstructParts,
    fetch_default_parts,
    persist_constructs,
)
from .scoring import (
    add_aggrescan,
    compute_components,
    score_all_deliveries,
)
from .select import (
    apply_safety,
    enrich_top_bottom_with_netmhciipan,
    enrich_top_bottom_with_scores,
    fetch_candidate_pool_ids,
)

log = logging.getLogger("antimelanin.run")


# 候选池里已有 tipred；额外需要 enrich 的 7 个 tool
EXTRA_TOOLS = [
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",   # DB 列；如全 NULL 则走独立表
    "plm4cpps",
    "algpred2",
    "bepipred3",             # 注意是 bepipred3，不是 bepipred
]


def run(
    cfg: AntimelaninConfig | None = None,
    parts: ConstructParts | None = None,
) -> dict:
    """端到端跑一次抗黑素 pipeline，落库 constructs 表（direction='antimelanin'），
    返回汇总。所有 passed 的 construct 标 status='WIP'。"""
    if cfg is None:
        cfg = AntimelaninConfig()

    t0 = time.time()

    # ---- R1 功能预筛（提案 §1 退化版：直接 SQL 切片 tipred top/bottom） ----
    top, bottom = fetch_candidate_pool_ids(cfg)
    log.info("R1: candidate pool = full library (no functional prescreen)")

    top_ids    = top["peptide_id"].tolist()
    bottom_ids = bottom["peptide_id"].tolist()
    log.info("  top/bottom: %d + %d = %d ids", len(top_ids), len(bottom_ids), len(top_ids) + len(bottom_ids))

    # ---- R2 enrich 7 个 tool 的 score ----
    top_scores_df, bot_scores_df = enrich_top_bottom_with_scores(
        top_ids, bottom_ids, EXTRA_TOOLS,
    )
    top    = top.merge(top_scores_df, on="peptide_id", how="left")
    bottom = bottom.merge(bot_scores_df, on="peptide_id", how="left")

    # ---- R3 enrich netMHCIIpan（独立表 netmhc_score）----
    top_mhcii_df, bot_mhcii_df = enrich_top_bottom_with_netmhciipan(top_ids, bottom_ids)
    top    = top.merge(top_mhcii_df, on="peptide_id", how="left")
    bottom = bottom.merge(bot_mhcii_df, on="peptide_id", how="left")

    # ---- R4 拉 sequence（落库时需要）----
    seq_ids = list(set(top_ids) | set(bottom_ids))
    from .. import db
    with db.cursor() as cur:
        cur.execute("SELECT id, sequence, length FROM peptides WHERE id = ANY(%s)", (seq_ids,))
        seqs = pd.DataFrame(cur.fetchall(), columns=["peptide_id", "sequence", "length"])
    top    = top.merge(seqs, on="peptide_id", how="left")
    bottom = bottom.merge(seqs, on="peptide_id", how="left")

    # ---- R5 覆盖度字段 ----
    for col, key in [("hemopi2", "assessed_hemo"),
                     ("mhcflurry", "assessed_mhci"),
                     ("netmhcipan_assessed", "assessed_mhcii")]:
        if col in top.columns:
            top[key]    = top[col].notna()
            bottom[key] = bottom[col].notna()
        else:
            top[key]    = False
            bottom[key] = False

    # ---- R6 安全门控 ----
    passed_top,    failed_top    = apply_safety(top, cfg)
    passed_bottom, failed_bottom = apply_safety(bottom, cfg)
    log.info(
        "R6 safety: top %d/%d passed, bottom %d/%d passed (failed: top=%d, bottom=%d)",
        len(passed_top),    len(top),
        len(passed_bottom), len(bottom),
        len(failed_top),    len(failed_bottom),
    )

    # ---- R7 一维 aggrescan 实时算 ----
    passed_top    = add_aggrescan(passed_top)
    passed_bottom = add_aggrescan(passed_bottom)

    # ---- R8 加权综合分（在 passed_top 上算；bottom 不参与综合分）----
    if len(passed_top) > 0:
        comps_df, comps, weights = compute_components(passed_top)
        passed_top = score_all_deliveries(passed_top, comps, weights, cfg)
        # 排名（构造器真正场景驱动排序在产品里实现，这里演示用 composite_topical）
        passed_top = passed_top.sort_values(
            "composite_topical", ascending=False,
        ).reset_index(drop=True)
        passed_top["rank"] = range(1, len(passed_top) + 1)
    else:
        comps, weights = {}, {}

    # ---- R9 落库（所有 passed → status='WIP'）----
    if parts is None:
        parts = fetch_default_parts()
    n_top = persist_constructs(passed_top,    parts, "top")
    n_bot = persist_constructs(passed_bottom, parts, "bottom")

    summary = {
        "direction":            "antimelanin",
        "candidate_pool_size":  "full library (20.25M, no functional prescreen)",
        "top_n_passed":         n_top,
        "bottom_n_passed":      n_bot,
        "weights":              weights,
        "tipred_top_min":       float(passed_top["tipred"].min()) if len(passed_top) else None,
        "tipred_bot_max":       float(passed_bottom["tipred"].max()) if len(passed_bottom) else None,
        "tipred_top_median":    float(passed_top["tipred"].median()) if len(passed_top) else None,
        "tipred_bot_median":    float(passed_bottom["tipred"].median()) if len(passed_bottom) else None,
        "elapsed_seconds":      round(time.time() - t0, 1),
        "wip_note":             "All passed constructs marked status='WIP' per proposal §5: "
                                "TIPred 99.5% 阳性无富集力 + BLSAM-TIP 未上线生产 DB。",
    }
    log.info("done: %s", json.dumps(summary, ensure_ascii=False))
    return summary