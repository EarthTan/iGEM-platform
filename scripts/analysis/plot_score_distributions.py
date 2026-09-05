#!/usr/bin/env python3
"""
plot_score_distributions.py
从 PostgreSQL `peptide_enrichment` 拉每个工具的 score 分布,
画 3x3 grid 直方图 + KDE,输出 PNG + 汇总统计 CSV。

用法:
  /tmp/plot_venv/bin/python scripts/analysis/plot_score_distributions.py \
      --out-dir results/plots

设计:
  - 每个工具最多采样 500k 条(对 KDE 形状足够,内存安全)
  - toxinpred3 只有 4.88M,全量拉
  - mhcflurry 13.5w,全量
  - 直方图 bins=80,KDE 用 scipy.stats.gaussian_kde
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from scipy.stats import gaussian_kde

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("plot-score")


DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"

# 每个工具的样本上限。超过这个数就 RANDOM() 抽样。
SAMPLE_CAP = 500_000
HIST_BINS = 80       # 直方图分箱
KDE_POINTS = 400     # KDE 曲线采样点


def fetch_tool_scores(cur, tool: str, cap: int) -> np.ndarray:
    """拉一个工具的 score。对超大表用 TABLESAMPLE 不可行(只支持页级),
    所以超过 cap 走 ORDER BY random() LIMIT cap(PG 侧的采样,严格随机)。
    """
    cur.execute("SELECT count(*) AS c FROM peptide_enrichment WHERE tool = %s", (tool,))
    total = cur.fetchone()["c"]
    if total <= cap:
        cur.execute(
            "SELECT score FROM peptide_enrichment WHERE tool = %s AND score IS NOT NULL",
            (tool,),
        )
        rows = cur.fetchall()
        sampled = total
    else:
        cur.execute(
            "SELECT score FROM peptide_enrichment WHERE tool = %s AND score IS NOT NULL "
            "ORDER BY random() LIMIT %s",
            (tool, cap),
        )
        rows = cur.fetchall()
        sampled = cap
    arr = np.fromiter((r["score"] for r in rows), dtype=np.float32, count=len(rows))
    log.info("  %-11s total=%-9d sampled=%-9d null_removed_in_window=%d",
             tool, total, sampled, sampled - len(arr))
    return arr


def plot_one(ax, scores: np.ndarray, tool: str, total: int, sampled: int) -> dict:
    """画单图 + 返回该工具的统计摘要。"""
    s = scores[~np.isnan(scores)]
    n = len(s)
    if n == 0:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(tool)
        return {"tool": tool, "n": 0, "mean": None, "median": None,
                "std": None, "p1": None, "p99": None, "min": None, "max": None}

    # 直方图(密度归一)+ KDE
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-9:  # 退化情况
        hi = lo + 1e-9
    bins = np.linspace(lo, hi, HIST_BINS + 1)

    ax.hist(s, bins=bins, density=True, color="#4C78A8", alpha=0.55,
            edgecolor="white", linewidth=0.3, label="histogram")

    # KDE:对极大样本 gaussian_kde 会 O(n^2),这里已经降到 50w,需 bandwidth 略放宽
    try:
        if n > 50_000:
            # 子采样加速 KDE
            sub = np.random.default_rng(0).choice(s, size=50_000, replace=False)
        else:
            sub = s
        kde = gaussian_kde(sub, bw_method="scott")
        xs = np.linspace(lo, hi, KDE_POINTS)
        ys = kde(xs)
        ax.plot(xs, ys, color="#E45756", linewidth=1.6, label="KDE")
    except Exception as e:
        log.warning("  KDE failed for %s: %s", tool, e)

    # 统计
    p1, p99 = np.percentile(s, [1, 99])
    mean = float(s.mean())
    median = float(np.median(s))
    std = float(s.std(ddof=0))
    stats = {
        "tool": tool,
        "total_rows": total,
        "sampled": sampled,
        "n": n,
        "mean": mean,
        "median": median,
        "std": std,
        "p1": float(p1),
        "p99": float(p99),
        "min": float(lo),
        "max": float(hi),
    }

    title = f"{tool}  n={n:,}"
    if sampled < total:
        title += f"  (sampled / {total:,})"
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("score")
    ax.set_ylabel("density")
    ax.set_xlim(lo, hi)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.text(0.98, 0.95,
            f"μ={mean:.3f}\nσ={std:.3f}\nmed={median:.3f}",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=8, family="monospace",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85, edgecolor="none"))
    return stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots", help="输出目录")
    p.add_argument("--dsn", default=DB_DSN)
    p.add_argument("--tools", nargs="*", default=None,
                   help="只画这些工具,默认全部(按 coverage 视图顺序)")
    p.add_argument("--sample-cap", type=int, default=SAMPLE_CAP)
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("output -> %s", out_dir)

    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.tools:
        tools = args.tools
    else:
        cur.execute("SELECT tool FROM v_peptide_enrichment_coverage ORDER BY coverage_pct DESC, tool")
        tools = [r["tool"] for r in cur.fetchall()]
    log.info("tools: %s", tools)

    data = {}
    totals = {}
    for t in tools:
        log.info("fetching %s ...", t)
        cur.execute("SELECT count(*) AS c FROM peptide_enrichment WHERE tool = %s", (t,))
        total = cur.fetchone()["c"]
        totals[t] = total
        arr = fetch_tool_scores(cur, t, args.sample_cap)
        data[t] = arr

    cur.close()
    conn.close()

    # 3x3 grid
    n_tools = len(tools)
    cols = 3
    rows = math.ceil(n_tools / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.0 * cols, 3.6 * rows))
    axes_flat = np.array(axes).flatten() if n_tools > 1 else np.array([axes])
    fig.suptitle("peptide_enrichment score distributions (per tool)",
                 fontsize=14, y=0.995)

    summary = []
    for i, t in enumerate(tools):
        st = plot_one(axes_flat[i], data[t], t,
                      total=totals[t], sampled=len(data[t]))
        summary.append(st)

    # 隐藏多余 subplot
    for j in range(len(tools), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.tight_layout(rect=[0, 0, 1, 0.98])
    out_png = out_dir / "score_distributions_grid.png"
    fig.savefig(out_png, dpi=140, bbox_inches="tight")
    log.info("wrote %s", out_png)

    # 单独每工具一张高清版: 写到 results/plots/<tool>/ 子文件夹
    for t in tools:
        tool_dir = out_dir / t
        tool_dir.mkdir(parents=True, exist_ok=True)
        fig1, ax1 = plt.subplots(figsize=(7, 4.5))
        st = plot_one(ax1, data[t], t, total=totals[t], sampled=len(data[t]))
        fig1.tight_layout()
        fig1.savefig(tool_dir / f"score_{t}.png", dpi=160, bbox_inches="tight")
        plt.close(fig1)
    plt.close(fig)
    log.info("wrote %d per-tool PNGs into %s/<tool>/", len(tools), out_dir)

    # 汇总 CSV
    csv_path = out_dir / "score_summary.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        for row in summary:
            w.writerow(row)
    log.info("wrote %s", csv_path)

    # 顺便存 raw 抽样到 npz(后续分析用,免 parquet 依赖)
    npz_path = out_dir / "score_samples.npz"
    np.savez_compressed(npz_path, **{t: data[t].astype(np.float32) for t in tools})
    log.info("wrote %s", npz_path)

    # 终端也打印一眼
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
