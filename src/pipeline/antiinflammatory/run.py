"""end-to-end orchestration — implements proposal §1–§7 in a single function.

工作流（适配 peptide_enrichment 97GB 长表 + netmhc_score 35GB 独立表的 IO 限制）：

    1. SQL 取 imfp_lg_AIP P90+ 候选池（~2M 行，秒级）
       —— 内联 length ∈ [11, 30] 硬约束（走 peptides_length_idx）
    2. 在候选池 DataFrame 上做 Top150 + Bottom100 切片（in-memory）
    3. 对 Top150 + Bottom100 共约 250 个 id，在 peptide_enrichment_pk 主键上
       一次性查 10 个 tool 的 score 列（imfp_lg_AIP 已在候选池）：
         - imfp_lg_ACP/ADP/AHP/AMP（跨功能去混淆）
         - aopxsvm / amp-esm（抗氧化/抗菌跨功能）
         - toxinpred3 / hemopi2 / mhcflurry / netmhcipan_pctrank（安全）
         - plm4cpps / algpred2 / bepipred3（可开发性 + B 细胞表位）
    4. netMHCIIpan：从独立表 `netmhc_score` 接入（与 antibacterial 一致）
    5. 计算 aip_specific = aip_imfp_rank − max(other_function_rank)（提案 §4.2）
    6. 安全门控（可空缺性 + 长度硬约束冗余）
    7. 一维 AGGRESCAN 实时算
    8. Winsorized std 权重 + 4 种递送综合分
    9. 分层 Top-K：每个长度桶各取 K（提案 §4.1）
    10. 排名（用 composite_topical）
    11. 落 constructs 表（direction='anti_inflammatory', status='WIP'）
"""
from __future__ import annotations

import json
import logging
import time

import pandas as pd

from .. import db
from . import AntiinflammatoryConfig
from .assemble import (
    ConstructParts,
    fetch_default_parts,
    persist_constructs,
)
from .scoring import (
    add_aggrescan,
    add_length_bucket,
    compute_components,
    imfp_lg_aip_confirm,
    score_all_deliveries,
)
from .select import (
    apply_safety,
    compute_aip_specific,
    enrich_top_bottom_with_netmhciipan,
    enrich_top_bottom_with_scores,
    fetch_candidate_pool_ids,
)

log = logging.getLogger("antiinflammatory.run")


# 在 250 id 上需要 enrich 的 10 个 tool（imfp_lg_AIP 已在候选池）
EXTRA_TOOLS = [
    # iMFP-LG 跨功能通道（提案 §4.2 期望）
    "imfp_lg_ACP",
    "imfp_lg_ADP",
    "imfp_lg_AHP",
    "imfp_lg_AMP",
    # 抗氧化 / 抗菌 跨功能
    "aopxsvm",
    "amp-esm",
    # 安全门控四项
    "toxinpred3",
    "hemopi2",
    "mhcflurry",
    "netmhcipan_pctrank",
    # 可开发性 + B 细胞表位
    "plm4cpps",
    "algpred2",
    "bepipred3",          # 注意：DB 中 tool 名是 bepipred3，不是 bepipred
]


