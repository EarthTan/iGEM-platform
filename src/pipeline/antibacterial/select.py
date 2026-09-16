"""candidate pool selection — proposal §1.

Workflow:
    1. SQL 取全库 amp-esm P90 cutoff → 候选池 DataFrame（~2M 行）
    2. 取候选池在 amp-esm 上的 Top N + Bottom M（默认 150 + 100）
    3. 对 Top + Bottom 共约 250 id，在 peptide_enrichment_pk 主键上一次性查其他 8 个 tool 的 score

Why not a wide table:
    peptide_enrichment is 97 GB; 9 correlated subqueries × 20M rows OOM the planner
    (实测 ~30 min linear, confirmed in build_peptide_scores_table.py timing logs).
    复用 antioxidant/select.py 的 IO 策略：候选池 ≤ 2M 行 → Top/Bottom 250 id × 8 tools。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd

from .. import db
from . import AntibacterialConfig

log = logging.getLogger("antibacterial.select")

# 候选池 SQL：proposal §1 期望的逻辑，但写在 peptide_enrichment 长表上。
# amp-esm 即 AMPlify 原版 v0.1.0 的全量分数（implementation: TF/Keras BiLSTM +
# Multi-Head + Context Attention），与提案 §0/§2 描述一致。
CANDIDATE_POOL_SQL = """
WITH p90 AS (
    SELECT percentile_disc(%(p90)s) WITHIN GROUP (ORDER BY score)::float8 AS cutoff
      FROM peptide_enrichment
     WHERE tool = 'amp-esm' AND score IS NOT NULL
)
SELECT
    e.peptide_id,
    e.score                            AS amp_esm,
    e.label                            AS amp_esm_label
  FROM peptide_enrichment e, p90
 WHERE e.tool = 'amp-esm'
   AND e.score >= p90.cutoff
