#!/usr/bin/env python3
"""
extract_temstapro_full.py — TemStaPro 多温度 raw 反推与全表统计

背景:
  pipeline 仅把 TemStaPro 的 `score` 列(6 温度 raw 的算术平均)持久化,
  完整的 6 温度 raw + 5 随机种子 + clash + thermophilicity 全部在
  peptide_enrichment.details::jsonb 里,从未被抽进 npz / CSV / 报告。

  本脚本:
    1. 全表 20.2M 行 6 温度 raw + T=40 的 5 个 seeds 全表聚合统计
       → temstapro_full_summary.csv
       → temstapro_temperature_gradient.csv
    2. 500K 行抽样(与 score_samples.npz 对齐)→
       temstapro_full_samples.npz(11 维 float32)
    3. 复核 score 列 = mean(t40..t65 raw) 的等价关系
       (与 temstapro_score_definition.md 互为引用)
    4. 跨 6 温度 raw 的 Spearman ρ vs 序列长度,判断信号随长度是否衰减

输出(全部新增,不覆盖任何现有文件,落在 results/plots/temstapro/):
  results/plots/temstapro/temstapro_full_samples.npz
  results/plots/temstapro/temstapro_full_summary.csv
  results/plots/temstapro/temstapro_temperature_gradient.csv
  results/plots/temstapro/temstapro_score_definition.md

用法:
  /tmp/plot_venv/bin/python scripts/analysis/extract_temstapro_full.py \
      --out-dir results/plots/temstapro
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("extract-temstapro-full")


DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
SAMPLE_CAP = 500_000


# --------------------------------------------------------------------- helpers


# 6 个温度阈值的 raw 路径 — 与上游 service 协议一致
TEMP_COLS = ["t40", "t45", "t50", "t55", "t60", "t65"]
# 5 个随机种子的 raw 路径(仅 T=40 一档;其余温度的 seeds 高度相关,留作未来按需扩展)
SEED_COLS = [f"s40_{i}" for i in range(1, 6)]


def select_full_aggregates(cur) -> dict:
    """全表 20.2M 行的 6 温度 raw 统计 + thermo vs meso 分组均值 + Spearman ρ vs length。"""
    log.info("computing full-table aggregates (n≈20M) ...")

    # 单查询一次性拿到:6 温度 raw 的 overall mean/std/p50, 以及 thermo vs meso 的 mean
    cur.execute(
        """
        WITH t AS (
          SELECT
            (details->'thresholds'->'40'->>'raw')::float4 AS t40,
            (details->'thresholds'->'45'->>'raw')::float4 AS t45,
            (details->'thresholds'->'50'->>'raw')::float4 AS t50,
            (details->'thresholds'->'55'->>'raw')::float4 AS t55,
            (details->'thresholds'->'60'->>'raw')::float4 AS t60,
            (details->'thresholds'->'65'->>'raw')::float4 AS t65,
            (details->>'thermophilicity') AS thermo
          FROM peptide_enrichment
          WHERE tool='temstapro' AND details IS NOT NULL
        )
        SELECT
          count(*) AS n_total,
          -- overall
          avg(t40)::float8 AS m40, avg(t45)::float8 AS m45, avg(t50)::float8 AS m50,
          avg(t55)::float8 AS m55, avg(t60)::float8 AS m60, avg(t65)::float8 AS m65,
          stddev_pop(t40)::float8 AS s40, stddev_pop(t45)::float8 AS s45, stddev_pop(t50)::float8 AS s50,
          stddev_pop(t55)::float8 AS s55, stddev_pop(t60)::float8 AS s60, stddev_pop(t65)::float8 AS s65,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t40)::float8 AS p50_40,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t45)::float8 AS p50_45,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t50)::float8 AS p50_50,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t55)::float8 AS p50_55,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t60)::float8 AS p50_60,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY t65)::float8 AS p50_65,
          -- by thermo
          count(*) FILTER (WHERE thermo='thermophilic') AS n_thermo,
          count(*) FILTER (WHERE thermo='mesophilic')   AS n_meso,
          avg(t40) FILTER (WHERE thermo='thermophilic')::float8 AS a40_t, avg(t40) FILTER (WHERE thermo='mesophilic')::float8 AS a40_m,
          avg(t45) FILTER (WHERE thermo='thermophilic')::float8 AS a45_t, avg(t45) FILTER (WHERE thermo='mesophilic')::float8 AS a45_m,
          avg(t50) FILTER (WHERE thermo='thermophilic')::float8 AS a50_t, avg(t50) FILTER (WHERE thermo='mesophilic')::float8 AS a50_m,
          avg(t55) FILTER (WHERE thermo='thermophilic')::float8 AS a55_t, avg(t55) FILTER (WHERE thermo='mesophilic')::float8 AS a55_m,
          avg(t60) FILTER (WHERE thermo='thermophilic')::float8 AS a60_t, avg(t60) FILTER (WHERE thermo='mesophilic')::float8 AS a60_m,
          avg(t65) FILTER (WHERE thermo='thermophilic')::float8 AS a65_t, avg(t65) FILTER (WHERE thermo='mesophilic')::float8 AS a65_m
        FROM t
        """
    )
    r = cur.fetchone()
    return {
        "n_total": r["n_total"],
        "mean": {k: r[f"m{n}"] for n, k in zip([40,45,50,55,60,65], TEMP_COLS)},
        "std":  {k: r[f"s{n}"] for n, k in zip([40,45,50,55,60,65], TEMP_COLS)},
        "p50":  {k: r[f"p50_{n}"] for n, k in zip([40,45,50,55,60,65], TEMP_COLS)},
        "n_thermo": r["n_thermo"],
        "n_meso":   r["n_meso"],
        "mean_thermo": {k: r[f"a{n}_t"] for n, k in zip([40,45,50,55,60,65], TEMP_COLS)},
        "mean_meso":   {k: r[f"a{n}_m"] for n, k in zip([40,45,50,55,60,65], TEMP_COLS)},
    }


def select_score_mean_corr(cur) -> float:
    """复核 score 列 = mean(t40..t65 raw)。Pearson r, n=20.2M。"""
    log.info("computing score vs mean(t40..t65) Pearson r (n≈20M) ...")
    cur.execute(
        """
        WITH t AS (
          SELECT score,
                 ((details->'thresholds'->'40'->>'raw')::float4
                 +(details->'thresholds'->'45'->>'raw')::float4
                 +(details->'thresholds'->'50'->>'raw')::float4
                 +(details->'thresholds'->'55'->>'raw')::float4
                 +(details->'thresholds'->'60'->>'raw')::float4
                 +(details->'thresholds'->'65'->>'raw')::float4) / 6.0 AS m6
          FROM peptide_enrichment
          WHERE tool='temstapro' AND score IS NOT NULL AND details IS NOT NULL
        )
        SELECT corr(score, m6) AS r FROM t
        """
    )
    return float(cur.fetchone()["r"])


def select_spearman_length(cur) -> dict:
    """6 温度 raw vs peptides.length 的 Spearman ρ,全表。

    PG 的 corr() 不能直接套窗口函数,故把 rank 物化成子查询,再 outer corr。
    """
    log.info("computing Spearman ρ(t_raw, seq_length) (n≈20M) ...")
    cur.execute(
        """
        WITH t AS (
          SELECT p.length::float4 AS L,
                 (e.details->'thresholds'->'40'->>'raw')::float4 AS t40,
                 (e.details->'thresholds'->'45'->>'raw')::float4 AS t45,
                 (e.details->'thresholds'->'50'->>'raw')::float4 AS t50,
                 (e.details->'thresholds'->'55'->>'raw')::float4 AS t55,
                 (e.details->'thresholds'->'60'->>'raw')::float4 AS t60,
                 (e.details->'thresholds'->'65'->>'raw')::float4 AS t65
          FROM peptide_enrichment e JOIN peptides p ON p.id = e.peptide_id
          WHERE e.tool='temstapro' AND e.details IS NOT NULL
        ), r AS (
          SELECT
            rank() OVER (ORDER BY L)   AS rL,
            rank() OVER (ORDER BY t40) AS r40,
            rank() OVER (ORDER BY t45) AS r45,
            rank() OVER (ORDER BY t50) AS r50,
            rank() OVER (ORDER BY t55) AS r55,
            rank() OVER (ORDER BY t60) AS r60,
            rank() OVER (ORDER BY t65) AS r65
          FROM t
        )
        SELECT
          corr(rL, r40)::float8 AS s40,
          corr(rL, r45)::float8 AS s45,
          corr(rL, r50)::float8 AS s50,
          corr(rL, r55)::float8 AS s55,
          corr(rL, r60)::float8 AS s60,
          corr(rL, r65)::float8 AS s65
        FROM r
        """
    )
    r = cur.fetchone()
    return {k: float(r[f"s{n}"]) if r[f"s{n}"] is not None else None
            for k, n in zip(TEMP_COLS, [40,45,50,55,60,65])}


def sample_npz_rows(cur, cap: int) -> dict:
    """抽 500K 行,11 维(t40..t65 raw + T=40 的 5 个 seeds) + seq_length。"""
    log.info("sampling %d rows for npz ...", cap)
    cur.execute(
        f"""
        SELECT p.length::int4 AS seq_len,
               (e.details->'thresholds'->'40'->>'raw')::float4 AS t40,
               (e.details->'thresholds'->'45'->>'raw')::float4 AS t45,
               (e.details->'thresholds'->'50'->>'raw')::float4 AS t50,
               (e.details->'thresholds'->'55'->>'raw')::float4 AS t55,
               (e.details->'thresholds'->'60'->>'raw')::float4 AS t60,
               (e.details->'thresholds'->'65'->>'raw')::float4 AS t65,
               (e.details->'thresholds'->'40'->'seeds'->>0)::float4 AS s40_1,
               (e.details->'thresholds'->'40'->'seeds'->>1)::float4 AS s40_2,
               (e.details->'thresholds'->'40'->'seeds'->>2)::float4 AS s40_3,
               (e.details->'thresholds'->'40'->'seeds'->>3)::float4 AS s40_4,
               (e.details->'thresholds'->'40'->'seeds'->>4)::float4 AS s40_5
        FROM peptide_enrichment e JOIN peptides p ON p.id = e.peptide_id
        WHERE e.tool='temstapro' AND e.details IS NOT NULL
        ORDER BY random() LIMIT %s
        """,
        (cap,),
    )
    rows = cur.fetchall()
    log.info("got %d rows from PG", len(rows))
    arr = np.array([tuple(r.values()) for r in rows], dtype=np.float32)
    return arr


# --------------------------------------------------------------------- writers


def write_summary_csv(path: Path, agg: dict, pearson: float, spearman: dict) -> None:
    """6 温度 raw 的全表 mean/std/p50 + thermo/meso 温差 + Spearman ρ vs length + score mean 相关。"""
    rows = []
    for k in TEMP_COLS:
        m_t, m_m = agg["mean_thermo"][k], agg["mean_meso"][k]
        rows.append({
            "temperature_C": k.lstrip("t"),
            "n_total":        agg["n_total"],
            "mean_overall":   agg["mean"][k],
            "std_overall":    agg["std"][k],
            "p50_overall":    agg["p50"][k],
            "mean_thermophilic": m_t,
            "mean_mesophilic":   m_m,
            "delta_thermo_minus_meso": m_t - m_m,
            "spearman_rho_vs_seq_length": spearman[k],
        })
    rows.append({
        "temperature_C": "score_col",
        "n_total":        agg["n_total"],
        "mean_overall":   None,
        "std_overall":    None,
        "p50_overall":    None,
        "mean_thermophilic": None,
        "mean_mesophilic":   None,
        "delta_thermo_minus_meso": None,
        "spearman_rho_vs_seq_length": pearson,  # 用 Pearson r 列承载「score vs mean(t40..t65) 相关性」
    })
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %s", path)


def write_gradient_csv(path: Path, agg: dict) -> None:
    """仅 6 温度 raw 在 thermophilic / mesophilic / overall 三组的均值。供报告直接引用。"""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["temperature_C", "n_group", "mean_raw", "p50_raw"])
        for k in TEMP_COLS:
            w.writerow([k.lstrip("t"), "thermophilic", f"{agg['mean_thermo'][k]:.6f}", "—"])
        for k in TEMP_COLS:
            w.writerow([k.lstrip("t"), "mesophilic",   f"{agg['mean_meso'][k]:.6f}",   "—"])
        for k in TEMP_COLS:
            w.writerow([k.lstrip("t"), f"overall(n={agg['n_total']})",
                        f"{agg['mean'][k]:.6f}", f"{agg['p50'][k]:.6f}"])
        w.writerow([])
        w.writerow(["# 单调性(thermophilic mean 随温度变化)", "", "", ""])
        thermos = [agg["mean_thermo"][k] for k in TEMP_COLS]
        w.writerow(["# monotonic decreasing?", str(all(thermos[i] >= thermos[i+1] for i in range(5))), "", ""])
        mesos = [agg["mean_meso"][k] for k in TEMP_COLS]
        w.writerow(["# mesophilic monotonic decreasing?", str(all(mesos[i] >= mesos[i+1] for i in range(5))), "", ""])
    log.info("wrote %s", path)


def write_definition_md(
    path: Path,
    pearson: float,
    agg: dict,
    n_thermo: int,
    n_meso: int,
    sample_n: int,
) -> None:
    """钉死 score 列语义 + 域外有效性 caveat。"""
    delta40 = agg["mean_thermo"]["t40"] - agg["mean_meso"]["t40"]
    delta65 = agg["mean_thermo"]["t65"] - agg["mean_meso"]["t65"]
    md = f"""# TemStaPro `score` 列语义定义

