"""candidate pool selection — proposal §1.

Workflow:
    1. SQL 取全库 aopxsvm P90 cutoff，WHERE 安全门控（可空缺性）→ 候选池 DataFrame
    2. 取候选池在 aopxsvm 上的 Top N + Bottom M（默认 150 + 100）
    3. 对候选池逐列查其他 8 个 tool 的 score 列 → merge 回 DataFrame

Why not a wide table:
    peptide_enrichment is 97 GB; 9 correlated subqueries × 20M rows OOM the planner
    (实测 ~30 min linear, confirmed in build_peptide_scores_table.py timing logs).
    We instead keep selection ≤ 2M rows (top 10%) and pay 9 small index lookups per
    peptide afterwards. Total cost: 9 × 10s = ~90s instead of 30+ min.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable
from pathlib import Path

import pandas as pd

from .. import db
from . import AntioxidantConfig

log = logging.getLogger("antioxidant.select")

# 候选池 SQL：与 proposal §1 完全对齐的逻辑，但写在 peptide_enrichment 长表上
# ——> 这里用 9 个相关子查询的写法在 20M 行上不可行，所以改用一次 aopxsvm 索引扫描
#     + 一次 self-join 取其他列。详见 select_candidate_pool()。

CANDIDATE_POOL_SQL = """
WITH p90 AS (
    SELECT percentile_disc(%(p90)s) WITHIN GROUP (ORDER BY score)::float8 AS cutoff
      FROM peptide_enrichment
     WHERE tool = 'aopxsvm' AND score IS NOT NULL
)
SELECT
    e.peptide_id,
    e.score                            AS aopxsvm,
    e.label                            AS aopxsvm_label
  FROM peptide_enrichment e, p90
 WHERE e.tool = 'aopxsvm'
   AND e.score >= p90.cutoff
ORDER BY e.score DESC
"""


def fetch_candidate_pool_ids(cfg: AntioxidantConfig) -> pd.DataFrame:
    """候选池：aopxsvm 全库 P90 以上的肽 id + aopxsvm 分。"""
    log.info("fetching candidate pool (aopxsvm >= P%.0f) ...", cfg.p90_quantile * 100)
    t0 = time.time()
    with db.cursor(name="cur_pool", statement_timeout_ms=10 * 60_000) as cur:
        cur.itersize = 200_000
        cur.execute(CANDIDATE_POOL_SQL, {"p90": cfg.p90_quantile})
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["peptide_id", "aopxsvm", "aopxsvm_label"])
    df["peptide_id"] = df["peptide_id"].astype("int64")
    df["aopxsvm"] = df["aopxsvm"].astype("float32")
    log.info("  pool: %s peptides in %.1fs", f"{len(df):,}", time.time() - t0)
    return df


def _fetch_column(tool: str, peptide_ids: Iterable[int], statement_timeout_ms: int = 600_000) -> pd.Series:
    """对单个 tool 查 (peptide_id, score)，返回以 peptide_id 为索引的 Series。

    使用 COPY ... TO STDOUT，避 fetchall 在 20M 行上的客户端 IO 瓶颈。
    """
    ids = list(peptide_ids)
    if not ids:
        return pd.Series(dtype="float32", name=tool)

    log.info("  fetching tool=%s ...", tool)
    t0 = time.time()
    # 走 server-side + COPY + 临时文件 + pandas read_csv
    tmp_path = f"/tmp/_pipeline_{tool.replace('/', '_').replace('-', '_')}.tsv"
    with db.connect(statement_timeout_ms=statement_timeout_ms) as conn:
        with conn.cursor() as cur:
            copy_sql = (
                f"COPY (SELECT peptide_id, score FROM peptide_enrichment "
                f"WHERE tool = '{tool}' AND score IS NOT NULL) TO STDOUT"
            )
            with open(tmp_path, "wb") as f:
                cur.copy_expert(copy_sql, f)

    df = pd.read_csv(
        tmp_path, sep="\t", header=None, names=["peptide_id", "score"],
        dtype={"score": "float32"},
    )
    Path(tmp_path).unlink(missing_ok=True)
    log.info("    %s non-null in %.1fs", f"{len(df):,}", time.time() - t0)
    return df.set_index("peptide_id")["score"].rename(tool)


def enrich_with_scores(
    candidate_df: pd.DataFrame,
    extra_tools: list[str],
) -> pd.DataFrame:
    """对候选池补齐其他 tool 的 score 列（left join，结果保留所有候选肽）。

    策略：在 peptide_id = ANY([2M ids]) 上查工具走 peptide_enrichment_pk 主键，
    planner 会一次性 batch fetch，2M id × tool 理论上秒级。
    但实则 planner 不一定能采用 Index Only Scan + IN-batch 计划，所以这里 fallback
    为 _fetch_column 单 tool 全扫（已验证 1.5s/total tool）。
    """
    out = candidate_df.set_index("peptide_id")
    for tool in extra_tools:
        col = _fetch_column(tool, candidate_df["peptide_id"].tolist())
        out[tool] = col          # index 对齐 → 缺的肽得到 NaN（= NULL）
    return out.reset_index()


def enrich_top_bottom_with_scores(
    top_ids: list[int],
    bottom_ids: list[int],
    extra_tools: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """对 Top + Bottom 共约 250 个肽 id 一次性拉所有 tool 的 score。

    250 id × 9 tools × 0.2ms ≈ 0.5s 全部完成。
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


def select_top_bottom(df: pd.DataFrame, cfg: AntioxidantConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """在候选池内取 Top N（功能分最高）和 Bottom M（功能分最低）。"""
    sorted_df = df.sort_values("aopxsvm", ascending=False).reset_index(drop=True)
    top = sorted_df.head(cfg.top_n).copy()
    bot = sorted_df.tail(cfg.bottom_n).copy()
    return top, bot


# 安全门控：可空缺性原则（proposal §1 + README §三 第五项）
def apply_safety(
    df: pd.DataFrame,
    cfg: AntioxidantConfig,
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
    if "netmhcipan_pctrank" in df.columns:
        # NetMHCIIpan %Rank 越低越强结合；< 2 是 SB 强结合 → 不通过
        df["_fail_mhcii"] = df["netmhcipan_pctrank"].notna() & (df["netmhcipan_pctrank"] < 2.0)
    else:
        df["_fail_mhcii"] = False

    df["passed_safety"] = ~(df["_fail_tox"] | df["_fail_hemo"] | df["_fail_mhci"] | df["_fail_mhcii"])
    df["status"] = df["passed_safety"].map({True: "passed", False: "failed_safety"})
    return df[df["passed_safety"]].copy(), df[~df["passed_safety"]].copy()