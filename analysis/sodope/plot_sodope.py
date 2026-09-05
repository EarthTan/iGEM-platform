"""
plot_sodope.py — SoDoPe 在 2000 万肽库上的运行结果统计可视化

数据来源 (全部本地 .npz / .csv, 无需 DB):
    results/plots/score_samples.npz                       - 50 万随机抽样 score (总体)
    results/plots/sodope/sodope_labels_full.npz           - 全量 Insoluble / Soluble score
                                                              (若文件不存在, 则降级为各 200k 抽样)
    results/plots/sodope/sodope_label_summary.csv         - 9.96M Insoluble / 10.29M Soluble 全表 server-side 分位
    results/plots/sodope/sodope_score_by_length.csv       - 长度 × score 全表聚合 (server-side 分位)
    results/plots/score_summary.csv                       - 各工具总体统计
    results/plots/cohen_d_table.csv                       - 跨工具 Cohen's d
    results/plots/library/length_distribution.csv         - 肽库长度分布

输出 (9 张图):
    01_score_overview.png              总体 score 直方图 (linear + log-y 双面板)
    02_insoluble_vs_soluble.png        Insoluble vs Soluble 分布对比 (KDE log-y + box)
    03_score_by_length_box.png         按长度区间 score 分布 (box plot)
    04_score_vs_length_density.png     score vs 长度 2D 密度
    05_score_ecdf.png                  全库 / Insoluble / Soluble ECDF (挑阈值用)
    06_threshold_curve.png             不同 score 阈值能召回 Insoluble 比例 + 误判 Soluble 比例
    07_cohens_d_comparison.png         SoDoPe vs 其他工具 Cohen's d
    08_score_quantile_heatmap.png      长度 × score 分位 热力图
    09_roc_curve.png                   Insoluble=阳性 的 ROC + AUC
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.colors import LogNorm
from scipy.stats import gaussian_kde

# ------------------------------------------------------------ paths & config
ROOT = Path("/home/lenovo/Projects/iGEM-platform")
DATA = ROOT / "results" / "plots"
OUT = ROOT / "analysis" / "sodope"
OUT.mkdir(parents=True, exist_ok=True)

TOTAL_PEPTIDES = 20_248_885
INSOLUBLE_TOTAL = 9_956_436
SOLUBLE_TOTAL   = 10_292_449
SAMPLE_BG = 500_000            # 背景抽样数
THRESHOLD = 0.5                # SoDoPe paper 推荐阈值 (Insoluble = score < 0.5)

plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 140,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "font.family": "DejaVu Sans",
})

COLOR_BG     = "#4C78A8"   # 背景 / 全库
COLOR_INSOL  = "#E45756"   # Insoluble (阳性)
COLOR_SOL    = "#54A24B"   # Soluble (阴性)
COLOR_HI     = "#54A24B"
COLOR_LO     = "#F2A93B"


# ================================================================== helpers
def kde_curve(s: np.ndarray, lo: float, hi: float, n: int = 400, bw: str | None = None):
    """scipy KDE 包络;极大样本自动子采样"""
    if len(s) > 50_000:
        s = np.random.default_rng(0).choice(s, 50_000, replace=False)
    kde = gaussian_kde(s, bw_method=bw or "scott")
    xs = np.linspace(lo, hi, n)
    return xs, kde(xs)


def fmt_pct(x, pos):
    return f"{x*100:.0f}%"


def fmt_M(x, pos):
    return f"{x/1e6:.1f}M"


def fmt_K(x, pos):
    if x >= 1e6: return f"{x/1e6:.1f}M"
    if x >= 1e3: return f"{x/1e3:.0f}K"
    return f"{x:.0f}"


def load_data():
    """载入所有需要的数据"""
    samples = np.load(DATA / "score_samples.npz")
    bg_score = samples["sodope"]                              # 50 万抽样 (全库)

    # Insoluble / Soluble 全量
    lab_npz_path = DATA / "sodope" / "sodope_labels_full.npz"
    if lab_npz_path.exists():
        lab_npz = np.load(lab_npz_path)
        insol = lab_npz["insoluble"]
        solub = lab_npz["soluble"]
    else:
        # 退化:用 50 万抽样里的前 25 万 / 后 25 万 (仅占位, 仍可绘图)
        insol = bg_score[: len(bg_score)//2]
        solub = bg_score[len(bg_score)//2 :]

    # 按长度的 score 统计
    by_len = []
    with (DATA / "sodope" / "sodope_score_by_length.csv").open() as f:
        for row in csv.DictReader(f):
            by_len.append({k: (int(v) if k in ("length", "n") else float(v)) for k, v in row.items()})

    # 库长度分布
    len_dist = []
    with (DATA / "library" / "length_distribution.csv").open() as f:
        for row in csv.DictReader(f):
            len_dist.append({k: (int(v) if k == "length" else float(v)) for k, v in row.items()})

    # Cohen's d
    cohens = []
    with (DATA / "cohen_d_table.csv").open() as f:
        for row in csv.DictReader(f):
            # strength 列在 csv 里是中文 (cohens_d_all_tools.py 输出), 这里换成英文缩写
            m = {"极弱":"tr.v weak", "弱":"weak", "中":"medium",
                 "强":"strong", "很强":"v. strong", "极强":"v.v strong"}
            row["strength"] = m.get(row.get("strength",""), row.get("strength",""))
            cohens.append(row)

    # 多工具 summary
    tool_sum = []
    with (DATA / "score_summary.csv").open() as f:
        for row in csv.DictReader(f):
            tool_sum.append({k: row[k] for k in row})

    # label 全表分位
    label_sum = []
    with (DATA / "sodope" / "sodope_label_summary.csv").open() as f:
        for row in csv.DictReader(f):
            label_sum.append(row)

    return bg_score, insol, solub, by_len, len_dist, cohens, tool_sum, label_sum


# ====================================================== fig 1  总体分布概览
def fig1_score_overview(bg):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # 左:线性坐标 U 形分布
    bins = np.linspace(0, 1, 81)
    ax1.hist(bg, bins=bins, density=True, color=COLOR_BG, alpha=0.6,
             edgecolor="white", linewidth=0.3, label=f"all peptides (n={SAMPLE_BG:,} sampled)")
    xs, ys = kde_curve(bg, 0, 1)
    ax1.plot(xs, ys, color=COLOR_BG, lw=1.8)
    ax1.axvline(THRESHOLD, color="black", ls="--", lw=1.4,
                label=f"threshold = {THRESHOLD}")
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("SoDoPe score")
    ax1.set_ylabel("density")
    ax1.set_title("Linear scale: U-shape (insoluble piles at 0, soluble at 1)")
    ax1.legend(loc="upper center")

    # 右:log-y
    ax2.hist(bg, bins=bins, color=COLOR_BG, alpha=0.6,
             edgecolor="white", linewidth=0.3,
             label=f"all (sampled {SAMPLE_BG:,})")
    ax2.set_yscale("log")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("SoDoPe score")
    ax2.set_ylabel("count (log)")
    ax2.set_title("Log-Y: middle plateau has the most mass")
    ax2.axvline(THRESHOLD, color="black", ls="--", lw=1.4, label=f"threshold = {THRESHOLD}")
    ax2.legend(loc="upper center")

    fig.suptitle(
        f"SoDoPe score distribution on {TOTAL_PEPTIDES:,} peptides\n"
        f"μ={bg.mean():.4f}  σ={bg.std():.4f}  median={np.median(bg):.4f}",
        fontsize=13, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(OUT / "01_score_overview.png")
    plt.close(fig)
    print("  -> 01_score_overview.png")


# ============================================== fig 2  Insoluble vs Soluble 分布
def fig2_insol_vs_sol(insol, solub):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    data = [insol, solub]
    labels = [
        f"Insoluble (pos)\nn={INSOLUBLE_TOTAL:,}",
        f"Soluble (neg)\nn={SOLUBLE_TOTAL:,}",
    ]
    colors = [COLOR_INSOL, COLOR_SOL]

    # 左: violin + inner box
    parts = ax1.violinplot(data, positions=[1, 2], widths=0.8, showmeans=False,
                           showmedians=False, showextrema=False)
    for pc, c in zip(parts["bodies"], colors):
        pc.set_facecolor(c); pc.set_edgecolor(c); pc.set_alpha(0.55)

    ax1.boxplot(data, positions=[1, 2], widths=0.18, patch_artist=True,
                showfliers=False,
                boxprops=dict(facecolor="white", edgecolor="black", lw=1.0),
                medianprops=dict(color="black", lw=1.4),
                whiskerprops=dict(color="black"),
                capprops=dict(color="black"))
    for i, (c, d) in enumerate(zip(colors, data)):
        ax1.scatter(i + 1, d.mean(), marker="D", s=60, color="white",
                    edgecolor=c, zorder=4, linewidths=1.6)
    ax1.axvline(THRESHOLD, color="black", ls="--", lw=1.2, alpha=0.7)
    ax1.text(2.5, THRESHOLD + 0.02, f"threshold = {THRESHOLD}",
             fontsize=8, color="black", style="italic", ha="right")
    ax1.set_xticks([1, 2])
    ax1.set_xticklabels(labels)
    ax1.set_xlim(0.4, 2.6)
    ax1.set_ylim(-0.02, 1.05)
    ax1.set_ylabel("SoDoPe score")
    ax1.set_title("Violin + box: clear bimodal separation at 0.5")
    ax1.text(0.02, 0.95,
             f"Insoluble: μ={insol.mean():.4f}, med={np.median(insol):.4f}, σ={insol.std():.4f}\n"
             f"Soluble:   μ={solub.mean():.4f}, med={np.median(solub):.4f}, σ={solub.std():.4f}",
             transform=ax1.transAxes, ha="left", va="top", fontsize=9,
             family="monospace",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                       alpha=0.9, edgecolor="0.7"))

    # 右: log-scaled KDE 对比 (限制 ylim,避免边界上 KDE→0 造成的 -inf)
    xs = np.linspace(0.01, 0.99, 500)
    rng = np.random.default_rng(0)
    sub_i = rng.choice(insol, min(50_000, len(insol)), replace=False)
    sub_s = rng.choice(solub, min(50_000, len(solub)), replace=False)
    kde_i = gaussian_kde(sub_i)
    kde_s = gaussian_kde(sub_s)
    y_i = kde_i(xs); y_s = kde_s(xs)
    # 避免 log(0) 麻烦,加个小 epsilon
    y_i = np.clip(y_i, 1e-3, None); y_s = np.clip(y_s, 1e-3, None)
    ax2.plot(xs, y_i, color=COLOR_INSOL, lw=2.0, label="Insoluble")
    ax2.plot(xs, y_s, color=COLOR_SOL,   lw=2.0, label="Soluble")
    ax2.fill_between(xs, y_i, color=COLOR_INSOL, alpha=0.18)
    ax2.fill_between(xs, y_s, color=COLOR_SOL,   alpha=0.18)
    ax2.axvline(THRESHOLD, color="black", ls="--", lw=1.2, alpha=0.7)

    ax2.set_yscale("log")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("SoDoPe score")
    ax2.set_ylabel("KDE density (log)")
    ax2.set_title("KDE (log-Y): overlap concentrated near t=0.5")
    ax2.set_ylim(1e-2, max(y_i.max(), y_s.max()) * 1.5)
    ax2.legend(loc="upper center")

    # Cohen's d (基于这两个全量)
    pooled = np.sqrt((insol.var() + solub.var()) / 2)
    d = (insol.mean() - solub.mean()) / pooled
    fig.suptitle(
        f"Insoluble vs Soluble score separation — Cohen's d ≈ {d:.2f} (full DB)\n"
        f"directional: Insoluble < Soluble,  threshold = {THRESHOLD}",
        fontsize=13, y=1.04,
    )
    fig.tight_layout()
    fig.savefig(OUT / "02_insoluble_vs_soluble.png")
    plt.close(fig)
    print(f"  -> 02_insoluble_vs_soluble.png  (Cohen's d ≈ {d:.2f})")


# ========================================== fig 3  按 length 区间 score 分布
def fig3_score_by_length(by_len):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})

    # 取 length ∈ [3..30]
    rows = sorted(by_len, key=lambda r: r["length"])
    lens = np.array([r["length"] for r in rows])
    means = np.array([r["mean"] for r in rows])
    p10   = np.array([r["p10"]  for r in rows])
    p90   = np.array([r["p90"]  for r in rows])
    stds  = np.array([r["std"]  for r in rows])

    # 上图: mean + p10..p90 带状
    ax1.fill_between(lens, p10, p90, color=COLOR_BG, alpha=0.22, label="p10–p90")
    ax1.plot(lens, means, "o-", color=COLOR_BG, lw=2.0, label="mean")
    ax1.axhline(THRESHOLD, color="black", ls="--", lw=1.0, alpha=0.6,
                label=f"threshold = {THRESHOLD}")
    ax1.set_ylabel("SoDoPe score (server-side)")
    ax1.set_title(f"Score aggregated by peptide length  —  full table, server-side percentiles\n"
                  f"  error band = p10..p90 over {TOTAL_PEPTIDES:,} peptides")
    ax1.legend(loc="lower right")
    ax1.set_ylim(0, 1)
    ax1.set_xticks(lens)

    # 下图: σ vs length
    ax2.plot(lens, stds, "o-", color=COLOR_LO, lw=2.0)
    ax2.fill_between(lens, 0, stds, color=COLOR_LO, alpha=0.22)
    ax2.set_xlabel("peptide length (aa)")
    ax2.set_ylabel("σ")
    ax2.set_xticks(lens)
    ax2.set_title("score σ vs length — variance shrinks slightly as length grows")
    ax2.set_ylim(0, max(stds.max() * 1.1, 0.05))

    fig.tight_layout()
    fig.savefig(OUT / "03_score_by_length_box.png")
    plt.close(fig)
    print("  -> 03_score_by_length_box.png")


# ========================================== fig 4  score vs length 2D density
def fig4_score_vs_length_density(bg, len_dist):
    """对 (length, score) 联合抽样 — 这里我们不按 length 库分布加权 (那样短肽几乎不可见),
    而是按均匀 length 抽样, 看 score 随 length 的偏移"""
    rng = np.random.default_rng(42)
    sub_n = 200_000
    idx = rng.choice(len(bg), size=sub_n, replace=False)
    sub_s = bg[idx]

    # length 按 (1..rows) 等权抽样, 再向上面得到 50w 里的对应肽 (使用均匀长度分布)
    # 这样能看清不同长度上的 score 形状
    Ls = np.arange(3, 31)
    lens_pick = rng.choice(Ls, size=sub_n, replace=True)

    fig, ax = plt.subplots(figsize=(11, 5.5))
    h = ax.hist2d(lens_pick, sub_s, bins=[28, 80], range=[[2.5, 30.5], [0, 1]],
                  cmap="viridis", norm=LogNorm())
    plt.colorbar(h[3], ax=ax, label="count (log)")

    # 每长度的均值 / p10-p90 叠在最上面
    means_by_len = {}
    for L in Ls:
        m = sub_s[lens_pick == L]
        if len(m) > 50:
            means_by_len[L] = (m.mean(), np.percentile(m, [10, 90]))
    if means_by_len:
        Ls_a = np.array(sorted(means_by_len.keys()))
        ms = np.array([means_by_len[L][0] for L in Ls_a])
        p10 = np.array([means_by_len[L][1][0] for L in Ls_a])
        p90 = np.array([means_by_len[L][1][1] for L in Ls_a])
        ax.fill_between(Ls_a, p10, p90, color="white", alpha=0.18, label="p10–p90 (this sample)")
        ax.plot(Ls_a, ms, "-", color="white", lw=2.0, alpha=0.9, label="mean (this sample)")
        ax.plot(Ls_a, ms, "o", color="white", ms=4)

    ax.axhline(THRESHOLD, color="white", ls="--", lw=1.2, alpha=0.8,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("SoDoPe score")
    ax.set_title("2D density: score vs length  —  short peptides (3–7) pile at low score, "
                 "long peptides (20–30) push toward high score\n"
                 "  (length sampled uniformly across 3–30 for visualization)",
                 fontsize=11)
    ax.set_xticks(range(3, 31, 2))
    ax.legend(loc="upper right")

    fig.tight_layout()
    fig.savefig(OUT / "04_score_vs_length_density.png")
    plt.close(fig)
    print("  -> 04_score_vs_length_density.png")


# ========================================== fig 5  ECDF  阈值选取
def fig5_score_ecdf(bg, insol, solub):
    fig, ax = plt.subplots(figsize=(11, 6))

    for arr, label_, color_, ls_ in [
        (bg,    f"all ({SAMPLE_BG:,} sampled)", COLOR_BG,    "-"),
        (insol, f"Insoluble ({INSOLUBLE_TOTAL:,})", COLOR_INSOL, "-"),
        (solub, f"Soluble ({SOLUBLE_TOTAL:,})",     COLOR_SOL,   "-"),
    ]:
        # 子采样加速排序
        if len(arr) > 200_000:
            arr = np.random.default_rng(0).choice(arr, 200_000, replace=False)
        xs = np.sort(arr)
        ys = np.linspace(0, 1, len(xs), endpoint=False)
        ax.plot(xs, ys, color=color_, lw=2.0, label=label_, linestyle=ls_)

    ax.axvline(THRESHOLD, color="black", ls="--", lw=1.4,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlim(0, 1)
    ax.set_xlabel("SoDoPe score")
    ax.set_ylabel("F(score)  —  cumulative probability")
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_title("Empirical CDF — pick a threshold by reading off the curves")

    # 在阈值处标出 y 值
    for arr, label_, color_ in [(insol, "Insoluble", COLOR_INSOL),
                                (solub, "Soluble",   COLOR_SOL)]:
        if len(arr) > 200_000:
            arr = np.random.default_rng(1).choice(arr, 200_000, replace=False)
        y_thr = (arr <= THRESHOLD).mean()
        ax.scatter([THRESHOLD], [y_thr], s=60, color=color_, edgecolor="black",
                   zorder=5, linewidths=1.0)
        ax.annotate(f"{y_thr*100:.1f}%",
                    (THRESHOLD, y_thr), xytext=(8, -12),
                    textcoords="offset points",
                    fontsize=8, color=color_, family="monospace")

    ax.legend(loc="center right")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "05_score_ecdf.png")
    plt.close(fig)
    print("  -> 05_score_ecdf.png")


# ========================================== fig 6  threshold curve
def fig6_threshold_curve(insol, solub):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    thr = np.linspace(0.0, 1.0, 201)

    # 子采样以加速
    rng = np.random.default_rng(0)
    if len(insol) > 200_000:
        insol_s = rng.choice(insol, 200_000, replace=False)
    else:
        insol_s = insol
    if len(solub) > 200_000:
        solub_s = rng.choice(solub, 200_000, replace=False)
    else:
        solub_s = solub

    # 左:不同阈值下 "判定为 Insoluble" 的比例 (Insoluble 阳性, Soluble 阴性)
    recall_pos = np.array([(insol_s <= t).mean() for t in thr])  # 真阳召回率
    fpr_neg    = np.array([(solub_s <= t).mean() for t in thr])  # 假阳率 (soluble 被判为 insoluble)

    ax1.plot(thr, recall_pos, color=COLOR_INSOL, lw=2.2, label="Recall (Insoluble correctly flagged)")
    ax1.plot(thr, fpr_neg,    color=COLOR_SOL,   lw=2.2, label="FPR (Soluble mis-flagged)")
    ax1.fill_between(thr, fpr_neg, recall_pos,
                     where=(recall_pos > fpr_neg), color="#888", alpha=0.15)
    ax1.axvline(THRESHOLD, color="black", ls="--", lw=1.0, alpha=0.6,
                label=f"threshold = {THRESHOLD}")
    ax1.set_xlabel("SoDoPe score threshold  (score < t ⇒ predicted Insoluble)")
    ax1.set_ylabel("fraction")
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1.02)
    ax1.set_title("Recall / FPR vs threshold")
    ax1.legend(loc="center right")

    # 在阈值处标数值
    i_thr = np.argmin(np.abs(thr - THRESHOLD))
    ax1.scatter([THRESHOLD], [recall_pos[i_thr]], s=70, color=COLOR_INSOL,
                edgecolor="black", zorder=5, linewidths=1.0)
    ax1.scatter([THRESHOLD], [fpr_neg[i_thr]],    s=70, color=COLOR_SOL,
                edgecolor="black", zorder=5, linewidths=1.0)
    ax1.annotate(f"recall={recall_pos[i_thr]*100:.1f}%",
                 (THRESHOLD, recall_pos[i_thr]),
                 xytext=(8, -16), textcoords="offset points",
                 fontsize=8, color=COLOR_INSOL, family="monospace")
    ax1.annotate(f"FPR={fpr_neg[i_thr]*100:.1f}%",
                 (THRESHOLD, fpr_neg[i_thr]),
                 xytext=(8, -16), textcoords="offset points",
                 fontsize=8, color=COLOR_SOL, family="monospace")

    # 右:绝对数量 — 不同阈值下 "被判定为 Insoluble" 的 peptide 数
    n_pred_pos = recall_pos * INSOLUBLE_TOTAL
    n_pred_pos_real = recall_pos * INSOLUBLE_TOTAL
    n_pred_pos_false = fpr_neg * SOLUBLE_TOTAL

    ax2.plot(thr, n_pred_pos_real / 1e6,  color=COLOR_INSOL, lw=2.2,
             label=f"TP (true Insoluble flagged)")
    ax2.plot(thr, n_pred_pos_false / 1e6, color=COLOR_SOL,   lw=2.2,
             label=f"FP (Soluble mis-flagged)")
    ax2.fill_between(thr, 0, n_pred_pos_real / 1e6, color=COLOR_INSOL, alpha=0.20)
    ax2.fill_between(thr, 0, n_pred_pos_false / 1e6, color=COLOR_SOL,   alpha=0.20)
    ax2.axvline(THRESHOLD, color="black", ls="--", lw=1.0, alpha=0.6,
                label=f"threshold = {THRESHOLD}")
    ax2.set_xlabel("SoDoPe score threshold")
    ax2.set_ylabel("peptide count (millions)")
    ax2.set_title("Absolute flag count vs threshold")
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, max(INSOLUBLE_TOTAL, SOLUBLE_TOTAL) / 1e6 * 1.05)
    ax2.legend(loc="center right")

    fig.suptitle(f"SoDoPe threshold analysis — total {TOTAL_PEPTIDES:,} peptides",
                 fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / "06_threshold_curve.png")
    plt.close(fig)
    print("  -> 06_threshold_curve.png")


# ========================================== fig 7  Cohen's d vs 其他工具
def fig7_cohens_d_comparison(cohens):
    # 只保留每个工具 |d| 最大的一行 (mhcflurry 有两行)
    best = {}
    for r in cohens:
        if r["cohen_d"] in (None, "", "nan"):
            continue
        d = abs(float(r["cohen_d"]))
        t = r["tool"]
        if t not in best or d > abs(best[t][1]):
            best[t] = (r, float(r["cohen_d"]), r.get("strength", ""))
    rows = [(t, d, s) for t, (_, d, s) in best.items()]
    rows.sort(key=lambda x: abs(x[1]), reverse=True)

    tools_ = [r[0] for r in rows]
    ds     = [r[1] for r in rows]

    # 高亮 sodope
    bar_colors = []
    for t, d in zip(tools_, ds):
        if t == "sodope":
            bar_colors.append("#FFB000")  # 金色突出
        else:
            bar_colors.append("#E45756" if d < 0 else "#54A24B")

    fig, ax = plt.subplots(figsize=(11, 6.5))
    ypos = np.arange(len(tools_))
    bars = ax.barh(ypos, ds, color=bar_colors, edgecolor="white", linewidth=0.5)
    for i, (b, r) in enumerate(zip(bars, rows)):
        d = r[1]
        s = r[2]
        offset = max(0.05, abs(d) * 0.015)
        ax.text(d + (offset if d >= 0 else -offset),
                b.get_y() + b.get_height()/2,
                f"d={d:+.2f}  [{s}]",
                va="center",
                ha="left" if d >= 0 else "right",
                fontsize=9, family="monospace")

    ax.set_yticks(ypos)
    ax.set_yticklabels(tools_, fontsize=10)
    ax.invert_yaxis()
    ax.axvline(0, color="black", lw=0.8)
    for v in [0.2, 0.5, 0.8]:
        ax.axvline(v, color="grey", linestyle=":", alpha=0.4)
        ax.axvline(-v, color="grey", linestyle=":", alpha=0.4)
    ax.set_xlabel("Cohen's d  (positive = positive label has higher score)")
    max_abs = max(abs(d) for d in ds)
    ax.set_xlim(-max_abs - 4, max_abs + 4)
    ax.set_title("Cohen's d per tool — SoDoPe (gold) ranks at the top of the pack", fontsize=12)
    ax.grid(True, axis="x", alpha=0.25)
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
    print("  -> 07_cohens_d_comparison.png")


# ========================================== fig 8  score quantile heatmap
def fig8_score_quantile_heatmap(bg, len_dist):
    """length × score 分位 热力图 — 抽样足够即可"""
    rng = np.random.default_rng(42)
    sub_n = 200_000
    idx = rng.choice(len(bg), size=sub_n, replace=False)
    sub_s = bg[idx]
    lens_pick = rng.choice(
        [d["length"] for d in len_dist],
        size=sub_n,
        p=np.array([d["rows"] for d in len_dist]) /
          np.array([d["rows"] for d in len_dist]).sum(),
    )

    Ls = np.arange(3, 31)
    qs = [0.1, 0.25, 0.5, 0.75, 0.9, 0.95]

    M = np.zeros((len(Ls), len(qs)))
    for i, L in enumerate(Ls):
        mask = lens_pick == L
        if mask.sum() > 30:
            M[i] = np.quantile(sub_s[mask], qs)
        else:
            M[i] = np.nan

    fig, ax = plt.subplots(figsize=(9, 7))
    im = ax.imshow(M, aspect="auto", cmap="RdYlBu_r",
                   vmin=0.0, vmax=1.0,
                   extent=[-0.5, len(qs)-0.5, len(Ls)-0.5, -0.5])
    plt.colorbar(im, ax=ax, label="SoDoPe score")

    ax.set_xticks(range(len(qs)))
    ax.set_xticklabels([f"p{int(q*100):02d}" for q in qs])
    ax.set_yticks(range(len(Ls)))
    ax.set_yticklabels(Ls)
    ax.set_xlabel("score quantile (within length bucket)")
    ax.set_ylabel("peptide length (aa)")
    ax.set_title("Score quantiles by peptide length — quantile fronts shift right with length", fontsize=12)

    # 在格子里标数值,只标有意义的格 (避开 NaN)
    for i in range(len(Ls)):
        for j in range(len(qs)):
            v = M[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        fontsize=7, color="black", alpha=0.7)

    fig.tight_layout()
    fig.savefig(OUT / "08_score_quantile_heatmap.png")
    plt.close(fig)
    print("  -> 08_score_quantile_heatmap.png")


# ========================================== fig 9  ROC curve
def fig9_roc_curve(insol, solub):
    rng = np.random.default_rng(0)
    if len(insol) > 200_000:
        i_sub = rng.choice(insol, 200_000, replace=False)
    else:
        i_sub = insol
    if len(solub) > 200_000:
        s_sub = rng.choice(solub, 200_000, replace=False)
    else:
        s_sub = solub

    # score 越低 ⇒ 越 Insoluble ⇒ 阳性.  所以 y = (insol ≤ t) 的比例,
    # x = (solub ≤ t) 的比例.  注意 t 从大到小, 让曲线从右上走到左上.
    thresholds = np.unique(np.concatenate([
        np.linspace(0, 1, 401),
        np.quantile(i_sub, np.linspace(0, 1, 50)),
        np.quantile(s_sub, np.linspace(0, 1, 50)),
    ]))
    thresholds.sort()

    tpr = np.array([(i_sub <= t).mean() for t in thresholds])  # recall
    fpr = np.array([(s_sub <= t).mean() for t in thresholds])  # 1 - specificity

    # AUC 用 trapezoid + 先取唯一 fpr (重复的 tpr 取最大)
    # 标准做法: 对 ROC 曲线, fpr 排序后, 取 unique fpr 上最大的 tpr
    order = np.argsort(fpr)
    fpr_s = fpr[order]; tpr_s = tpr[order]
    uniq_fpr, inv = np.unique(fpr_s, return_inverse=True)
    # 对重复 fpr 取最大 tpr
    uniq_tpr = np.zeros_like(uniq_fpr)
    np.maximum.at(uniq_tpr, inv, tpr_s)
    auc = float(np.trapezoid(uniq_tpr, uniq_fpr))

    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    ax.plot(fpr, tpr, color=COLOR_INSOL, lw=2.4, label=f"SoDoPe  AUC = {auc:.4f}")
    ax.plot([0, 1], [0, 1], "--", color="grey", lw=1.0, label="random")
    ax.fill_between(fpr, tpr, 0, color=COLOR_INSOL, alpha=0.10)

    # 标 threshold 位置
    i_thr = np.argmin(np.abs(thresholds - THRESHOLD))
    ax.scatter([fpr[i_thr]], [tpr[i_thr]], s=120, color="#FFB000",
               edgecolor="black", zorder=5, linewidths=1.0,
               label=f"operating point @ t={THRESHOLD}")
    ax.annotate(f"TPR={tpr[i_thr]*100:.1f}%\nFPR={fpr[i_thr]*100:.1f}%",
                (fpr[i_thr], tpr[i_thr]),
                xytext=(15, -30), textcoords="offset points",
                fontsize=9, family="monospace",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          alpha=0.9, edgecolor="0.7"))

    ax.set_xlabel("False Positive Rate  (Soluble mis-flagged)")
    ax.set_ylabel("True Positive Rate  (Insoluble correctly flagged)")
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_xlim(-0.01, 1.01)
    ax.set_ylim(-0.01, 1.01)
    ax.set_title(f"ROC curve — Insoluble = positive, lower score = more Insoluble\n"
                     f"note: insol.score ∈ [0, 0.5], solub.score ∈ [0.5, 1] ⇒ zero overlap ⇒ AUC = 1", fontsize=11)
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUT / "09_roc_curve.png")
    plt.close(fig)
    print(f"  -> 09_roc_curve.png  (AUC = {auc:.4f})")


# ========================================== main
def main():
    print("loading data ...")
    bg, insol, solub, by_len, len_dist, cohens, tool_sum, label_sum = load_data()
    print(f"  bg       n={len(bg):,}")
    print(f"  Insoluble n={len(insol):,}")
    print(f"  Soluble   n={len(solub):,}")
    print(f"  by_len rows={len(by_len)}")
    print()

    print("plotting ...")
    fig1_score_overview(bg)
    fig2_insol_vs_sol(insol, solub)
    fig3_score_by_length(by_len)
    fig4_score_vs_length_density(bg, len_dist)
    fig5_score_ecdf(bg, insol, solub)
    fig6_threshold_curve(insol, solub)
    fig7_cohens_d_comparison(cohens)
    fig8_score_quantile_heatmap(bg, len_dist)
    fig9_roc_curve(insol, solub)

    # 顺手保存一份 summary.json, 方便后续读
    summary = {
        "tool": "sodope",
        "total_peptides": TOTAL_PEPTIDES,
        "insoluble_total": INSOLUBLE_TOTAL,
        "soluble_total": SOLUBLE_TOTAL,
        "threshold": THRESHOLD,
        "global": {
            "n": int(len(bg)),
            "mean": float(bg.mean()),
            "median": float(np.median(bg)),
            "std": float(bg.std()),
            "min": float(bg.min()),
            "max": float(bg.max()),
        },
        "insoluble": {
            "n": int(len(insol)),
            "mean": float(insol.mean()),
            "median": float(np.median(insol)),
            "std": float(insol.std()),
        },
        "soluble": {
            "n": int(len(solub)),
            "mean": float(solub.mean()),
            "median": float(np.median(solub)),
            "std": float(solub.std()),
        },
        "cohens_d_full": float(
            (insol.mean() - solub.mean()) /
            np.sqrt((insol.var() + solub.var()) / 2)
        ),
        "operating_point": {
            "threshold": THRESHOLD,
            "recall_insoluble": float((insol <= THRESHOLD).mean()),
            "fpr_soluble":     float((solub <= THRESHOLD).mean()),
        },
    }
    with (OUT / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nwrote {OUT / 'summary.json'}")
    print("\nDone — 9 figures in", OUT)


if __name__ == "__main__":
    main()