def _stratified_topk(df: pd.DataFrame, total_k: int, by: str) -> pd.DataFrame:
    """分层 Top-K（提案 §4.1）：每个 len_bucket 各取 K，合并成 Top total_k。

    若 len_bucket 有 N 个桶，则 per_bucket = total_k // N，余数加到最大的桶。
    若某桶不足 K 条，则其他桶多分摊。
    """
    df = df.copy()
    buckets = sorted(df[by].dropna().unique())
    if not buckets:
        return df.sort_values("composite_topical", ascending=False).head(total_k)
    per_bucket = max(1, total_k // len(buckets))
    parts = []
    for b in buckets:
        sub = df[df[by] == b].sort_values("composite_topical", ascending=False).head(per_bucket)
        parts.append(sub)
    out = pd.concat(parts).sort_values("composite_topical", ascending=False)
    # 补足（如某桶不足 per_bucket）
    if len(out) < total_k:
        rest = df[~df.index.isin(out.index)].sort_values("composite_topical", ascending=False).head(total_k - len(out))
        out = pd.concat([out, rest]).sort_values("composite_topical", ascending=False)
    return out.head(total_k)


def run(cfg: AntiinflammatoryConfig | None = None, parts: ConstructParts | None = None) -> dict:
    """端到端跑一次抗炎 pipeline，落库 constructs 表（status='WIP'），返回汇总。"""
    if cfg is None:
        cfg = AntiinflammatoryConfig()

    t0 = time.time()
    pool = fetch_candidate_pool_ids(cfg)
    pool_sorted = pool.sort_values("aip_imfp", ascending=False).reset_index(drop=True)
    top    = pool_sorted.head(cfg.top_n).copy()
    bottom = pool_sorted.tail(cfg.bottom_n).copy()

    top_ids    = top["peptide_id"].tolist()
    bottom_ids = bottom["peptide_id"].tolist()

    # ---- 关键 IO 优化 ----
    # 对 Top150 + Bottom100 共约 250 个 peptide_id，在 peptide_enrichment_pk 主键
    # 上一次性查 12 个 tool 的 score；每个 tool ~1s，总 ~12s。
    top_scores_df, bot_scores_df = enrich_top_bottom_with_scores(
        top_ids, bottom_ids, EXTRA_TOOLS,
    )

    # 合并 aip_imfp（已在候选池 DataFrame）
    top    = top.merge(top_scores_df, on="peptide_id", how="left")
    bottom = bottom.merge(bot_scores_df, on="peptide_id", how="left")

    # ---- netMHCIIpan：独立表 netmhc_score 接入 ----
    top_mhcii_df, bot_mhcii_df = enrich_top_bottom_with_netmhciipan(top_ids, bottom_ids)
    top    = top.merge(top_mhcii_df,    on="peptide_id", how="left")
    bottom = bottom.merge(bot_mhcii_df, on="peptide_id", how="left")

    # 拉 sequence + length（构造落库时需要；length 已用于硬约束冗余检查）
    seq_map = pd.concat([top[["peptide_id"]], bottom[["peptide_id"]]]).drop_duplicates()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, sequence, length FROM peptides WHERE id = ANY(%s)",
            (seq_map["peptide_id"].tolist(),),
        )
        seqs = pd.DataFrame(cur.fetchall(), columns=["peptide_id", "sequence", "length"])
    top    = top.merge(seqs, on="peptide_id", how="left")
    bottom = bottom.merge(seqs, on="peptide_id", how="left")

    # 一维 aggrescan 实时算 + 长度分桶
    top    = add_aggrescan(top)
    bottom = add_aggrescan(bottom)
    top    = add_length_bucket(top)
    bottom = add_length_bucket(bottom)

    # 覆盖度字段
    for col, key in [("hemopi2", "assessed_hemo"),
                     ("mhcflurry", "assessed_mhci"),
                     ("netmhcipan_assessed", "assessed_mhcii")]:
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

    # iMFP-LG AIP 主信号辅助确认（不参与综合分权重）
    passed_top    = passed_top.assign(imfp_confirm=imfp_lg_aip_confirm(passed_top))
    passed_bottom = passed_bottom.assign(imfp_confirm=imfp_lg_aip_confirm(passed_bottom))

    # ---- 跨功能去混淆（提案 §4.2） ----
    passed_top    = compute_aip_specific(passed_top)
    passed_bottom = compute_aip_specific(passed_bottom)

    # 综合分（在 passed_top 上算；bottom 不参与综合分）
    comps_df, comps, weights = compute_components(passed_top)
    passed_top = score_all_deliveries(passed_top, comps, weights, cfg)

    # ---- 分层 Top-K + 排名（提案 §4.1 + §6） ----
    # 注意：passed_top 可能 < 150（被安全门控剔一些），分层 Top-K 在 passed_top 内取。
    # 若 passed_top < 150 则全部保留；rank 1..N。
    if len(passed_top) > 0:
        stratified_top = _stratified_topk(passed_top, cfg.top_n, "len_bucket")
        # 排名按 composite_topical 全局排（rank 字段全局唯一）
        passed_top_ranked = passed_top.sort_values("composite_topical", ascending=False).reset_index(drop=True)
        passed_top_ranked["rank"] = range(1, len(passed_top_ranked) + 1)
        # 落库时只持久化 stratified_top（即实际通过分层选出的 K 条）
        passed_top = passed_top_ranked.set_index("peptide_id").reindex(
            stratified_top["peptide_id"].tolist()
        ).reset_index()
    else:
        passed_top = passed_top.sort_values("composite_topical", ascending=False).reset_index(drop=True)
        passed_top["rank"] = []

    # 落库
    if parts is None:
        parts = fetch_default_parts()
    n_top = persist_constructs(passed_top,    parts, "top")
    n_bot = persist_constructs(passed_bottom, parts, "bottom")

    # 汇总
    summary = {
        "direction":             "anti_inflammatory",
        "status":                "WIP",
        "candidate_pool_size":   len(pool),
        "top_n_passed":          n_top,
        "bottom_n_passed":       n_bot,
        "weights":               weights,
        "aip_imfp_top_min":      float(passed_top["aip_imfp"].min()) if len(passed_top) else None,
        "aip_imfp_bot_max":      float(passed_bottom["aip_imfp"].max()) if len(passed_bottom) else None,
        "aip_specific_top_median": float(passed_top["aip_specific"].median()) if len(passed_top) else None,
        "imfp_confirm_top_n":    int(passed_top["imfp_confirm"].sum()) if len(passed_top) else 0,
        "len_bucket_counts_top":  passed_top["len_bucket"].value_counts().to_dict() if len(passed_top) else {},
        "elapsed_seconds":       round(time.time() - t0, 1),
    }
    log.info("done: %s", json.dumps(summary, ensure_ascii=False))
    return summary