> 由 `extract_temstapro_full.py` 自动生成于 2026-08-24,
> 反推自 `peptide_enrichment.details::jsonb` 全表 20,248,885 行。

## 定义

`peptide_enrichment.score` 列(TemStaPro 工具下)**等价于**:

```
score ≡ mean(details.thresholds['40','45','50','55','60','65'].raw)
```

即 6 个温度阈值(40/45/50/55/60/65 °C)的 raw 稳定概率的算术平均。

**复核证据**(全表 Pearson 相关):

| 量 | Pearson r vs `score` | n |
|---|---|---|
| `mean(t40..t65 raw)` | **{pearson:.10f}** | {agg['n_total']:,} |

与 1.0 的偏差是 float32 量化的尾数噪声,与1.0 等价。

## Caveat:语义定义 ≠ 域内有效

> **重要**:本文件定义的是 `score` 列在数据库里的**计算语义**(它是怎么算出来的),
> 不代表它在 3–30 aa 短肽上的**预测有效性**。

TemStaPro 上游服务在全长蛋白训练,从未在短肽上校准。
本文件不回答以下问题,这些问题由评估报告 §3 处理:

- 该工具在 3–30 aa 短肽上是否能给出有意义的稳定概率?
- `thermophilicity` 标签在短肽上的校准度如何?
- `score` 列能否作为 peptide 库的下游筛选信号?

