#!/usr/bin/env python3
"""
plot_algpred2.py — AlgPred2 在 2000 万肽库上的运行结果统计可视化

数据来源 (全部由 fetch_data.py 离线准备,无需 DB):
    analysis/algpred2/algpred2_full_scores.npz    score (20.2M float32) + seq_len (int16)
    analysis/algpred2/algpred2_labels.npy         label 字符串 ("Allergen" / "Non-Allergen")
    analysis/algpred2/algpred2_score_by_length.csv 按 length 全量聚合
    analysis/algpred2/algpred2_summary.json       关键数字
    results/plots/library/length_distribution.csv  库长度分布
    results/plots/cohen_d_table.csv               跨工具 Cohen's d

输出 (9 张图):
    01_score_histogram_overview.png        总体 score 直方图 (linear + log-Y)
    02_allergen_vs_non_distribution.png   Allergen vs Non-Allergen 分布对比 (violin/box/KDE)
    03_score_by_length_box.png            按长度的 score 分布 (box)
    04_allergen_rate_by_length.png        Allergen 占比随长度变化 + 阈值线
    05_score_ecdf.png                     全库 ECDF + Allergen / Non-Allergen ECDF
    06_threshold_curve.png                不同 score 阈值的 (被标为 Allergen 比例 / 计数)
    07_cohens_d_comparison.png            AlgPred2 vs 其他工具 Cohen's d
    08_score_quantile_heatmap.png         length × score quantile 热力图
    09_score_vs_length_density.png        score vs 长度 2D 密度 + 分位带
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.colors import LogNorm
from scipy.stats import gaussian_kde

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("plot-algpred2")

# ---------------------------------------------------------- paths & config
ROOT = Path("/home/lenovo/Projects/iGEM-platform")
DATA = ROOT / "analysis" / "algpred2"
GLOBAL = ROOT / "results" / "plots"
OUT = DATA                                # 直接写到本目录
OUT.mkdir(parents=True, exist_ok=True)

THRESHOLD = 0.3                           # AlgPred2 SDK 硬编码

# 全局 rc
plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 140,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "font.family": "DejaVu Sans",
})

# 配色
COLOR_BG     = "#4C78A8"   # 全库 / 背景
COLOR_ALG    = "#E45756"   # Allergen
COLOR_NON    = "#54A24B"   # Non-Allergen
COLOR_THR    = "black"


# ===================================================================== helpers
def kde_curve(s: np.ndarray, lo: float, hi: float, n: int = 400, bw=None):
    """scipy KDE 包络,极大样本自动子采样到 50k 以保证 O(n²) 安全。"""
    if len(s) > 50_000:
        s = np.random.default_rng(0).choice(s, 50_000, replace=False)
    kde = gaussian_kde(s, bw_method=bw or "scott")
    xs = np.linspace(lo, hi, n)
    return xs, kde(xs)


def bucket_of(L: int) -> str:
    """把肽长度分成 5 个语义区间。"""
    if L < 8:   return "tiny (3-7)"
    if L < 14:  return "short (8-13)"
    if L < 20:  return "medium (14-19)"
    if L < 26:  return "long (20-25)"
    return "xlong (26-30)"


def load_all():
    """读所有离线数据。"""
    z = np.load(DATA / "algpred2_full_scores.npz")
    score = z["score"].astype(np.float32)
    seq_len = z["seq_len"].astype(np.int16)
    label_u8 = np.load(DATA / "algpred2_labels.npy")           # uint8 编码:0=Non,1=Allergen
    label = np.where(label_u8 == 1, "Allergen",
             np.where(label_u8 == 0, "Non-Allergen", "Unknown"))

    by_len = []
    with (DATA / "algpred2_score_by_length.csv").open() as f:
        for row in csv.DictReader(f):
            by_len.append({k: (int(v) if k in ("seq_len","n","n_allergen") else float(v))
                           for k, v in row.items()})

    lib_dist = []
    with (GLOBAL / "library" / "length_distribution.csv").open() as f:
        for row in csv.DictReader(f):
            lib_dist.append({"length": int(row["length"]),
                             "rows":   int(row["rows"]),
                             "pct":    float(row["pct"])})

    with (GLOBAL / "cohen_d_table.csv").open() as f:
        cohens = list(csv.DictReader(f))

    with (DATA / "algpred2_summary.json").open() as f:
        summary = json.load(f)

    return score, seq_len, label, by_len, lib_dist, cohens, summary


# ====================================================== fig 1  总体分布概览
def fig1_score_overview(score):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    bins = np.linspace(0, 1, 81)
    ax1.hist(score, bins=bins, density=True, color=COLOR_BG, alpha=0.6,
             edgecolor="white", linewidth=0.3,
             label=f"all peptides (n={score.size:,})")
    xs, ys = kde_curve(score, 0, 1)
    ax1.plot(xs, ys, color=COLOR_BG, lw=1.8)
    ax1.axvline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.4,
                label=f"SDK threshold = {THRESHOLD}")
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("AlgPred2 score")
    ax1.set_ylabel("density")
    ax1.set_title("Linear scale (main mass visible)")
    ax1.legend(loc="upper right")

    ax2.hist(score, bins=bins, color=COLOR_BG, alpha=0.6,
             edgecolor="white", linewidth=0.3)
    ax2.axvline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.4,
                label=f"threshold = {THRESHOLD}")
    ax2.set_yscale("log")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("AlgPred2 score")
    ax2.set_ylabel("count (log)")
    ax2.set_title("Log-Y (right tail visible)")
    ax2.legend(loc="upper right")

    n_above = int((score >= THRESHOLD).sum())
    n_above_5 = int((score >= 0.5).sum())
    fig.suptitle(
        f"AlgPred2 score distribution on {score.size:,} peptides\n"
        f"μ={score.mean():.4f},  σ={score.std(ddof=0):.4f},  median={np.median(score):.3f}   "
        f"|   ≥ {THRESHOLD}: {n_above:,} ({100*n_above/score.size:.2f}%)   "
        f"≥ 0.5: {n_above_5:,} ({100*n_above_5/score.size:.2f}%)",
        fontsize=13, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(OUT / "01_score_histogram_overview.png")
    plt.close(fig)
    log.info("  -> 01_score_histogram_overview.png")


# ============================== fig 2  Allergen vs Non-Allergen 分布对比 (KDE + box)
def fig2_allergen_vs_non(score, label):
    pos = score[label == "Allergen"]
    neg = score[label == "Non-Allergen"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))

    # --- KDE(密度对比)
    lo, hi = 0.0, 1.0
    xs_p, ys_p = kde_curve(pos, lo, hi)
    xs_n, ys_n = kde_curve(neg, lo, hi)
    ax1.fill_between(xs_p, ys_p, color=COLOR_ALG, alpha=0.35,
                     label=f"Allergen (n={pos.size:,})")
    ax1.plot(xs_p, ys_p, color=COLOR_ALG, lw=1.8)
    ax1.fill_between(xs_n, ys_n, color=COLOR_NON, alpha=0.35,
                     label=f"Non-Allergen (n={neg.size:,})")
    ax1.plot(xs_n, ys_n, color=COLOR_NON, lw=1.8)
    ax1.axvline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.4, label=f"threshold = {THRESHOLD}")
    ax1.set_xlim(lo, hi)
    ax1.set_xlabel("AlgPred2 score")
    ax1.set_ylabel("density")
    ax1.set_title("KDE — Allergen vs Non-Allergen")
    ax1.legend(loc="upper right")

    # --- box plot (只显示主质量区)
    bp = ax2.boxplot(
        [neg, pos], tick_labels=["Non-Allergen", "Allergen"],
        patch_artist=True, widths=0.55,
        flierprops=dict(marker=".", markersize=2.5, alpha=0.4, markeredgecolor="none"),
        medianprops=dict(color="black", lw=1.6),
        whiskerprops=dict(color="#666"),
        capprops=dict(color="#666"),
    )
    for patch, color in zip(bp["boxes"], [COLOR_NON, COLOR_ALG]):
        patch.set_facecolor(color); patch.set_alpha(0.6); patch.set_edgecolor("#333")
    ax2.axhline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.4, label=f"threshold = {THRESHOLD}")
    ax2.set_ylim(0, 0.8)
    ax2.set_ylabel("AlgPred2 score")
    ax2.set_title("Box — main mass (outliers cropped)")
    ax2.legend(loc="upper left")
    ax2.grid(True, axis="y", alpha=0.3)

    fig.suptitle(
        f"Allergen vs Non-Allergen score distributions\n"
        f"Allergen μ={pos.mean():.3f}  Non-Allergen μ={neg.mean():.3f}   "
        f"Δμ = {pos.mean()-neg.mean():.3f}",
        fontsize=13, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(OUT / "02_allergen_vs_non_distribution.png")
    plt.close(fig)
    log.info("  -> 02_allergen_vs_non_distribution.png")


# ======================================== fig 3  按 length 区间的 score 分布
def fig3_score_by_length(score, seq_len):
    buckets = ["tiny (3-7)", "short (8-13)", "medium (14-19)", "long (20-25)", "xlong (26-30)"]
    groups = [score[(seq_len >= lo) & (seq_len <= hi)]
              for (lo, hi) in [(3, 7), (8, 13), (14, 19), (20, 25), (26, 30)]]

    fig, ax = plt.subplots(figsize=(12, 5.5))
    bp = ax.boxplot(
        groups, tick_labels=[f"{b}\nn={len(g):,}" for b, g in zip(buckets, groups)],
        patch_artist=True, widths=0.55,
        flierprops=dict(marker=".", markersize=2, alpha=0.3, markeredgecolor="none"),
        medianprops=dict(color="black", lw=1.6),
        whiskerprops=dict(color="#666"),
        capprops=dict(color="#666"),
    )
    colors = ["#9ECAE1", "#6BAED6", "#4292C6", "#2171B5", "#08519C"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color); patch.set_alpha(0.7); patch.set_edgecolor("#222")

    ax.axhline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.4, label=f"threshold = {THRESHOLD}")
    ax.set_ylim(0, 0.85)
    ax.set_ylabel("AlgPred2 score")
    ax.set_xlabel("peptide length bucket")
    ax.set_title("Score distribution by peptide-length bucket")
    ax.legend(loc="upper right")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "03_score_by_length_box.png")
    plt.close(fig)
    log.info("  -> 03_score_by_length_box.png")


# ============================== fig 4  Allergen 占比随 length 的变化(带阈值)
def fig4_allergen_rate_by_length(by_len, lib_dist):
    Ls = np.array([r["seq_len"] for r in by_len])
    rates = np.array([r["allergen_rate"] for r in by_len])     # %
    nrows = np.array([r["n"] for r in by_len])

    # 库分布(3..30)与 by_len(1..30)对齐:只保留 3..30
    lib_pct_map = {r["length"]: r["pct"] for r in lib_dist}
    lib_pct = np.array([lib_pct_map.get(int(L), 0.0) for L in Ls if 3 <= int(L) <= 30])
    Ls = np.array([L for L in Ls if 3 <= int(L) <= 30])
    rates = np.array([rates[i] for i, L in enumerate(Ls)])
    nrows = np.array([nrows[i] for i, L in enumerate(Ls)])

    fig, ax1 = plt.subplots(figsize=(11, 5.5))

    # 主轴:allergen_rate
    bars = ax1.bar(Ls, rates, color=COLOR_ALG, alpha=0.78, edgecolor="white", linewidth=0.5,
                   label="Allergen rate (%)")
    for b, r, n in zip(bars, rates, nrows):
        ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5,
                 f"{r:.1f}%\n({n/1e3:.0f}k)" if n >= 1000 else f"{r:.1f}%\n({n})",
                 ha="center", va="bottom", fontsize=7, color="#7a1b1d")

    ax1.axhline(THRESHOLD * 100, color=COLOR_THR, ls="--", lw=1.4)
    ax1.set_xlabel("peptide length (aa)")
    ax1.set_ylabel("Allergen rate (%)", color=COLOR_ALG)
    ax1.tick_params(axis="y", labelcolor=COLOR_ALG)
    ax1.set_xticks(Ls)
    ax1.set_ylim(0, 115)              # 给顶部标签留 15% 空间
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))

    # 副轴:库长度分布(归一)
    ax2 = ax1.twinx()
    ax2.plot(Ls, lib_pct, color=COLOR_BG, marker="o", lw=1.6,
             label="library share (%)")
    ax2.set_ylabel("library share (%)", color=COLOR_BG)
    ax2.tick_params(axis="y", labelcolor=COLOR_BG)
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))

    ax1.set_title(
        f"Allergen rate by peptide length  (overall = {sum(int(r['n_allergen']) for r in by_len)/sum(int(r['n']) for r in by_len)*100:.2f}%)\n"
        "short peptides are predicted Allergen far more often than long ones",
        fontsize=12,
    )
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", bbox_to_anchor=(0.02, 0.98))
    # 阈值文字直接放在线上,避开右上角图例
    ax1.text(Ls.max() + 0.3, THRESHOLD * 100, f"threshold = {THRESHOLD}",
             color=COLOR_THR, fontsize=9, ha="left", va="center")
    ax1.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "04_allergen_rate_by_length.png")
    plt.close(fig)
    log.info("  -> 04_allergen_rate_by_length.png")


# ====================================================== fig 5  ECDF (全库 + 二分类)
def fig5_score_ecdf(score, label):
    pos = score[label == "Allergen"]
    neg = score[label == "Non-Allergen"]

    fig, ax = plt.subplots(figsize=(11, 5.5))

    def ecdf(s, n_max=None):
        s = np.sort(s)
        y = np.arange(1, s.size + 1) / s.size
        if n_max is not None and s.size > n_max:
            idx = np.linspace(0, s.size - 1, n_max).astype(int)
            s, y = s[idx], y[idx]
        return s, y

    # 为避免曲线太长采样展示
    xs_all, ys_all = ecdf(score, n_max=2000)
    xs_pos, ys_pos = ecdf(pos,    n_max=2000)
    xs_neg, ys_neg = ecdf(neg,    n_max=2000)

    ax.plot(xs_all, ys_all, color=COLOR_BG, lw=2.2,
            label=f"all (n={score.size:,})")
    ax.plot(xs_pos, ys_pos, color=COLOR_ALG, lw=1.6, ls="--",
            label=f"Allergen (n={pos.size:,})")
    ax.plot(xs_neg, ys_neg, color=COLOR_NON, lw=1.6, ls="--",
            label=f"Non-Allergen (n={neg.size:,})")

    # 阈值线
    for thr, c in [(0.3, "black"), (0.4, "#444"), (0.5, "#666")]:
        ax.axvline(thr, color=c, ls=":", lw=1.0, alpha=0.7)

    ax.set_xlabel("AlgPred2 score")
    ax.set_ylabel("ECDF (cumulative fraction)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.0)
    ax.set_title("Empirical CDF — score distribution")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")

    # 标注关键阈值
    for thr in [0.3, 0.4, 0.5]:
        frac = float((score < thr).mean())
        ax.annotate(f"P(score<{thr})={frac:.3f}",
                    xy=(thr, frac), xytext=(thr + 0.04, frac - 0.06),
                    fontsize=8, color="#222",
                    arrowprops=dict(arrowstyle="->", color="#888", lw=0.6))

    fig.tight_layout()
    fig.savefig(OUT / "05_score_ecdf.png")
    plt.close(fig)
    log.info("  -> 05_score_ecdf.png")


# ====================================================== fig 6  阈值-数量曲线
def fig6_threshold_curve(score):
    thrs = np.linspace(0.10, 0.80, 71)
    counts = np.array([(score >= t).sum() for t in thrs])
    pct = counts / score.size * 100.0

    fig, ax1 = plt.subplots(figsize=(11, 5.5))
    ax1.plot(thrs, counts, color=COLOR_BG, lw=2.0, marker="o", markersize=3.5,
             label="# peptides ≥ threshold")
    ax1.set_xlabel("score threshold")
    ax1.set_ylabel("count", color=COLOR_BG)
    ax1.tick_params(axis="y", labelcolor=COLOR_BG)
    ax1.set_yscale("log")
    ax1.grid(True, alpha=0.3, which="both")

    ax2 = ax1.twinx()
    ax2.plot(thrs, pct, color=COLOR_ALG, lw=2.0, marker="o", markersize=3.5,
             label="% library ≥ threshold")
    ax2.set_ylabel("% of library", color=COLOR_ALG)
    ax2.tick_params(axis="y", labelcolor=COLOR_ALG)
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=2))

    # 阈值 0.3 / 0.5 标注
    for thr in [0.3, 0.5]:
        idx = int(np.argmin(np.abs(thrs - thr)))
        c, p = int(counts[idx]), float(pct[idx])
        ax1.scatter([thr], [c], s=110, color="black", zorder=5)
        ax1.annotate(f"@ {thr}\n{c:,}  ({p:.2f}%)",
                     xy=(thr, c), xytext=(thr + 0.04, c * 1.3),
                     fontsize=8, family="monospace",
                     arrowprops=dict(arrowstyle="->", color="#444", lw=0.7))

    ax1.set_title("Threshold sweep — how many peptides are kept")
    ax1.set_xlim(0.10, 0.80)
    fig.tight_layout()
    fig.savefig(OUT / "06_threshold_curve.png")
    plt.close(fig)
    log.info("  -> 06_threshold_curve.png")


# ============================== fig 7  AlgPred2 Cohen's d vs 其他工具
def fig7_cohens_d(cohens):
    """algpred2 高亮,其他工具灰色背景。"""
    # 排序:abs_d 大→小
    rows = []
    for r in cohens:
        try:
            d = float(r["cohen_d"])
            ad = float(r["abs_d"])
        except (TypeError, ValueError):
            continue
        rows.append({**r, "cohen_d_f": d, "abs_d_f": ad})
    rows.sort(key=lambda r: r["abs_d_f"], reverse=True)
    rows_dict = {r["tool"]: r["cohen_d_f"] for r in rows}

    names = [f"{r['tool']}\n({r['config'].split(' vs ')[0]})" for r in rows]
    ds = [r["cohen_d_f"] for r in rows]
    colors = ["#E45756" if n.startswith("algpred2") else "#B0B7BF" for n in names]
    edgecolors = ["#7a1b1d" if c == "#E45756" else "#666" for c in colors]

    fig, ax = plt.subplots(figsize=(11, 6))
    ypos = np.arange(len(names))
    bars = ax.barh(ypos, ds, color=colors, edgecolor=edgecolors, linewidth=0.6)
    for b, r in zip(bars, rows):
        x = r["cohen_d_f"]
        ax.text(x + (0.05 if x >= 0 else -0.05),
                b.get_y() + b.get_height() / 2,
                f"d={x:+.3f}  [{r['strength']}]",
                va="center", ha="left" if x >= 0 else "right",
                fontsize=8, family="monospace")

    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=9)
    ax.invert_yaxis()
    ax.axvline(0, color="black", linewidth=0.8)
    for v in [-0.8, -0.5, -0.2, 0.2, 0.5, 0.8]:
        ax.axvline(v, color="grey", linestyle=":", alpha=0.35)
    ax.set_xlabel("Cohen's d  (positive = pos label has higher score)")
    ax.set_xlim(-2.0, max(6.0, max(abs(d) for d in ds) + 0.5))
    ax.set_title(
        f"Cohen's d per tool — AlgPred2 highlighted  "
        f"(algpred2 d={rows_dict['algpred2']:+.2f}; max={rows[0]['cohen_d_f']:+.2f} = {rows[0]['tool']})",
        fontsize=12,
    )
    ax.grid(True, axis="x", alpha=0.3)
    ax.text(0.99, 0.02,
            "Cohen's d reference:\n"
            " 0.2  weak\n 0.5  medium\n 0.8  strong\n 1.2+ very strong",
            transform=ax.transAxes, va="bottom", ha="right",
            fontsize=8, family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF3CD",
                      alpha=0.85, edgecolor="none"))
    fig.tight_layout()
    fig.savefig(OUT / "07_cohens_d_comparison.png")
    plt.close(fig)
    log.info("  -> 07_cohens_d_comparison.png")


# ============================== fig 8  length × score quantile 热力图
def fig8_score_quantile_heatmap(score, seq_len):
    """行=peptide length,列=score quantile bin,单元格=peptide 数量 (log)。"""
    Ls_unique = np.arange(3, 31)              # 3..30
    q_edges = np.linspace(0.0, 1.0, 21)        # 20 bins
    q_centers = (q_edges[:-1] + q_edges[1:]) / 2

    grid = np.zeros((len(Ls_unique), len(q_centers)), dtype=np.int64)
    for i, L in enumerate(Ls_unique):
        m = seq_len == L
        if not m.any():
            continue
        s = score[m]
        # 用 qcut 风格:用 percentile cut 计数
        qs = np.quantile(s, q_edges)
        qs[0] -= 1e-6; qs[-1] += 1e-6
        grid[i] = np.histogram(s, bins=qs)[0]

    fig, ax = plt.subplots(figsize=(11, 6))
    im = ax.imshow(grid, aspect="auto", cmap="viridis", origin="lower",
                   norm=LogNorm(vmin=max(1, grid.min()), vmax=grid.max()),
                   extent=[0, len(q_centers), 2.5, 30.5])
    plt.colorbar(im, ax=ax, label="count (log scale)")
    ax.set_yticks(Ls_unique)
    ax.set_xticks(np.arange(len(q_centers)) + 0.5)
    ax.set_xticklabels([f"{q:.2f}" for q in q_centers], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("score quantile bin (0=low, 1=high)")
    ax.set_ylabel("peptide length (aa)")
    ax.set_title("Length × Score-quantile heatmap (count, log scale)\n"
                 "each row sums to the # peptides of that length; rows 20-30 dominate because the library is 94% long peptides",
                 fontsize=11)
    # threshold 0.3 ≈ 落在 score 分布的 ~35% 分位附近
    thr_q = float((score < 0.3).mean())
    ax.axvline(thr_q * 20, color="#FF4136", lw=1.4, alpha=0.85)
    ax.text(thr_q * 20, 30.8, f" threshold=0.3  (≈ q={thr_q:.2f})",
            color="#FF4136", fontsize=8, ha="left", va="bottom")
    fig.tight_layout()
    fig.savefig(OUT / "08_score_quantile_heatmap.png")
    plt.close(fig)
    log.info("  -> 08_score_quantile_heatmap.png")


# ======================================== fig 9  score vs length 2D 密度
def fig9_score_vs_length_density(score, seq_len):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9),
                                   gridspec_kw={"height_ratios": [1, 2]},
                                   sharex=True)

    # --- 上: 分位数带
    Ls = np.arange(3, 31)
    q10, q25, q50, q75, q90 = [], [], [], [], []
    for L in Ls:
        s = score[seq_len == L]
        if len(s) == 0:
            q10.append(np.nan); q25.append(np.nan); q50.append(np.nan)
            q75.append(np.nan); q90.append(np.nan)
            continue
        q10.append(np.quantile(s, 0.10))
        q25.append(np.quantile(s, 0.25))
        q50.append(np.quantile(s, 0.50))
        q75.append(np.quantile(s, 0.75))
        q90.append(np.quantile(s, 0.90))
    q10 = np.array(q10); q25 = np.array(q25); q50 = np.array(q50)
    q75 = np.array(q75); q90 = np.array(q90)

    ax1.fill_between(Ls, q10, q90, color=COLOR_BG, alpha=0.15, label="10-90% band")
    ax1.fill_between(Ls, q25, q75, color=COLOR_BG, alpha=0.30, label="25-75% band")
    ax1.plot(Ls, q50, color=COLOR_BG, lw=2.0, label="median")
    ax1.axhline(THRESHOLD, color=COLOR_THR, ls="--", lw=1.2, label=f"threshold = {THRESHOLD}")
    ax1.set_xlim(2.5, 30.5)
    ax1.set_ylabel("AlgPred2 score")
    ax1.set_title("Score percentile bands by peptide length")
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.3)

    # --- 下: 2D hexbin (密度)
    hb = ax2.hexbin(seq_len, score, gridsize=(28, 60),
                    bins="log", cmap="viridis", mincnt=1)
    cb = plt.colorbar(hb, ax=ax2, label="density (log)")
    ax2.axhline(THRESHOLD, color="white", ls="--", lw=1.4)
    ax2.text(30.6, THRESHOLD + 0.005, " threshold 0.3",
             color="white", fontsize=9, ha="right", va="bottom")
    ax2.set_xlim(2.5, 30.5)
    ax2.set_ylim(0, 1.0)
    ax2.set_xlabel("peptide length (aa)")
    ax2.set_ylabel("AlgPred2 score")
    ax2.set_title("Score × length 2D density (log color scale)")

    fig.tight_layout()
    fig.savefig(OUT / "09_score_vs_length_density.png")
    plt.close(fig)
    log.info("  -> 09_score_vs_length_density.png")


# ====================================================================== main
def main():
    log.info("loading offline data ...")
    score, seq_len, label, by_len, lib_dist, cohens, summary = load_all()
    log.info("score n=%d  label unique=%s  seq_len unique=%d",
             score.size, np.unique(label), len(np.unique(seq_len)))

    fig1_score_overview(score)
    fig2_allergen_vs_non(score, label)
    fig3_score_by_length(score, seq_len)
    fig4_allergen_rate_by_length(by_len, lib_dist)
    fig5_score_ecdf(score, label)
    fig6_threshold_curve(score)
    fig7_cohens_d(cohens)
    fig8_score_quantile_heatmap(score, seq_len)
    fig9_score_vs_length_density(score, seq_len)

    log.info("all 9 figures written to %s", OUT)


if __name__ == "__main__":
    sys.exit(main())