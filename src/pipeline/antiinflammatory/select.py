"""candidate pool selection + cross-function de-confounding — proposal §1–§4.

Workflow:
    1. SQL 取 imfp_lg_AIP P90+ 候选池 id + imfp_lg_AIP（~2M 行，秒级）
       —— 注意：aip_v5 / aip_esm2 不在 DB（check_score_coverage_aip 已核对），
       所以"L1→L2 级联"退化为 imfp_lg_AIP 单信号。
    2. 候选池在 peptides 主键上 JOIN 长度，过滤到 [11, 30] 窗口（提案 §2 硬约束）。
    3. 在 DataFrame 上做 Top150 + Bottom100 切片（in-memory，毫秒）。
    4. 对 Top150 + Bottom100 共约 250 个 id，在 peptide_enrichment_pk 主键上
       一次性查 11 个 tool 的 score 列（每个 tool ~1s，共 ~10s）：
         - imfp_lg_AIP（主排序，已在候选池）
         - imfp_lg_ACP/ADP/AHP/AMP（跨功能去混淆）
         - aopxsvm / amp-esm（抗氧化/抗菌跨功能）
         - toxinpred3 / hemopi2 / mhcflurry / netmhcipan_pctrank（安全）
         - plm4cpps / algpred2 / bepipred3（可开发性 + B 细胞表位）
    5. 计算 aip_specific = AIP_rank − max(other_function_rank) 作为"去跨功能混淆"信号
       （提案 §4.2）。
    6. 安全门控（可空缺性 + 长度硬约束）。

Why not a wide table:
    peptide_enrichment is 97 GB; 9 correlated subqueries × 20M rows OOM the planner
    (实测 ~30 min linear, confirmed in build_peptide_scores_table.py timing logs).
    复用 antibacterial/select.py 的 IO 策略：候选池 ≤ 2M 行 → Top/Bottom 250 id × tools。
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .. import db
from . import AntiinflammatoryConfig, length_bucket

log = logging.getLogger("antiinflammatory.select")

# 候选池 SQL：imfp_lg_AIP P90+（实测 P50=0.18, P90=0.87，候选池 2,024,889）
# 注：aip_v5 / aip_esm2 不在 DB，所以"L1→L2 级联"退化为单信号 imfp_lg_AIP。
CANDIDATE_POOL_SQL = """
WITH p90 AS (
    SELECT percentile_disc(%(p90)s) WITHIN GROUP (ORDER BY score)::float8 AS cutoff
      FROM peptide_enrichment
     WHERE tool = 'imfp_lg_AIP' AND score IS NOT NULL
)
SELECT
    e.peptide_id,
    e.score                            AS aip_imfp,
    e.label                            AS aip_imfp_label
  FROM peptide_enrichment e, p90
 WHERE e.tool = 'imfp_lg_AIP'
   AND e.score >= p90.cutoff
   AND e.peptide_id IN (SELECT id FROM peptides WHERE length BETWEEN %(len_min)s AND %(len_max)s)
ORDER BY e.score DESC
"""


def fetch_candidate_pool_ids(cfg: AntiinflammatoryConfig) -> pd.DataFrame:
    """候选池：imfp_lg_AIP 全库 P90 以上 且 length ∈ [len_min, len_max] 的肽 id + 分。

    注意 SQL 内联了 length 过滤（走 peptides_length_idx），避免外 JOIN 时
    20M 行无索引扫描。
    """
    log.info("fetching candidate pool (imfp_lg_AIP >= P%.0f, length in [%d,%d]) ...",
             cfg.p90_quantile * 100, cfg.len_min, cfg.len_max)
    t0 = time.time()
    with db.cursor(name="cur_pool", statement_timeout_ms=10 * 60_000) as cur:
        cur.itersize = 200_000
        cur.execute(CANDIDATE_POOL_SQL, {
            "p90": cfg.p90_quantile,
            "len_min": cfg.len_min,
            "len_max": cfg.len_max,
        })
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["peptide_id", "aip_imfp", "aip_imfp_label"])
    df["peptide_id"] = df["peptide_id"].astype("int64")
    df["aip_imfp"] = df["aip_imfp"].astype("float32")
    log.info("  pool: %s peptides in %.1fs", f"{len(df):,}", time.time() - t0)
    return df


def enrich_top_bottom_with_scores(
    top_ids: list[int],
    bottom_ids: list[int],
    extra_tools: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """对 Top + Bottom 共约 250 个肽 id 一次性拉所有 tool 的 score。

    250 id × 11 tools × 0.2ms ≈ 0.5s 全部完成（沿用 antibacterial 的策略）。
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

    沿用 antibacterial/select.py 的实现：每个肽聚合
      - min_rank_pct / min_sb_rank_pct / has_sb / has_wb / n_alleles
      - assessed: peptide 是否在 10-30 aa 且有预测结果
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