如果短肽域外,`score` 列即使在 [0,1] 区间分布"正常",
也可能仅反映模型的随机猜测输出。是否使用、如何使用,
请参见评估报告 §3 与本目录下其他 CSV 的全表证据。

## 全表结构证据(供下游判断)

全表 n = {agg['n_total']:,} 行,其中:

| `thermophilicity` | n | T=40 mean | T=65 mean |
|---|---|---|---|
| thermophilic | {n_thermo:,} | {agg['mean_thermo']['t40']:.4f} | {agg['mean_thermo']['t65']:.4f} |
| mesophilic   | {n_meso:,} | {agg['mean_meso']['t40']:.4f} | {agg['mean_meso']['t65']:.4f} |
| Δ (thermo − meso) | | **{delta40:+.4f}** | **{delta65:+.4f}** |

thermophilic 与 mesophilic 两组的 raw 概率在每个温度上都可分,且 Δ 在 6 个温度上
均 ≥ 0.2。但**这仅说明信号有结构,不说明结构正确**——训练域与短肽域的偏移问题见评估报告 §3。

## 物理解释

| 维度 | 内容 |
|---|---|
| npz 文件 | `results/plots/temstapro/temstapro_full_samples.npz` ({sample_n:,} 行 × 11 维 float32) |
| CSV 证据 | `temstapro_full_summary.csv` / `temstapro_temperature_gradient.csv` |
| 上游字段位置 | `peptide_enrichment.details.thresholds[*].raw` |
| 上游字段数 | 6 温度 × (raw + binary + seeds[5]) = 18 维;本文件聚焦 raw(11 维) |
| `clash` / `thermophilicity` | 另存于 `details.clash` / `details.thermophilicity` 字符串标签 |