ORDER BY e.score DESC
"""


def fetch_candidate_pool_ids(cfg: AntibacterialConfig) -> pd.DataFrame:
    """候选池：amp-esm 全库 P90 以上的肽 id + amp-esm 分。"""
    log.info("fetching candidate pool (amp-esm >= P%.0f) ...", cfg.p90_quantile * 100)
    t0 = time.time()
    with db.cursor(name="cur_pool", statement_timeout_ms=10 * 60_000) as cur:
        cur.itersize = 200_000
        cur.execute(CANDIDATE_POOL_SQL, {"p90": cfg.p90_quantile})
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["peptide_id", "amp_esm", "amp_esm_label"])
    df["peptide_id"] = df["peptide_id"].astype("int64")
    df["amp_esm"] = df["amp_esm"].astype("float32")
    log.info("  pool: %s peptides in %.1fs", f"{len(df):,}", time.time() - t0)
    return df


def enrich_top_bottom_with_scores(
    top_ids: list[int],
    bottom_ids: list[int],
    extra_tools: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """对 Top + Bottom 共约 250 个肽 id 一次性拉所有 tool 的 score。

    250 id × 8 tools × 0.2ms ≈ 0.5s 全部完成（沿用 antioxidant 的策略）。
    """
    ids = list(top_ids) + list(bottom_ids)
    all_data: list[dict] = []
    for tool in extra_tools:
        sql = (
            f"SELECT peptide_id, score FROM peptide_enrichment "
            f"WHERE tool = '{tool}' AND peptide_id = ANY(%s)"
        )
        log.info("  fetching tool=%s for %d peptides ...", tool, len(ids))
        t0 = time.time()
        with db.cursor(name=f"cur_topbot_{tool}") as cur:
            cur.execute(sql, (ids,))
            for row in cur.fetchall():
                all_data.append({
                    "peptide_id": row["peptide_id"],
                    "tool": tool,
                    "score": row["score"],
                })
        log.info("    done in %.1fs", time.time() - t0)
    if not all_data:
        empty = pd.DataFrame(columns=["peptide_id"] + extra_tools)
        return (empty, empty)
    long_df = pd.DataFrame(all_data)
    wide = long_df.pivot(index="peptide_id", columns="tool", values="score")
    top_df = wide.reindex(top_ids).reset_index()
    bot_df = wide.reindex(bottom_ids).reset_index()
    return top_df, bot_df


def enrich_top_bottom_with_netmhciipan(
    top_ids: list[int],
    bottom_ids: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """从独立表 `netmhc_score` 拉 Top + Bottom 的 MHC-II 评估。

    该表是 netMHCIIpan 预测的原始输出，肽 × allele 粒度，1.475 亿行、131 个 allele、
    14.8M 独立肽（与提案 §0 预期一致）。

    这里对每个肽聚合：
      - min_rank_pct：所有 allele 中最小的 %Rank，越低越强结合
      - min_sb_rank_pct：SB（Strong Binder，%Rank ≤ 2）中最小的 %Rank；NULL 表示无 SB
      - has_sb / has_wb：是否至少有一个 allele 为 SB / WB
      - n_alleles：有预测的 allele 数
      - assessed：是否完全找到（peptide 在 10-30 aa 且有预测结果）

    Returns: (top_df, bot_df)，列名与 `enrich_top_bottom_with_scores` 对齐。
    """
    ids = list(top_ids) + list(bottom_ids)
    if not ids:
        empty = pd.DataFrame(columns=[
            "peptide_id", "netmhcipan_min_rank_pct", "netmhcipan_min_sb_rank_pct",
            "netmhcipan_has_sb", "netmhcipan_has_wb", "netmhcipan_n_alleles",
            "netmhcipan_assessed",
        ])
        return (empty, empty)

    log.info("  fetching netmhc_score for %d peptides ...", len(ids))
    t0 = time.time()
    # 先拉 sequence（用 peptides 主键 index，毫秒）
    seq_sql = "SELECT id, sequence, length FROM peptides WHERE id = ANY(%s)"
    with db.cursor() as cur:
        cur.execute(seq_sql, (ids,))
        seqs = pd.DataFrame(cur.fetchall(), columns=["peptide_id", "sequence", "length"])
    # 再用 sequence 数组在 netmhc_score 上查（peptide 上有 idx_netmhc_score_peptide 索引）
    seq_list = seqs["sequence"].tolist()
    agg_sql = """
        SELECT peptide,
               MIN(rank_pct)                                        AS min_rank_pct,
               MIN(rank_pct) FILTER (WHERE bind_level = 'SB')      AS min_sb_rank_pct,
               BOOL_OR(bind_level = 'SB')                          AS has_sb,
               BOOL_OR(bind_level = 'WB')                          AS has_wb,
               COUNT(*)                                            AS n_alleles
          FROM netmhc_score
         WHERE peptide = ANY(%s)
         GROUP BY peptide
    """
    with db.cursor() as cur:
        cur.execute(agg_sql, (seq_list,))
        rows = cur.fetchall()
    agg_df = pd.DataFrame(rows, columns=[
        "sequence", "min_rank_pct", "min_sb_rank_pct",
        "has_sb", "has_wb", "n_alleles",
    ])
    log.info("    netmhc_score done in %.1fs (matched %d/%d peptides)",
             time.time() - t0, len(agg_df), len(seqs))

    # merge 回 sequence
    out = seqs.merge(agg_df, on="sequence", how="left")
    # 计算 assessed：有 netmhc_score 行 → True
    out["netmhcipan_assessed"] = out["min_rank_pct"].notna()
    # 重命名为预期列名
    out = out.rename(columns={
        "min_rank_pct": "netmhcipan_min_rank_pct",
        "min_sb_rank_pct": "netmhcipan_min_sb_rank_pct",
        "has_sb": "netmhcipan_has_sb",
        "has_wb": "netmhcipan_has_wb",
        "n_alleles": "netmhcipan_n_alleles",
    })
    cols = ["peptide_id", "netmhcipan_min_rank_pct", "netmhcipan_min_sb_rank_pct",
            "netmhcipan_has_sb", "netmhcipan_has_wb", "netmhcipan_n_alleles",
            "netmhcipan_assessed"]
    out = out[cols]
    top_df = out.set_index("peptide_id").reindex(top_ids).reset_index()
    bot_df = out.set_index("peptide_id").reindex(bottom_ids).reset_index()
    return top_df, bot_df


def select_top_bottom(df: pd.DataFrame, cfg: AntibacterialConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """在候选池内取 Top N（功能分最高）和 Bottom M（功能分最低）。"""
    sorted_df = df.sort_values("amp_esm", ascending=False).reset_index(drop=True)
    top = sorted_df.head(cfg.top_n).copy()
    bot = sorted_df.tail(cfg.bottom_n).copy()
    return top, bot


# 安全门控：可空缺性原则（proposal §1 + README §三 第五项，与 antioxidant 同套）
def apply_safety(
    df: pd.DataFrame,
    cfg: AntibacterialConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (passed_df, failed_df)。

    规则：
      - toxinpred3 全覆盖：score IS NULL → 默认 pass（DB 全覆盖，不会发生）
      - hemopi2 / mhcflurry / netmhcipan_pctrank 列大部分为 NULL → NULL 默认 pass
      - 有分且超过阈值 → failed_safety
    """
    df = df.copy()
    if "toxinpred3" in df.columns:
        df["_fail_tox"] = df["toxinpred3"].notna() & (df["toxinpred3"] >= cfg.tox_threshold)
    else:
        df["_fail_tox"] = False
    if "hemopi2" in df.columns:
        df["_fail_hemo"] = df["hemopi2"].notna() & (df["hemopi2"] >= cfg.hemo_threshold)
    else:
        df["_fail_hemo"] = False
    if "mhcflurry" in df.columns:
        df["_fail_mhci"] = df["mhcflurry"].notna() & (df["mhcflurry"] >= cfg.mhci_threshold)
    else:
        df["_fail_mhci"] = False
    # netMHCIIpan：从独立表 `netmhc_score` 聚合得到的 `netmhcipan_min_sb_rank_pct`
    # 与 `netmhcipan_has_sb` 列；SB = Strong Binder（%Rank ≤ 2）。
    # 交付实中 DB 1.475 亿行·131 个 allele，所以有 SB 才判 failed。
    if "netmhcipan_has_sb" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_has_sb"].fillna(False).astype(bool)
    elif "netmhcipan_pctrank" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_pctrank"].notna() & (df["netmhcipan_pctrank"] < 2.0)
    else:
        df["_fail_mhcii"] = False

    df["passed_safety"] = ~(df["_fail_tox"] | df["_fail_hemo"] | df["_fail_mhci"] | df["_fail_mhcii"])
    df["status"] = df["passed_safety"].map({True: "passed", False: "failed_safety"})
    return df[df["passed_safety"]].copy(), df[~df["passed_safety"]].copy()