def compute_aip_specific(df: pd.DataFrame) -> pd.DataFrame:
    """跨功能去混淆（提案 §4.2）—— 计算 `aip_specific` 信号。

    AIP 模型把"功能肽"都打高分；本步把"该肽在其他功能上的得分"作为
    "它有多像通用功能肽"，从 AIP 分里**减去**这部分，剩下的才是
    "AIP 特有信号"。

    实际可用跨功能信号（提案 §4.2 期望的全部 - 缺失）：
      - aopxsvm        (抗氧化)         ← DB ✅
      - amp-esm        (抗菌)           ← DB ✅
      - blsam_tip_lr   (抗黑素 LR)      ← DB ❌ 缺失
      - blsam_tip_gbm  (抗黑素 GBM)     ← DB ❌ 缺失
      - imfp_lg_ACP    (iMFP-LG ACP)    ← DB ✅
      - imfp_lg_ADP    (iMFP-LG ADP)    ← DB ✅
      - imfp_lg_AHP    (iMFP-LG AHP)    ← DB ✅
      - imfp_lg_AMP    (iMFP-LG AMP)    ← DB ✅

    **抗黑素通道缺失**：与 antimelanin v1 一致（BLSAM-TIP 重建版未上线生产 DB），
    本管线跨功能去混淆只用 6 个通道（抗氧化/抗菌/iMFP-LG 五通道）。
    这意味着 "抗黑素成分贡献"无法在 de-confounding 中显式扣减，但
    `tipred` 99.5% 阳性（提案 §1 已排除）也提供了一个间接的"非抗黑素"弱信号
    —— 在本步中不使用它（tipred 与 AIP 正交性弱、贡献已被 iMFP-LG ACP/ADP/AHP
    通道覆盖）。

    输出列：
      - aip_specific_rank: 减完后的 rank pct (-1 ~ +1)
    """
    df = df.copy()

    other_cols = {
        "antiox":   "aopxsvm",
        "antibac":  "amp-esm",
        "imfp_acp": "imfp_lg_ACP",
        "imfp_adp": "imfp_lg_ADP",
        "imfp_ahp": "imfp_lg_AHP",
        "imfp_amp": "imfp_lg_AMP",
    }

    # rank pct 化（0 ~ 1，越高越像该功能）
    other_pct = {}
    for short, col in other_cols.items():
        if col in df.columns and df[col].notna().any():
            other_pct[short] = df[col].rank(pct=True, ascending=True)
        else:
            log.warning("  de-confounding: column %s missing or all-NaN, skipped", col)
            other_pct[short] = pd.Series(0.5, index=df.index)

    other_df = pd.DataFrame(other_pct)
    other_max = other_df.max(axis=1)

    # 主信号 aip_imfp 的 rank pct
    if "aip_imfp" in df.columns and df["aip_imfp"].notna().any():
        aip_rank = df["aip_imfp"].rank(pct=True, ascending=True)
    else:
        log.warning("  de-confounding: aip_imfp missing or all-NaN; aip_specific = 0")
        aip_rank = pd.Series(0.5, index=df.index)

    df["other_max"] = other_max
    df["aip_specific"] = aip_rank - other_max
    log.info("  aip_specific: min=%.4f, max=%.4f, median=%.4f",
             df["aip_specific"].min(), df["aip_specific"].max(), df["aip_specific"].median())
    return df


def apply_safety(
    df: pd.DataFrame,
    cfg: AntiinflammatoryConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """安全门控：可空缺性原则（提案 §2 + README §三 第五项，与另三方向同套）。

    规则：
      - toxinpred3 全覆盖：score IS NULL → 默认 pass（DB 全覆盖，不会发生）
      - hemopi2 / mhcflurry / netmhcipan_pctrank 列大部分为 NULL → NULL 默认 pass
      - 有分且超过阈值 → failed_safety
      - 长度硬约束 [len_min, len_max]：已在候选池 SQL 层强制，这里冗余检查
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
    # netMHCIIpan：从独立表 `netmhc_score` 聚合得到的 `netmhcipan_has_sb` 列；
    # SB = Strong Binder（%Rank ≤ 2）。有 SB 才判 failed。
    if "netmhcipan_has_sb" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_has_sb"].fillna(False).astype(bool)
    elif "netmhcipan_pctrank" in df.columns:
        df["_fail_mhcii"] = df["netmhcipan_pctrank"].notna() & (df["netmhcipan_pctrank"] < 2.0)
    else:
        df["_fail_mhcii"] = False

    # 长度硬约束冗余检查（提案 §2：≤10 aa 分数是随机噪声 AUC 0.498）
    df["_fail_len"] = ~df["length"].between(cfg.len_min, cfg.len_max) if "length" in df.columns else False

    df["passed_safety"] = ~(df["_fail_tox"] | df["_fail_hemo"] | df["_fail_mhci"] | df["_fail_mhcii"] | df["_fail_len"])
    df["status"] = df["passed_safety"].map({True: "passed", False: "failed_safety"})
    return df[df["passed_safety"]].copy(), df[~df["passed_safety"]].copy()
