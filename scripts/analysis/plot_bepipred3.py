"""plot_bepipred3.py — BepiPred-3.0 20M enrichment 的统计学图表。

Inputs (under results/plots/bepipred3/):
  - bepipred3_full_sample.csv.gz : 1M 随机样本
  - bepipred3_pos_sample.csv     : 50k Epitope
  - bepipred3_neg_sample.csv     : 50k Non-epitope
  - bepipred3_by_length.csv      : 全表 GROUP BY length
  - bepipred3_by_source.csv      : 全表 GROUP BY source
  - bepipred3_summary.json       : 全表统计

Outputs:
  - bepipred3_score_distribution.png  - score 分布: AMP (mgy) vs non-AMP, + 阈值线
  - bepipred3_score_by_length.png     - 按 peptide length 看 mean / epitope rate
  - bepipred3_score_by_source.png     - 按 source 看 mean / epitope rate
  - bepipred3_per_residue_stats.png   - avg vs max vs max_linear 三种分数的对比
  - bepipred3_label_box.png           - Epitope / Non-epitope box plot
  - bepipred3_score_ecdf.png          - 全表 CDF + percentile markers
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import gaussian_kde

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "results" / "plots" / "bepipred3"

THRESHOLD = 0.1512
COL_EPI = "#E45756"
COL_NONEPI = "#54A24B"
COL_AMP = "#4C78A8"
COL_NONAMP = "#F2A93B"
COL_THR = "#333333"


def load_csv(path):
    out = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as f:
        header = f.readline().strip().split(",")
        for line in f:
            parts = line.rstrip("\n").split(",")
            out.append({h: parts[i] for i, h in enumerate(header)})
    return out, header


def main():
    log_rows, header = load_csv(HERE / "bepipred3_full_sample.csv.gz")
    pos_rows, _ = load_csv(HERE / "bepipred3_pos_sample.csv")
    neg_rows, _ = load_csv(HERE / "bepipred3_neg_sample.csv")
    summary = json.loads((HERE / "bepipred3_summary.json").read_text())
    by_len = []
    with open(HERE / "bepipred3_by_length.csv") as f:
        next(f)
        for line in f:
            p = line.strip().split(",")
            by_len.append({"length": int(p[0]), "n": int(p[1]),
                           "mean": float(p[2]), "epitope_pct": float(p[3])})
    by_src = []
    with open(HERE / "bepipred3_by_source.csv") as f:
        next(f)
        for line in f:
            p = line.strip().split(",")
            by_src.append({"source": p[0], "n": int(p[1]),
                           "mean": float(p[2]), "epitope_pct": float(p[3])})

    score = np.array([float(r["score"]) for r in log_rows])
    label = np.array([r["label"] for r in log_rows])
    source = np.array([r["source"] for r in log_rows])
    length = np.array([int(r["length"]) for r in log_rows])
    avg_ep = np.array([float(r["avg_ep"]) for r in log_rows])
    max_ep = np.array([float(r["max_ep"]) for r in log_rows])
    max_lin = np.array([float(r["max_lin_ep"]) for r in log_rows])

    pos_score = np.array([float(r["score"]) for r in pos_rows])
    neg_score = np.array([float(r["score"]) for r in neg_rows])

    # ============= 图 1: score 分布 (AMP vs non-AMP, KDE + hist) =============
    fig, ax = plt.subplots(figsize=(10, 6))
    is_amp = (source == "legacy_mgy_2022")
    for mask, lbl, color in [
        (is_amp, "AMP (legacy_mgy_2022, n={:,})".format(is_amp.sum()), COL_AMP),
        (~is_amp, "non-AMP (other sources, n={:,})".format((~is_amp).sum()), COL_NONAMP),
    ]:
        s = score[mask]
        # subsample for KDE 速度
        sub = s if len(s) <= 100_000 else np.random.choice(s, 100_000, replace=False)
        bins = np.linspace(score.min(), score.max(), 80)
        ax.hist(sub, bins=bins, density=True, histtype="stepfilled",
                alpha=0.35, color=color, label=lbl)
        kde = gaussian_kde(sub, bw_method=0.05)
        xs = np.linspace(score.min(), score.max(), 400)
        ax.plot(xs, kde(xs), color=color, linewidth=2)
    ax.axvline(THRESHOLD, color=COL_THR, linestyle="--", linewidth=1.5,
               label=f"Epitope threshold = {THRESHOLD}")
    ax.set_xlabel("BepiPred-3.0 score (mean epitope probability)")
    ax.set_ylabel("Density")
    ax.set_title("BepiPred-3.0 score distribution: AMP vs non-AMP\n"
                 "n=1,000,000 sampled; AMP source = legacy_mgy_2022")
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_score_distribution.png", dpi=120)
    plt.close(fig)

    # ============= 图 2: score vs peptide length =============
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5), sharex=True)
    L = np.array([r["length"] for r in by_len])
    N = np.array([r["n"] for r in by_len])
    M = np.array([r["mean"] for r in by_len])
    P = np.array([r["epitope_pct"] for r in by_len])

    # 左: 总数 + mean score
    ax1b = ax1.twinx()
    ax1.bar(L - 0.2, N, width=0.4, color="#888", alpha=0.5, label="count")
    ax1b.plot(L, M, "o-", color=COL_AMP, linewidth=2, label="mean score")
    ax1b.axhline(THRESHOLD, color=COL_THR, linestyle="--", alpha=0.6,
                 label=f"threshold = {THRESHOLD}")
    ax1.set_xlabel("peptide length (aa)")
    ax1.set_ylabel("count", color="#888")
    ax1b.set_ylabel("mean score", color=COL_AMP)
    ax1.set_title("Distribution & mean score by length")
    ax1.grid(alpha=0.3)
    ax1.set_xlim(0, 31)

    # 右: epitope 率
    ax2.bar(L, P, color=COL_EPI, alpha=0.7, label="Epitope %")
    ax2.axhline(50, color=COL_THR, linestyle="--", alpha=0.4)
    ax2.set_xlabel("peptide length (aa)")
    ax2.set_ylabel("Epitope %")
    ax2.set_title("Epitope rate by length\n(threshold = 0.1512)")
    ax2.set_ylim(0, 105)
    ax2.grid(alpha=0.3)
    ax2.set_xlim(0, 31)

    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_score_by_length.png", dpi=120)
    plt.close(fig)

    # ============= 图 3: score by source =============
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    S = [r["source"] for r in by_src]
    N_s = np.array([r["n"] for r in by_src])
    M_s = np.array([r["mean"] for r in by_src])
    P_s = np.array([r["epitope_pct"] for r in by_src])

    ypos = np.arange(len(S))
    ax1.barh(ypos, N_s, color="#888", alpha=0.5)
    ax1.set_yticks(ypos)
    ax1.set_yticklabels(S, fontsize=9)
    ax1.invert_yaxis()
    ax1.set_xlabel("count (sampled)")
    ax1.set_xscale("log")
    ax1.set_title("Source distribution (sampled 5%)")
    ax1.grid(alpha=0.3, axis="x")

    ax2.barh(ypos, M_s, color=COL_AMP, alpha=0.7, label="mean score")
    ax2.axvline(THRESHOLD, color=COL_THR, linestyle="--", alpha=0.6,
                label=f"threshold = {THRESHOLD}")
    for i, (m, p) in enumerate(zip(M_s, P_s)):
        ax2.text(m + 0.001, i, f"  epi={p:.1f}%", fontsize=8, va="center", color="#444")
    ax2.set_yticks(ypos)
    ax2.set_yticklabels(S, fontsize=9)
    ax2.invert_yaxis()
    ax2.set_xlabel("mean score")
    ax2.set_title("Mean score & Epitope % by source")
    ax2.grid(alpha=0.3, axis="x")
    ax2.legend(loc="lower right")

    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_score_by_source.png", dpi=120)
    plt.close(fig)

    # ============= 图 4: 三种残基级分数对比 =============
    fig, ax = plt.subplots(figsize=(10, 6))
    sub_idx = np.random.choice(len(score), 50_000, replace=False)
    for data, lbl, color, alpha in [
        (avg_ep[sub_idx], "average_epitope_score", COL_AMP, 0.4),
        (max_ep[sub_idx], "max_epitope_score", COL_EPI, 0.4),
        (max_lin[sub_idx], "max_linear_epitope_score", "#888", 0.4),
    ]:
        kde = gaussian_kde(data, bw_method=0.05)
        xs = np.linspace(0, 1, 400)
        ax.plot(xs, kde(xs), color=color, linewidth=2, label=lbl)
        ax.fill_between(xs, kde(xs), alpha=alpha, color=color)
    ax.axvline(THRESHOLD, color=COL_THR, linestyle="--", linewidth=1.5,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("score value")
    ax.set_ylabel("density")
    ax.set_title("BepiPred-3.0 score variants (n=50k sampled)\n"
                 "average: mean over all residues | max: max residue | "
                 "linear: rolling-window max (window=7)")
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3)
    ax.set_xlim(0, 0.5)
    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_per_residue_stats.png", dpi=120)
    plt.close(fig)

    # ============= 图 5: Epitope vs Non-epitope box =============
    fig, ax = plt.subplots(figsize=(8, 6))
    bp = ax.boxplot([pos_score, neg_score],
                    tick_labels=[f"Epitope (n={len(pos_score):,})",
                                 f"Non-epitope (n={len(neg_score):,})"],
                    patch_artist=True, widths=0.5)
    for patch, color in zip(bp["boxes"], [COL_EPI, COL_NONEPI]):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
    ax.axhline(THRESHOLD, color=COL_THR, linestyle="--", linewidth=1.5,
               label=f"threshold = {THRESHOLD}")
    ax.set_ylabel("score")
    ax.set_title("BepiPred-3.0 score: Epitope vs Non-epitope\n"
                 "(label assigned by threshold 0.1512, hence bimodal)")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_label_box.png", dpi=120)
    plt.close(fig)

    # ============= 图 6: ECDF + percentile markers =============
    fig, ax = plt.subplots(figsize=(10, 6))
    sub = np.random.choice(score, 100_000, replace=False)
    sub_sorted = np.sort(sub)
    ecdf = np.arange(1, len(sub_sorted) + 1) / len(sub_sorted)
    ax.plot(sub_sorted, ecdf, linewidth=2, color=COL_AMP, label="ECDF (100k sampled)")
    for q, lbl in [("p01", "p01"), ("p25", "p25"), ("p50", "p50"), ("p75", "p75"), ("p99", "p99")]:
        v = summary[f"score_{q}"]
        ax.axvline(v, color="#888", linestyle=":", alpha=0.7)
        ax.text(v, 0.05, f"  {lbl}={v:.3f}", rotation=90, fontsize=9, va="bottom")
    ax.axvline(THRESHOLD, color=COL_THR, linestyle="--", linewidth=1.5,
               label=f"Epitope threshold = {THRESHOLD}")
    ax.set_xlabel("score")
    ax.set_ylabel("cumulative fraction")
    ax.set_title(f"BepiPred-3.0 score ECDF (full corpus n={summary['n_total']:,})")
    ax.legend(loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / "bepipred3_score_ecdf.png", dpi=120)
    plt.close(fig)

    print("plots saved:")
    for f in sorted(HERE.glob("bepipred3_*.png")):
        print(f"  {f.name}  ({f.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()