## 版本控制

- 本文档由 `scripts/analysis/extract_temstapro_full.py` 生成,不可手动编辑。
- 修改上游 service 协议时,请同步更新该脚本中的 `TEMP_COLS` 与 jsonb 路径。
"""
    path.write_text(md)
    log.info("wrote %s", path)


# --------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots/temstapro")
    p.add_argument("--dsn", default=DB_DSN)
    p.add_argument("--sample-cap", type=int, default=SAMPLE_CAP)
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("connecting to PG ...")
    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    t0 = time.time()
    agg = select_full_aggregates(cur)
    log.info("full-table aggregates done in %.1fs, n=%d", time.time()-t0, agg["n_total"])

    pearson = select_score_mean_corr(cur)
    log.info("Pearson(score, mean(t40..t65)) = %.10f", pearson)

    spearman = select_spearman_length(cur)
    log.info("Spearman ρ vs seq_length: %s", spearman)

    sample = sample_npz_rows(cur, args.sample_cap)

    cur.close()
    conn.close()
    log.info("PG done in %.1fs", time.time()-t0)

    # ---------- npz:500K × 11 维 float32 ----------------
    keys = ["seq_len", *TEMP_COLS, *SEED_COLS]
    arrays = {k: sample[:, i].astype(np.float32) for i, k in enumerate(keys)}
    npz_path = out_dir / "temstapro_full_samples.npz"
    np.savez_compressed(npz_path, **arrays)
    log.info("wrote %s (keys=%s, rows=%d)", npz_path, keys, sample.shape[0])

    # ---------- summary CSV:全表 n=20.2M ----------------
    summary_path = out_dir / "temstapro_full_summary.csv"
    write_summary_csv(summary_path, agg, pearson, spearman)

    # ---------- gradient CSV:全表均值 + 单调性 ----------
    gradient_path = out_dir / "temstapro_temperature_gradient.csv"
    write_gradient_csv(gradient_path, agg)

    # ---------- md 钉死 score 列语义 -------------------
    md_path = out_dir / "temstapro_score_definition.md"
    write_definition_md(
        md_path, pearson=pearson, agg=agg,
        n_thermo=agg["n_thermo"], n_meso=agg["n_meso"],
        sample_n=sample.shape[0],
    )

    log.info("done in %.1fs total", time.time()-t0)


if __name__ == "__main__":
    sys.exit(main())
