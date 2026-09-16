"""candidate pool selection — proposal §1.

抗黑素方向的"候选池"设计：

提案 §1 期望：
    SELECT ... WHERE blsam_tip_lr >= 0.5 AND blsam_tip_gbm >= 0.5 ...
  → 但这两个分数**不在生产 DB**（详见 check_score_coverage_melanin.py）。
  → 退化：用 tipred 单分数作为弱排序信号，但 tipred 99.5% 阳性（提案 §0 已
    明确"已排除"，无富集力），所以这里**不**做功能预筛——候选池在功能上
    等同于全库。

本模块的策略（**改进后**）：
  1. SQL 端直接在 `peptide_enrichment(tipred)` 上 ORDER BY score LIMIT 250，
     不用 Python 拉 20.25M 行（实测 2.1 秒；旧版 LEFT JOIN peptides 全表 461 秒）。
  2. 在 250 id 上做安全门控 + enrich 7 个 tool + netMHCIIpan（独立表）。
  3. 取通过门控者按 tipred 重排 → Top/Bottom → 落库。

Why not a wide table:
    peptide_enrichment is 97 GB; 9 correlated subqueries × 20M rows OOM the
    planner. We follow antibacterial/select.py 的策略：在切片上做小查询。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd

from .. import db
from . import AntimelaninConfig

log = logging.getLogger("antimelanin.select")


# 在 PostgreSQL 端 ORDER BY score LIMIT 250 切片 —— 实测 2.1 秒。
# 这是抗黑素方向对提案 §1 的**改进**：因 BLSAM-TIP 缺失，候选池=全库；
# 没必要把全库拉到 Python，只取 tipred 排序的两端即可。
CANDIDATE_TOP_BOTTOM_SQL = """
(SELECT peptide_id, score AS tipred, label AS tipred_label, 'top' AS rank_band
   FROM peptide_enrichment
  WHERE tool = 'tipred' AND score IS NOT NULL
  ORDER BY score DESC NULLS LAST LIMIT %(top_n)s)
UNION ALL
(SELECT peptide_id, score AS tipred, label AS tipred_label, 'bottom' AS rank_band
   FROM peptide_enrichment
  WHERE tool = 'tipred' AND score IS NOT NULL
  ORDER BY score ASC NULLS LAST LIMIT %(bottom_n)s)
"""


def fetch_candidate_pool_ids(cfg: AntimelaninConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """取 tipred 排序的 Top N + Bottom M（默认 150 + 100）。

    返回 (top_df, bottom_df)。每个 df 包含列：
        peptide_id, tipred, tipred_label, rank_band

    注意：因 TIPred 99.5% 阳性无富集力，本管线**不**做功能预筛。
    返回的 Top/Bottom 就是全库 tipred 排序的两端（250 个肽）。
    """
    log.info("fetching tipred top/bottom slices (no functional prescreen) ...")
    t0 = time.time()
    with db.cursor(name="cur_pool_amel", statement_timeout_ms=10 * 60_000) as cur:
        cur.itersize = 200_000
        cur.execute(
            CANDIDATE_TOP_BOTTOM_SQL,
            {"top_n": cfg.top_n, "bottom_n": cfg.bottom_n},
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["peptide_id", "tipred", "tipred_label", "rank_band"])
    df["peptide_id"] = df["peptide_id"].astype("int64")
    df["tipred"]     = df["tipred"].astype("float32")
    log.info("  fetched %s peptides in %.1fs", f"{len(df):,}", time.time() - t0)

    top_df = df[df["rank_band"] == "top"].drop(columns=["rank_band"]).reset_index(drop=True)
    bot_df = df[df["rank_band"] == "bottom"].drop(columns=["rank_band"]).reset_index(drop=True)
    log.info("  split: %d top + %d bottom", len(top_df), len(bot_df))
    return top_df, bot_df


def enrich_top_bottom_with_scores(
    top_ids: list[int],
    bottom_ids: list[int],
    extra_tools: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """对 Top + Bottom 共约 250 个肽 id 一次性拉所有 tool 的 score。

    与 antibacterial/select.py 同样的策略：250 id × 7 tools × 0.2ms ≈ 0.5s。
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
        # bepipred3 details JSONB 字段较大，主键 (peptide_id, tool) 上 PK lookup
        # 在某些 peptide_id 上首次访问会触发 index page miss，实测 250 id 偶发
        # >60s；统一用 10 分钟超时（与 fetch_candidate_pool_ids 一致）。
        with db.cursor(name=f"cur_topbot_amel_{tool}", statement_timeout_ms=10 * 60_000) as cur:
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

    与 antibacterial/select.py 同样的聚合逻辑：
      - min_rank_pct / min_sb_rank_pct / has_sb / has_wb / n_alleles / assessed
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
    seq_sql = "SELECT id, sequence, length FROM peptides WHERE id = ANY(%s)"
    with db.cursor() as cur:
        cur.execute(seq_sql, (ids,))
        seqs = pd.DataFrame(cur.fetchall(), columns=["peptide_id", "sequence", "length"])
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

    out = seqs.merge(agg_df, on="sequence", how="left")
    out["netmhcipan_assessed"] = out["min_rank_pct"].notna()
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


# 安全门控：可空缺性原则（与抗菌/抗氧化同套）
def apply_safety(
    df: pd.DataFrame,
    cfg: AntimelaninConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (passed_df, failed_df)。

    规则：
      - toxinpred3 全覆盖：score IS NULL → 默认 pass（DB 全覆盖，不会发生）
      - hemopi2 / mhcflurry / netmhcipan 列大部分为 NULL → NULL 默认 pass
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
    # netMHCIIpan：硬门控用 SB（%Rank ≤ 2），与抗菌方向一致。
    if "netmhcipan_has_sb" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_has_sb"].fillna(False).astype(bool)
    elif "netmhcipan_pctrank" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_pctrank"].notna() & (df["netmhcipan_pctrank"] < cfg.mhcii_threshold)
    else:
        df["_fail_mhcii"] = False

    df["passed_safety"] = ~(df["_fail_tox"] | df["_fail_hemo"] | df["_fail_mhci"] | df["_fail_mhcii"])
    # 与抗菌/抗氧化对齐：passed/failed_safety 二选一；最终 status 在 assemble.py
    # 里进一步把 passed 改成 'WIP'（提案 §5 "全方向标 WIP"）。
    df["status"] = df["passed_safety"].map({True: "passed", False: "failed_safety"})
    return df[df["passed_safety"]].copy(), df[~df["passed_safety"]].copy()