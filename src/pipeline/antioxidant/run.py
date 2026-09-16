"""end-to-end orchestration — implements proposal §1–§5 in a single function.

工作流（适配 peptide_enrichment 97GB 长表的 IO 限制）：
    1. SQL 取 aopxsvm P90+ 候选池 id + aopxsvm（~2M 行，秒级）
    2. 在 DataFrame 上做 Top150 + Bottom100 切片（in-memory，毫秒）
    3. 对 Top150 + Bottom100 共约 250 个 id，在 peptide_enrichment_pk 主键上
       一次性查 9 个 tool 的 score 列（每个 tool ~1s，共 ~10s）
    4. 安全门控（可空缺性）+ AnOxPePred FRS 辅助确认
    5. Winsorized std 权重 + 4 种递送综合分
    6. 组装成 construct，落 constructs 表

注意：此设计有意避开"在 20M 全表上做 9 个相关子查询"，那种方案 PG planner
# 无法去相关，O(N) 30+ 分钟。
"""
from __future__ import annotations

import json
import logging
import time

import pandas as pd

from . import AntioxidantConfig
from .assemble import (
    ConstructParts,
    fetch_default_parts,
    persist_constructs,
)
from .scoring import (
    add_aggrescan,
    anoxpepred_confirm,
    compute_components,
    score_all_deliveries,
)
from .select import (
    apply_safety,
    enrich_top_bottom_with_scores,
    fetch_candidate_pool_ids,
    select_top_bottom,
)

log = logging.getLogger("antioxidant.run")


EXTRA_TOOLS = [
    "anoxpepred-frs",
    "anoxpepred-chelating",
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    "plm4cpps",
    "algpred2",
    "bepipred",
]


def run(cfg: AntioxidantConfig | None = None, parts: ConstructParts | None = None) -> dict:
    """端到端跑一次抗氧化 pipeline，落库 constructs 表，返回汇总。"""
    if cfg is None:
        cfg = AntioxidantConfig()

    t0 = time.time()
    pool = fetch_candidate_pool_ids(cfg)
    pool_sorted = pool.sort_values("aopxsvm", ascending=False).reset_index(drop=True)
    top    = pool_sorted.head(cfg.top_n).copy()
    bottom = pool_sorted.tail(cfg.bottom_n).copy()

    top_ids    = top["peptide_id"].tolist()
    bottom_ids = bottom["peptide_id"].tolist()

    # ---- 关键 IO 优化 ----
    # 对 Top150 + Bottom100 共约 250 个 peptide_id，在 peptide_enrichment_pk 主键
    # 上一次性查 9 个 tool 的 score；每个 tool ~1s，总 ~10s。
    top_scores_df, bot_scores_df = enrich_top_bottom_with_scores(
        top_ids, bottom_ids, EXTRA_TOOLS,
    )

    # 合并 aopxsvm
    top    = top.merge(top_scores_df, on="peptide_id", how="left")
    bottom = bottom.merge(bot_scores_df, on="peptide_id", how="left")

    # 拉 sequence（构造落库时需要）
    seq_map = pd.concat([top[["peptide_id"]], bottom[["peptide_id"]]]).drop_duplicates()
    from .. import db
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, sequence FROM peptides WHERE id = ANY(%s)",
            (seq_map["peptide_id"].tolist(),),
        )
        seqs = pd.DataFrame(cur.fetchall(), columns=["peptide_id", "sequence"])
    top    = top.merge(seqs, on="peptide_id", how="left")
    bottom = bottom.merge(seqs, on="peptide_id", how="left")

    # 一维 aggrescan 实时算
    top    = add_aggrescan(top)
    bottom = add_aggrescan(bottom)

    # 覆盖度字段
    for col, key in [("hemopi2", "assessed_hemo"),
                     ("mhcflurry", "assessed_mhci"),
                     ("netmhcipan_pctrank", "assessed_mhcii")]:
        if col in top.columns:
            top[key]    = top[col].notna()
            bottom[key] = bottom[col].notna()
        else:
            top[key]    = False
            bottom[key] = False

    # 安全门控
    passed_top,    failed_top    = apply_safety(top, cfg)
    passed_bottom, failed_bottom = apply_safety(bottom, cfg)
    log.info(
        "safety: top %d/%d passed, bottom %d/%d passed (failed: top=%d, bottom=%d)",
        len(passed_top),    len(top),
        len(passed_bottom), len(bottom),
        len(failed_top),    len(failed_bottom),
    )

    # AnOxPePred FRS 辅助确认
    passed_top    = passed_top.assign(anox_confirm=anoxpepred_confirm(passed_top))
    passed_bottom = passed_bottom.assign(anox_confirm=anoxpepred_confirm(passed_bottom))

    # 综合分（在 passed_top 上算；bottom 不参与综合分）
    comps_df, comps, weights = compute_components(passed_top)
    passed_top = score_all_deliveries(passed_top, comps, weights, cfg)

    # 排名（构造器真正场景驱动排序在产品里实现，这里演示用 composite_topical）
    passed_top    = passed_top.sort_values("composite_topical", ascending=False).reset_index(drop=True)
    passed_top["rank"] = range(1, len(passed_top) + 1)

    # 落库
    if parts is None:
        parts = fetch_default_parts()
    n_top = persist_constructs(passed_top,    parts, "top")
    n_bot = persist_constructs(passed_bottom, parts, "bottom")

    summary = {
        "candidate_pool_size":  len(pool),
        "top_n_passed":         n_top,
        "bottom_n_passed":      n_bot,
        "weights":              weights,
        "aopxsvm_top_min":      float(passed_top["aopxsvm"].min()) if len(passed_top) else None,
        "aopxsvm_bot_max":      float(passed_bottom["aopxsvm"].max()) if len(passed_bottom) else None,
        "elapsed_seconds":      round(time.time() - t0, 1),
    }
    log.info("done: %s", json.dumps(summary, ensure_ascii=False))
    return summary