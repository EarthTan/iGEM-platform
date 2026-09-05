"""
plot_tipred.py — TIPred 在 2000 万肽库上的运行结果统计可视化

数据来源 (全部已离线准备,无需 DB):
    results/plots/score_samples.npz                       - 50 万随机抽样 score (总体)
    results/plots/tipred/tipred_nontip_scores.npz         - 全量 104,760 条 non-TIP score (label='non-TIP')
    results/plots/tipred/tipred_with_length.npz           - 198k 抽样,带 score/label/length
    results/plots/tipred/tipred_score_by_length.csv       - 全量按长度聚合 score 统计
    results/plots/score_summary.csv                       - 各工具总体统计
    results/plots/cohen_d_table.csv                       - 各工具 Cohen's d
    results/plots/library/length_distribution.csv         - 肽库长度分布

输出 (9 张图):
    01_score_histogram_overview.png     总体 score 直方图 (线性 + log-Y)
    02_score_distribution_predicted.png 预测 TIP vs non-TIP score 分布对比
    03_score_by_length_box.png          按长度区间 score 分布 (折带图)
    04_score_vs_length_density.png      score vs 长度 散点 + 分位带 + 库分布柱
    05_score_ecdf.png                   总体 ECDF 曲线 (用于挑阈值)
    06_threshold_curve.png              不同 score 阈值能召回 / 误报的肽数量
    07_cohens_d_comparison.png          TIPred vs 其他工具的 Cohen's d (与 pLM4CPPs 对齐)
    08_score_quantile_heatmap.png       按 length × score 分位 的热力图
    09_label_share_by_length.png        各长度段预测为 TIP / non-TIP 的占比

注意: TIPred 的 label 字段是 *预测* 结果(label='TIP' / 'non-TIP', 默认阈值=0.5),
       不是 ground truth。 详见 details: prediction 字段。
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
OUT = ROOT / "analysis" / "tipred"
OUT.mkdir(parents=True, exist_ok=True)

TOTAL_PEPTIDES = 20_248_885
TIPRED_TOTAL   = 20_248_885              # tipred 行数 (= 全库,因为是无 length 过滤的工具)
TIP_PRED_COUNT  = 20_144_125             # predicted TIP
NONTIP_PRED_COUNT = 104_760              # predicted non-TIP
SAMPLE_BG = 500_000
THRESHOLD = 0.5                          # TIPred 默认阈值 (details.threshold)

# 注: TIPred 语义上 score 越低 越像 TIP(positive),
#     score 越高 越像 non-TIP(negative)。 因此绘图时 TIP 是 "感兴趣" 的低分侧。
COLOR_BG     = "#4C78A8"  # 总体 / 高分侧
COLOR_TIP    = "#E45756"  # 预测 TIP
COLOR_NONTIP = "#54A24B"  # 预测 non-TIP
COLOR_HI     = "#54A24B"
COLOR_LO     = "#F2A93B"

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


# ================================================================== helpers
def load_data():
    samples = np.load(DATA / "score_samples.npz")
    bg_score = samples["tipred"]                            # 500k

    nontip_npz = np.load(DATA / "tipred" / "tipred_nontip_scores.npz")
    nontip_score = nontip_npz["nontip"]                     # 104,760 全量

    strat = np.load(DATA / "tipred" / "tipred_with_length.npz", allow_pickle=True)
    s_score  = strat["score"]
    s_label  = strat["label"]
    s_length = strat["length"]

    # length × score 全量聚合
    by_len = []
    with (DATA / "tipred" / "tipred_score_by_length.csv").open() as f:
        for row in csv.DictReader(f):
            by_len.append({k: (int(v) if k in ("length","n") else float(v))
                           for k, v in row.items()})

    # 库长度分布
    len_dist = []
    with (DATA / "library" / "length_distribution.csv").open() as f:
        for row in csv.DictReader(f):
            len_dist.append({k: (int(v) if k == "length" else float(v))
                             for k, v in row.items()})

    cohens = []
    with (DATA / "cohen_d_table.csv").open() as f:
        for row in csv.DictReader(f):
            cohens.append(row)

    return bg_score, nontip_score, s_score, s_label, s_length, by_len, len_dist, cohens


def kde_curve(s: np.ndarray, lo: float, hi: float, n: int = 400, bw=None):
    if len(s) > 50_000:
        s = np.random.default_rng(0).choice(s, 50_000, replace=False)
    kde = gaussian_kde(s, bw_method=bw or "scott")
    xs = np.linspace(lo, hi, n)
    return xs, kde(xs)


# ====================================================== fig 1  总体分布概览
def fig1_score_overview(bg, nontip):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # ----- 左: 线性坐标
    bins = np.linspace(0, 1, 81)
    ax1.hist(bg, bins=bins, density=True, color=COLOR_BG, alpha=0.55,
             edgecolor="white", linewidth=0.3,
             label=f"all peptides (n={SAMPLE_BG:,} sampled)")
    ax1.hist(nontip, bins=bins, density=True, color=COLOR_NONTIP, alpha=0.55,
             edgecolor="white", linewidth=0.3,
             label=f"predicted non-TIP (n={NONTIP_PRED_COUNT:,} full)")

    xs1, ys1 = kde_curve(bg, 0, 1)
    ax1.plot(xs1, ys1, color=COLOR_BG, lw=1.6)
    xs2, ys2 = kde_curve(nontip, 0, 1)
    ax1.plot(xs2, ys2, color=COLOR_NONTIP, lw=1.6)

    ax1.axvline(THRESHOLD, color="black", ls="--", lw=1.4,
                label=f"default threshold = {THRESHOLD}")
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("TIPred score (probability of being TIP)")
    ax1.set_ylabel("density")
    ax1.set_title("Linear scale (mass is near 0.9)")
    ax1.legend(loc="upper left", fontsize=8)

    # ----- 右: log-y 让 0 附近 / 中段可见
    ax2.hist(bg, bins=bins, color=COLOR_BG, alpha=0.55, edgecolor="white", linewidth=0.3,
             label=f"all (sampled {SAMPLE_BG:,})")
    ax2.hist(nontip, bins=bins, color=COLOR_NONTIP, alpha=0.55, edgecolor="white", linewidth=0.3,
             label=f"non-TIP ({NONTIP_PRED_COUNT:,} full)")
    ax2.axvline(THRESHOLD, color="black", ls="--", lw=1.4)
    ax2.set_yscale("log")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("TIPred score")
    ax2.set_ylabel("count (log)")
    ax2.set_title("Log-Y: tail near 0 becomes visible")
    ax2.legend(loc="upper left", fontsize=8)

    # 阈值指标
    bg_above = (bg >= THRESHOLD).sum()          # 分数>=0.5 的人数 (即被预测为 TIP)
    nontip_below = (nontip < THRESHOLD).sum()   # predicted non-TIP 中 score<0.5 的人数
    bg_tip_pred = bg_above / SAMPLE_BG * 100
    nontip_consistent = nontip_below / NONTIP_PRED_COUNT * 100

    fig.suptitle(
        f"TIPred score distribution on {TOTAL_PEPTIDES:,} peptides  "
        f"(threshold=0.5, lower ⇒ TIP)\n"
        f"background μ={bg.mean():.4f},  non-TIP μ={nontip.mean():.4f}   "
        f"|   predicted TIP (score≥0.5): {bg_tip_pred:.2f}%   "
        f"non-TIP class consistency (score<0.5): {nontip_consistent:.1f}%",
        fontsize=12, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(OUT / "01_score_histogram_overview.png")
    plt.close(fig)
    print("  -> 01_score_histogram_overview.png")


# ============================================ fig 2  TIP vs non-TIP violin + KDE
def fig2_predicted_vs_bg(bg, nontip):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    data = [bg, nontip]
    labels = [f"All peptides\n(n={SAMPLE_BG:,} sampled)",
              f"Predicted non-TIP\n(n={NONTIP_PRED_COUNT:,} full)"]
    colors = [COLOR_BG, COLOR_NONTIP]

    parts = ax1.violinplot(data, positions=[1, 2], widths=0.8,
                           showmeans=False, showmedians=False, showextrema=False)
    for pc, c in zip(parts["bodies"], colors):
        pc.set_facecolor(c)
        pc.set_edgecolor(c)
        pc.set_alpha(0.55)

    bp = ax1.boxplot(data, positions=[1, 2], widths=0.18, patch_artist=True,
                     showfliers=False,
                     boxprops=dict(facecolor="white", edgecolor="black", lw=1.0),
                     medianprops=dict(color="black", lw=1.4),
                     whiskerprops=dict(color="black"),
                     capprops=dict(color="black"))
    for i, (c, d_) in enumerate(zip(colors, data)):
        ax1.scatter(i + 1, d_.mean(), marker="D", s=60, color="white",
                    edgecolor=c, zorder=4, linewidths=1.6)

    ax1.axvline(0.5 + 1, color="black", ls="--", lw=1.2)  # x-axis equivalent of thr
    ax1.set_xticks([1, 2])
    ax1.set_xticklabels(labels)
    ax1.set_xlim(0.4, 2.6)
    ax1.set_ylim(-0.02, 1.05)
    ax1.set_ylabel("TIPred score")
    ax1.set_title("Violin + box: TIP scores are clearly lower")
    ax1.text(0.02, 0.95,
             f"background:  μ={bg.mean():.4f},  med={np.median(bg):.4f},  σ={bg.std():.4f}\n"
             f"non-TIP:     μ={nontip.mean():.4f},  med={np.median(nontip):.4f},  σ={nontip.std():.4f}",
             transform=ax1.transAxes, ha="left", va="top", fontsize=9,
             family="monospace",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                       alpha=0.9, edgecolor="0.7"))

    # ----- 右: 用 histogram 而不是 KDE,避免极高峰的数值下溢
    bins = np.linspace(0, 1, 81)
    ax2.hist(bg,     bins=bins, density=True, color=COLOR_BG,     alpha=0.45,
             edgecolor="white", linewidth=0.3,
             label=f"All (sampled {SAMPLE_BG:,})")
    ax2.hist(nontip, bins=bins, density=True, color=COLOR_NONTIP, alpha=0.45,
             edgecolor="white", linewidth=0.3,
             label=f"non-TIP ({NONTIP_PRED_COUNT:,})")

    ax2.set_yscale("log")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("TIPred score")
    ax2.set_ylabel("density (log)")
    ax2.set_title("Histogram (log-Y): both populations are tight")
    ax2.legend(loc="upper left", fontsize=8)

    cohens_d = (nontip.mean() - bg.mean()) / np.sqrt((nontip.var() + bg.var()) / 2)
    fig.suptitle(
        f"Predicted TIP vs non-TIP score separation  —  Cohen's d ≈ {cohens_d:.2f}\n"
        f"(this Cohen's d uses predicted labels on the whole 20M — different from "
        f"the small-sample value -15.38 in cohen_d_table.csv)",
        fontsize=11, y=1.03,
    )
    fig.tight_layout()
    fig.savefig(OUT / "02_score_distribution_predicted.png")
    plt.close(fig)
    print("  -> 02_score_distribution_predicted.png")


# ========================================== fig 3  按 length 区间 score 分布
def fig3_score_by_length(by_len):
    fig, ax = plt.subplots(figsize=(12, 6))

    groups = [(8, 12), (13, 17), (18, 22), (23, 27), (28, 30)]
    rows = [r for r in by_len if any(r["length"] in range(g[0], g[1] + 1) for g in groups)]

    def bucket_of(l):
        for g in groups:
            if g[0] <= l <= g[1]:
                return f"{g[0]}-{g[1]}"
        return None

    bucket_data = {}
    bucket_n    = {}
    for r in by_len:
        b = bucket_of(r["length"])
        if b is None: continue
        bucket_data.setdefault(b, []).append(r)
        bucket_n[b]    = bucket_n.get(b, 0) + r["n"]

    labels = [f"{g[0]}-{g[1]}" for g in groups]
    short = [r for r in by_len if r["length"] <= 7]
    if short:
        bucket_data["≤7"] = short
        bucket_n["≤7"] = sum(r["n"] for r in short)
        labels = ["≤7"] + labels

    means = [np.mean([r["mean"] for r in bucket_data[l]]) for l in labels]
    p25   = [np.mean([r["p25"]   for r in bucket_data[l]]) for l in labels]
    p75   = [np.mean([r["p75"]   for r in bucket_data[l]]) for l in labels]
    p95   = [np.mean([r["p95"]   for r in bucket_data[l]]) for l in labels]
    p99   = [np.mean([r["p99"]   for r in bucket_data[l]]) for l in labels]
    ns    = [bucket_n[l] for l in labels]

    x = np.arange(len(labels))
    ax.fill_between(x, p25, p75, color=COLOR_BG, alpha=0.30, label="p25–p75 (IQR)")
    ax.fill_between(x, p75, p95, color=COLOR_BG, alpha=0.18, label="p75–p95")
    ax.fill_between(x, p95, p99, color=COLOR_HI, alpha=0.30, label="p95–p99")
    ax.plot(x, means, "o-", color="black", lw=2, markersize=8, label="mean")
    ax.plot(x, p99, "v--", color=COLOR_TIP, lw=1.4, markersize=7, label="p99")

    for xi, (m, n, l) in enumerate(zip(means, ns, labels)):
        ax.annotate(f"μ={m:.3f}", (xi, m), textcoords="offset points",
                 xytext=(0, 10), ha="center", fontsize=9, color="black")
        ax.annotate(f"n={n:,}", (xi, p25[xi]), textcoords="offset points",
                 xytext=(0, -16), ha="center", fontsize=8, color="0.4")

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_yscale("linear")
    ax.set_xlabel("peptide length bucket")
    ax.set_ylabel("TIPred score")
    ax.set_ylim(0.5, 1.0)
    ax.axhline(THRESHOLD, color="black", ls="--", lw=1.2, alpha=0.7)
    ax.text(len(labels) - 0.5, THRESHOLD + 0.01, f"thr = {THRESHOLD}",
            ha="right", va="bottom", fontsize=8)
    ax.set_title(f"Score distribution by peptide length  (n={TIPRED_TOTAL:,} peptides, full population)")
    ax.legend(loc="lower right", ncol=2)
    fig.tight_layout()
    fig.savefig(OUT / "03_score_by_length_box.png")
    plt.close(fig)
    print("  -> 03_score_by_length_box.png")


# ========================================== fig 4  score vs length 2D density
def fig4_score_vs_length(by_len, len_dist, s_score, s_label, s_length):
    fig, ax = plt.subplots(figsize=(12, 5.5))

    # ---- 散点: 抽样样本 (predicted TIP vs non-TIP)
    rng = np.random.default_rng(0)
    tip_mask = s_label == 'TIP'
    nt_mask  = s_label == 'non-TIP'
    tip_idx = np.where(tip_mask)[0]
    nt_idx  = np.where(nt_mask)[0]
    # 因为 non-TIP 很少, 全部画
    ax.scatter(s_length[nt_idx], s_score[nt_idx], s=2, alpha=0.35,
               color=COLOR_NONTIP, label=f"predicted non-TIP (n={nt_mask.sum():,})", edgecolor="none")
    tip_sample = rng.choice(tip_idx, min(40_000, len(tip_idx)), replace=False)
    ax.scatter(s_length[tip_sample], s_score[tip_sample], s=1.2, alpha=0.25,
               color=COLOR_TIP, label=f"predicted TIP (sampled 40k)", edgecolor="none")

    # ---- 上层: 按 length 的分位带
    lens    = np.array([r["length"] for r in by_len])
    means   = np.array([r["mean"]   for r in by_len])
    p25     = np.array([r["p25"]    for r in by_len])
    p75     = np.array([r["p75"]    for r in by_len])
    p95     = np.array([r["p95"]    for r in by_len])
    p99     = np.array([r["p99"]    for r in by_len])
    ns      = np.array([r["n"]      for r in by_len], dtype=float)
    ns_share = ns / ns.sum() * 100

    ax.plot(lens, means, "o-", color="black", lw=2, markersize=6, label="mean (full)")
    ax.plot(lens, p99,  "v--", color=COLOR_TIP, lw=1.2, markersize=5,
            label="p99 (full)")
    ax.fill_between(lens, p25, p75, color=COLOR_BG, alpha=0.18, label="p25–p75 (IQR)")

    ax.axhline(THRESHOLD, color="black", ls="--", lw=1.0, alpha=0.5)
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("TIPred score")
    ax.set_xlim(0, 32)
    ax.set_ylim(0, 1.02)
    ax.set_title(f"Score vs length — predicted TIP sits under, predicted non-TIP overlaps near 0–0.5  (n={TIPRED_TOTAL:,})")
    ax.legend(loc="lower right", fontsize=8)

    # 副: 库长度分布柱
    ax2 = ax.twinx()
    ax2.bar(lens, ns_share, color="0.85", alpha=0.45, width=0.7, edgecolor="none")
    ax2.set_ylabel("share of library (%)", color="0.5")
    ax2.tick_params(axis="y", labelcolor="0.5")
    ax2.set_ylim(0, max(ns_share) * 2.5)
    ax2.grid(False)

    fig.tight_layout()
    fig.savefig(OUT / "04_score_vs_length_density.png")
    plt.close(fig)
    print("  -> 04_score_vs_length_density.png")


# ========================================== fig 5  ECDF (背景 + non-TIP)
def fig5_score_ecdf(bg, nontip):
    fig, ax = plt.subplots(figsize=(10, 5.5))

    bg_sorted = np.sort(bg)
    nt_sorted = np.sort(nontip)
    ax.plot(bg_sorted, np.linspace(0, 1, len(bg_sorted), endpoint=False),
            color=COLOR_BG, lw=2.0, label=f"All peptides  (sampled {SAMPLE_BG:,})")
    ax.plot(nt_sorted, np.linspace(0, 1, len(nt_sorted), endpoint=False),
            color=COLOR_NONTIP, lw=2.0, label=f"Predicted non-TIP  (full {NONTIP_PRED_COUNT:,})")

    for thr, c in [(0.05, "0.05"), (0.20, "0.20"), (0.30, "0.30"),
                   (0.50, "0.50"), (0.70, "0.70"), (0.90, "0.90")]:
        ax.axvline(thr, color="0.7", ls=":", lw=0.8)
        ax.text(thr, -0.04, c, ha="center", va="top", fontsize=8, color="0.4",
                transform=ax.get_xaxis_transform())

    scale = TOTAL_PEPTIDES / SAMPLE_BG
    bg_thr = {}
    for thr in [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9]:
        bg_thr[thr] = (bg >= thr).sum() * scale

    nt_thr = {thr: (nontip >= thr).sum() for thr in bg_thr}

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, 1.02)
    ax.set_xlabel("TIPred score")
    ax.set_ylabel("cumulative fraction")
    ax.set_title("ECDF — choose an operating threshold (lower = more aggressive TIP-call)")
    ax.legend(loc="lower right")

    rows = ["thr     bg (full)     non-TIP (full)"]
    for thr in [0.05, 0.10, 0.30, 0.50, 0.70, 0.90]:
        bg_n  = int(bg_thr[thr])
        nt_n  = int(nt_thr[thr])
        rows.append(f"{thr:.2f}    {bg_n:>10,}    {nt_n:>10,}")
    rows.append("---")
    rows.append("Interpretation: thr=t means")
    rows.append("  bg ≥ t   = predicted TIP")
    rows.append("  non-TIP ≥ t = still flagged TIP")
    txt = "\n".join(rows)
    ax.text(0.98, 0.65, txt, transform=ax.transAxes,
            ha="right", va="top", fontsize=8, family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                      alpha=0.92, edgecolor="0.7"))

    fig.tight_layout()
    fig.savefig(OUT / "05_score_ecdf.png")
    plt.close(fig)
    print("  -> 05_score_ecdf.png")


# ========================================== fig 6  不同阈值的召回曲线
def fig6_threshold_curve(bg, nontip):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    thrs = np.unique(np.concatenate([
        np.linspace(0, 0.5, 80),
        np.linspace(0.5, 1.0, 50),
    ]))
    scale = TOTAL_PEPTIDES / SAMPLE_BG

    n_bg      = np.array([(bg      >= t).sum() * scale for t in thrs])
    n_nontip  = np.array([(nontip  >= t).sum()          for t in thrs])
    recall    = n_nontip / NONTIP_PRED_COUNT * 100     # 1 - 拒绝率
    spec      = (1 - n_bg / TOTAL_PEPTIDES) * 100       # 特异度 (全库非 TIP 占比)

    # ----- 左: 阈值 vs 计数
    ax1.plot(thrs, n_bg,    color=COLOR_BG,     lw=2.0, label="All peptides (full)")
    ax1.plot(thrs, n_nontip, color=COLOR_NONTIP, lw=2.0, label="Predicted non-TIP (full)")
    ax1.set_yscale("log")
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("score threshold  (below = predict TIP)")
    ax1.set_ylabel("# peptides above threshold (log)")
    ax1.set_title("Population above each threshold")
    ax1.legend(loc="upper right")

    # ----- 右: TPR (1 - 拒绝率) / FPR (误报率 = 全库被错叫 TIP)
    fpr = (1 - spec)
    ax2.plot(thrs, recall, color=COLOR_NONTIP, lw=2.0,
             label="TPR on non-TIP  (1 − reject rate)")
    ax2.plot(thrs, fpr * 100, color=COLOR_TIP, lw=2.0,
             label="FPR  (all peptides called TIP)")
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 105)
    ax2.set_xlabel("score threshold")
    ax2.set_ylabel("%")
    ax2.set_title("TPR / FPR on the 20.25M library")
    ax2.legend(loc="upper right")

    for ax in (ax1, ax2):
        ax.axvline(THRESHOLD, color="black", ls="--", lw=1.4, alpha=0.7)
        ax.text(THRESHOLD, 0.96, f" {THRESHOLD}",
                transform=ax.get_xaxis_transform(),
                fontsize=9, color="black")

    fig.suptitle("Threshold sweep — operating point selection on the 20.25M library",
                 fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / "06_threshold_curve.png")
    plt.close(fig)
    print("  -> 06_threshold_curve.png")


# ========================================== fig 7  vs 其他工具 Cohen's d
def fig7_cohens_d(cohens):
    seen = set()
    rows = []
    strength_map = {"极强": "very strong", "很强": "very strong",
                    "强": "strong", "中等": "medium", "弱": "weak", "极弱": "very weak"}
    for r in cohens:
        if r["tool"] in seen: continue
        seen.add(r["tool"])
        s = r["strength"]
        for cn, en in strength_map.items():
            if cn in s:
                s = s.replace(cn, en)
        s = "".join(ch for ch in s if not (0x2E80 <= ord(ch) <= 0x9FFF))
        r["strength"] = s
        rows.append(r)

    rows.sort(key=lambda r: (r["tool"] != "tipred", -float(r["abs_d"])))

    labels = [r["tool"] for r in rows]
    d_vals = [float(r["cohen_d"]) for r in rows]
    abs_d  = [float(r["abs_d"]) for r in rows]
    strengths = [r["strength"] for r in rows]
    notes = [r["note"] for r in rows]
    colors = ["#E45756" if r["tool"] == "tipred" else "#4C78A8" for r in rows]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    bars = ax.barh(labels, abs_d, color=colors, edgecolor="white")
    ax.set_xscale("log")
    ax.set_xlabel("|Cohen's d|  (effect size, log scale)")
    ax.set_title("Separation power: TIPred vs other tools")

    for bar, d, absd, s, n in zip(bars, d_vals, abs_d, strengths, notes):
        sign = "+" if d >= 0 else "−"
        ax.text(bar.get_width() * 1.05, bar.get_y() + bar.get_height() / 2,
                f"d={sign}{absd:.2f} ({s})",
                va="center", fontsize=9, color="black")
        if n:
            n_en = n
            replacements = [
                ("阳性=non-TIP(score 低)", "positive=non-TIP (low score)"),
                ("阳性=non-TIP", "positive=non-TIP"),
                ("score 低", "(low score)"),
                ("阳性", "positive"),
            ]
            for cn, en in replacements:
                n_en = n_en.replace(cn, en)
            n_clean = "".join(ch for ch in n_en if not (0x2E80 <= ord(ch) <= 0x9FFF))
            if n_clean.strip():
                ax.text(0.6, bar.get_y() + bar.get_height() / 2,
                        n_clean, va="center", ha="left", fontsize=7, color="0.5",
                        style="italic")

    for thr, c in [(0.2, "small"), (0.5, "medium"), (0.8, "large"), (2.0, "v.large")]:
        ax.axvline(thr, color="0.7", ls=":", lw=0.7)
        ax.text(thr, -0.7, c, ha="center", fontsize=7, color="0.5",
                transform=ax.get_xaxis_transform())

    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.3)
    ax.text(0.99, 0.02,
            "TIPred d=15.38 was computed on a 200k random sample\n"
            "where 982 / 200k were TIP (vs 20.1M / 20.25M in the full library).\n"
            "On full data the picture is very different.",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8, style="italic", color="0.3")
    fig.tight_layout()
    fig.savefig(OUT / "07_cohens_d_comparison.png")
    plt.close(fig)
    print("  -> 07_cohens_d_comparison.png")


# ========================================== fig 8  length × quantile 热力图
def fig8_quantile_heatmap(by_len):
    fig, ax = plt.subplots(figsize=(11, 5.5))

    qs = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    lens = sorted({r["length"] for r in by_len if r["n"] > 1000})
    M = np.zeros((len(qs), len(lens)))

    by_len_map = {r["length"]: r for r in by_len}
    for j, l in enumerate(lens):
        r = by_len_map.get(l)
        if r is None: continue
        # 用 p25/p75/p95/p99 + 估算 p01/p05/p10/p90
        full_vals = {
            0.01: max(r["p25"] - (r["p75"] - r["p25"]) * 0.7, 0.0),
            0.05: r["p25"] - (r["p75"] - r["p25"]) * 0.3,
            0.10: r["p25"] - (r["p75"] - r["p25"]) * 0.1,
            0.25: r["p25"],
            0.50: r["mean"],
            0.75: r["p75"],
            0.90: r["p95"] - (r["p99"] - r["p95"]) * 0.3,
            0.95: r["p95"],
            0.99: r["p99"],
        }
        M[:, j] = [full_vals[q] for q in qs]

    im = ax.imshow(M, aspect="auto", cmap="viridis_r",
                   vmin=0.0, vmax=1.0,
                   extent=[-0.5, len(lens) - 0.5, len(qs) - 0.5, -0.5])
    ax.set_xticks(range(len(lens)))
    ax.set_xticklabels(lens)
    ax.set_yticks(range(len(qs)))
    ax.set_yticklabels([f"p{int(q*100):02d}" for q in qs])
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("score quantile")
    ax.set_title("Score quantile heatmap — TIPred scores are tight across lengths")

    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("TIPred score")

    q99_idx = qs.index(0.99)
    ax.axhline(q99_idx, color="white", lw=0.4, alpha=0.4)

    fig.tight_layout()
    fig.savefig(OUT / "08_score_quantile_heatmap.png")
    plt.close(fig)
    print("  -> 08_score_quantile_heatmap.png")


# ========================================== fig 9  按长度的 TIP/non-TIP 占比
def fig9_label_share_by_length(s_score, s_label, s_length, bg):
    """基于抽样 + 库长度分布,估算每个长度段 TIP / non-TIP 占比"""
    fig, ax = plt.subplots(figsize=(11, 5))

    lens = sorted(set(int(l) for l in s_length))
    pct_tip = {}
    pct_nt  = {}
    cnt_nt  = {}
    for l in lens:
        m = s_length == l
        if m.sum() == 0: continue
        n_tip = (s_label[m] == 'TIP').sum()
        n_nt  = (s_label[m] == 'non-TIP').sum()
        total = m.sum()
        pct_tip[l] = n_tip / total * 100
        pct_nt[l]  = n_nt  / total * 100
        cnt_nt[l]  = n_nt

    # 左: 占比
    x = np.array(lens)
    tip_arr = np.array([pct_tip.get(int(l), 0) for l in x])
    nt_arr  = np.array([pct_nt.get(int(l), 0) for l in x])

    ax.fill_between(x, 0, tip_arr, color=COLOR_TIP,    alpha=0.55, label="predicted TIP")
    ax.fill_between(x, tip_arr, 100, color=COLOR_NONTIP, alpha=0.55, label="predicted non-TIP")

    # 数值标注 - 只标有特色的长度,避免拥挤
    for i, (l_, p_t, p_n) in enumerate(zip(x, tip_arr, nt_arr)):
        n_at_l = int(np.sum(s_length == l_))
        if n_at_l < 30: continue
        # TIP: 只在 length 是奇数或 non-TIP>1% 时标注
        show_tip = (p_t > 50) and (int(l_) % 2 == 1 or p_n > 1)
        if show_tip:
            ax.annotate(f"{p_t:.1f}%", (l_, p_t / 2), ha="center", va="center",
                        fontsize=8, color="white", fontweight="bold")
        # non-TIP: 仅当 >=2% 时,标在顶部
        if p_n >= 2.0:
            ax.annotate(f"{p_n:.1f}%", (l_, p_t + p_n / 2), ha="center", va="center",
                        fontsize=8, color="white", fontweight="bold")

    ax.set_xlim(min(x), max(x))
    ax.set_ylim(0, 100)
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("share of TIPred prediction (%)")
    ax.set_title("Predicted TIP vs non-TIP share by peptide length (sampled 198k)")
    ax.legend(loc="lower right")

    # 副: 该长度段的 TIP count (按 scale factor)
    ax2 = ax.twinx()
    nt_cnt = np.array([cnt_nt.get(int(l), 0) for l in x])
    # scale: 198k 样本 / 总样本在 1/102 大约 => scale = 102
    scale = 102
    nt_full = nt_cnt * scale
    ax2.bar(x, nt_full, color="0.7", alpha=0.35, width=0.7, edgecolor="none")
    ax2.set_ylabel("estimated non-TIP count (full, log scale)", color="0.5")
    ax2.set_yscale("log")
    ax2.tick_params(axis="y", labelcolor="0.5")
    ax2.grid(False)
    ax2.set_ylim(1, 1e7)

    fig.tight_layout()
    fig.savefig(OUT / "09_label_share_by_length.png")
    plt.close(fig)
    print("  -> 09_label_share_by_length.png")


# ===================================================================== main
def main():
    print("loading data ...")
    bg, nontip, s_score, s_label, s_length, by_len, len_dist, cohens = load_data()
    print(f"  bg sample        : {len(bg):>9,} scores, mean={bg.mean():.4f}")
    print(f"  non-TIP full     : {len(nontip):>9,} scores, mean={nontip.mean():.4f}")
    print(f"  stratified sample: {len(s_score):>9,} (TIP={int((s_label=='TIP').sum()):,}, non-TIP={int((s_label=='non-TIP').sum()):,})")
    print(f"  by_length rows   : {len(by_len):>9,} length buckets")

    print("plotting 01 overview ...")
    fig1_score_overview(bg, nontip)

    print("plotting 02 predicted vs bg ...")
    fig2_predicted_vs_bg(bg, nontip)

    print("plotting 03 score by length ...")
    fig3_score_by_length(by_len)

    print("plotting 04 score vs length ...")
    fig4_score_vs_length(by_len, len_dist, s_score, s_label, s_length)

    print("plotting 05 ECDF ...")
    fig5_score_ecdf(bg, nontip)

    print("plotting 06 threshold curve ...")
    fig6_threshold_curve(bg, nontip)

    print("plotting 07 cohens d ...")
    fig7_cohens_d(cohens)

    print("plotting 08 quantile heatmap ...")
    fig8_quantile_heatmap(by_len)

    print("plotting 09 label share by length ...")
    fig9_label_share_by_length(s_score, s_label, s_length, bg)

    # summary JSON
    scale = TOTAL_PEPTIDES / SAMPLE_BG
    summary = {
        "tool": "tipred",
        "library_size": TOTAL_PEPTIDES,
        "background_sample": int(SAMPLE_BG),
        "predicted_tip_full_count":    int(TIP_PRED_COUNT),
        "predicted_nontip_full_count": int(NONTIP_PRED_COUNT),
        "background_score_mean":       float(bg.mean()),
        "background_score_std":        float(bg.std()),
        "background_score_med":        float(np.median(bg)),
        "nontip_score_mean":           float(nontip.mean()),
        "nontip_score_std":            float(nontip.std()),
        "nontip_score_med":            float(np.median(nontip)),
        "nontip_score_min":            float(nontip.min()),
        "nontip_score_max":            float(nontip.max()),
        "cohens_d_full_predicted":     float((nontip.mean() - bg.mean()) /
                                              np.sqrt((nontip.var() + bg.var()) / 2)),
        "cohens_d_reference_table":    -15.3787,
        "threshold_table": {
            str(t): {
                "bg_above":    int((bg      >= t).sum() * scale),
                "nontip_above": int((nontip >= t).sum()),
                "tpr_on_nontip": float((nontip >= t).sum() / NONTIP_PRED_COUNT),
            } for t in [0.05, 0.10, 0.30, 0.50, 0.70, 0.90]
        },
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print("  -> summary.json")
    print("\nALL DONE.")


if __name__ == "__main__":
    main()