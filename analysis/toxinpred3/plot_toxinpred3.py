"""
plot_toxinpred3.py — ToxinPred3 预测结果统计可视化

输入: /tmp/tox_score_by_len.csv (n=20.25M)
       /tmp/tox_by_source.csv (n=20.25M, 690MB, 带 source)
       /tmp/tox_sample.csv     (n=20.25M, 全量带 det_len)

输出: /home/lenovo/Projects/iGEM-platform/analysis/toxinpred3/*.png
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.colors import LogNorm

OUT_DIR = Path("/home/lenovo/Projects/iGEM-platform/analysis/toxinpred3")
OUT_DIR.mkdir(parents=True, exist_ok=True)

THRESHOLD = 0.38  # ToxinPred3 默认阈值 (service.py)

plt.rcParams.update({
    "figure.figsize": (10, 6),
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


def load_score_by_len() -> pd.DataFrame:
    print(f"loading tox_score_by_len.csv ...", flush=True)
    df = pd.read_csv("/tmp/tox_score_by_len.csv")
    print(f"  shape={df.shape} mem={df.memory_usage(deep=True).sum()/1024**3:.2f} GB", flush=True)
    return df


def load_by_source() -> pd.DataFrame:
    print(f"loading tox_by_source.csv ...", flush=True)
    df = pd.read_csv(
        "/tmp/tox_by_source.csv",
        dtype={"source": "category", "seq_len": "int16", "label": "category"},
    )
    print(f"  shape={df.shape}", flush=True)
    return df


# ===================================================================== fig 1
def fig_score_histogram(df: pd.DataFrame):
    """Score 全量分布直方图 + Toxin/Non-Toxin 区域着色"""
    fig, ax = plt.subplots(figsize=(11, 6))
    score = df["score"].to_numpy()
    n_total = len(score)
    n_toxin = int((score >= THRESHOLD).sum())
    n_non = n_total - n_toxin

    bins = np.linspace(0, 1, 101)
    counts, edges = np.histogram(score, bins=bins)
    centers = 0.5 * (edges[:-1] + edges[1:])

    color_toxin = "#d62728"
    color_non = "#1f77b4"
    bar_colors = [color_toxin if c >= THRESHOLD else color_non for c in centers]
    ax.bar(centers, counts, width=(edges[1] - edges[0]), color=bar_colors,
           edgecolor="none", alpha=0.92)

    ax.axvline(THRESHOLD, color="black", linestyle="--", linewidth=1.6,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("ToxinPred3 score (toxicity probability)")
    ax.set_ylabel("Count (peptides)")
    ax.set_xlim(0, 1)
    title = (
        f"ToxinPred3 score distribution  |  n = {n_total:,}\n"
        f"Toxin (score ≥ {THRESHOLD}): {n_toxin:,} ({n_toxin/n_total*100:.2f}%)   "
        f"Non-Toxin: {n_non:,} ({n_non/n_total*100:.2f}%)"
    )
    ax.set_title(title)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
    ax.legend(loc="upper right")
    fig.text(0.5, -0.02, "Bars to the right of the dashed line are predicted Toxin.",
             ha="center", fontsize=9, style="italic", color="gray")
    fig.savefig(OUT_DIR / "01_score_histogram.png")
    plt.close(fig)
    print(f"  -> 01_score_histogram.png")


# ===================================================================== fig 2
def fig_score_by_length(df_by_len: pd.DataFrame):
    """不同 length 段的 score 分布 (boxplot)"""
    df = df_by_len.copy()
    # 按 5 aa 一段分桶
    df["len_bucket"] = pd.cut(
        df["seq_len"], bins=[0, 5, 10, 15, 20, 25, 30, 50],
        labels=["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"],
        right=True, include_lowest=True,
    )
    # 为防止极端值主导,boxplot 用 1-99 percentile 截断
    fig, ax = plt.subplots(figsize=(12, 6))
    order = ["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"]
    present = [b for b in order if (df["len_bucket"] == b).any()]
    # 用 sample 防止内存炸
    sampled = df.groupby("len_bucket", observed=True).sample(n=min(50000, df.groupby("len_bucket", observed=True).size().min()),
                                                              random_state=42)
    data = [sampled.loc[sampled["len_bucket"] == b, "score"].to_numpy() for b in present]
    bp = ax.boxplot(data, tick_labels=present, showfliers=False, patch_artist=True,
                    widths=0.6)
    for patch in bp["boxes"]:
        patch.set_facecolor("#aec7e8")
    ax.axhline(THRESHOLD, color="#d62728", linestyle="--", linewidth=1.5,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("Peptide length (aa)")
    ax.set_ylabel("ToxinPred3 score")
    ax.set_title("Score distribution by peptide length (50k sample per bucket)")
    ax.legend(loc="upper right")
    fig.savefig(OUT_DIR / "02_score_by_length_box.png")
    plt.close(fig)
    print(f"  -> 02_score_by_length_box.png")


# ===================================================================== fig 3
def fig_toxin_rate_by_length(df_by_len: pd.DataFrame):
    """各 length 段 Toxin 比例 + n"""
    df = df_by_len.copy()
    df["len_bucket"] = pd.cut(
        df["seq_len"], bins=[0, 5, 10, 15, 20, 25, 30, 50],
        labels=["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"],
        right=True, include_lowest=True,
    )
    grp = df.groupby("len_bucket", observed=True)
    stats = pd.DataFrame({
        "n": grp.size(),
        "n_toxin": grp.apply(lambda x: (x["score"] >= THRESHOLD).sum(), include_groups=False),
        "pct_toxin": grp.apply(lambda x: (x["score"] >= THRESHOLD).mean() * 100, include_groups=False),
        "mean_score": grp["score"].mean(),
    }).reset_index()
    stats["n_M"] = stats["n"] / 1e6

    fig, ax1 = plt.subplots(figsize=(11, 6))
    color_rate = "#d62728"
    color_n = "#1f77b4"
    ax1.bar(stats["len_bucket"].astype(str), stats["pct_toxin"],
            color=color_rate, alpha=0.75, label="% Toxin")
    for x, y in zip(stats["len_bucket"].astype(str), stats["pct_toxin"]):
        ax1.text(x, y + 0.5, f"{y:.1f}%", ha="center", fontsize=9)
    ax1.set_ylabel("% predicted Toxin", color=color_rate)
    ax1.set_ylim(0, max(stats["pct_toxin"]) * 1.2)
    ax1.tick_params(axis="y", labelcolor=color_rate)
    ax1.set_xlabel("Peptide length (aa)")
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=1))

    ax2 = ax1.twinx()
    ax2.plot(stats["len_bucket"].astype(str), stats["n_M"], color=color_n, marker="o",
             linewidth=2, label="# peptides (M)")
    for x, y in zip(stats["len_bucket"].astype(str), stats["n_M"]):
        ax2.text(x, y + 0.4, f"{y:.2f}M", ha="center", fontsize=9, color=color_n)
    ax2.set_ylabel("# peptides (millions)", color=color_n)
    ax2.tick_params(axis="y", labelcolor=color_n)

    ax1.set_title("ToxinPred3 prediction: rate and sample size by length")
    fig.legend(loc="upper right", bbox_to_anchor=(0.92, 0.92))
    fig.savefig(OUT_DIR / "03_toxin_rate_by_length.png")
    plt.close(fig)
    print(f"  -> 03_toxin_rate_by_length.png")


# ===================================================================== fig 4
def fig_toxin_rate_by_source(df_src: pd.DataFrame):
    """各 source 的 Toxin 比例 + n"""
    grp = df_src.groupby("source", observed=True)
    stats = pd.DataFrame({
        "n": grp.size(),
        "n_toxin": grp.apply(lambda x: (x["score"] >= THRESHOLD).sum(), include_groups=False),
        "pct_toxin": grp.apply(lambda x: (x["score"] >= THRESHOLD).mean() * 100, include_groups=False),
        "mean_score": grp["score"].mean(),
    }).reset_index().sort_values("pct_toxin", ascending=False)

    fig, ax1 = plt.subplots(figsize=(12, 6))
    color_rate = "#d62728"
    color_n = "#1f77b4"
    xpos = np.arange(len(stats))
    ax1.bar(xpos, stats["pct_toxin"], color=color_rate, alpha=0.75, label="% Toxin")
    for x, y in zip(xpos, stats["pct_toxin"]):
        ax1.text(x, y + 1, f"{y:.1f}%", ha="center", fontsize=9)
    ax1.set_xticks(xpos)
    ax1.set_xticklabels(stats["source"], rotation=30, ha="right")
    ax1.set_ylabel("% predicted Toxin", color=color_rate)
    ax1.set_ylim(0, max(stats["pct_toxin"]) * 1.25)
    ax1.tick_params(axis="y", labelcolor=color_rate)
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=1))

    ax2 = ax1.twinx()
    ax2.plot(xpos, stats["n"] / 1e6, color=color_n, marker="o", linewidth=2,
             label="# peptides (M)")
    for x, y in zip(xpos, stats["n"] / 1e6):
        ax2.text(x, y + 0.3, f"{y:.2f}M", ha="center", fontsize=9, color=color_n)
    ax2.set_ylabel("# peptides (millions)", color=color_n)
    ax2.tick_params(axis="y", labelcolor=color_n)

    ax1.set_title("ToxinPred3 prediction rate by source database")
    fig.savefig(OUT_DIR / "04_toxin_rate_by_source.png")
    plt.close(fig)
    print(f"  -> 04_toxin_rate_by_source.png")


# ===================================================================== fig 5
def fig_score_heatmap_len_x_source(df_src: pd.DataFrame):
    """length × source 的 mean score 热图(用 source 中 sample 最多的几个)"""
    # 只保留 n>=10000 的 source,减少空格子
    counts = df_src.groupby("source", observed=True).size()
    keep = counts[counts >= 10000].index.tolist()
    sub = df_src[df_src["source"].isin(keep)].copy()

    # length bucket
    sub["len_bucket"] = pd.cut(
        sub["seq_len"], bins=[0, 5, 10, 15, 20, 25, 30, 50],
        labels=["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"],
        right=True, include_lowest=True,
    )
    pivot_mean = sub.pivot_table(index="source", columns="len_bucket",
                                 values="score", aggfunc="mean", observed=True)
    pivot_pct = sub.pivot_table(
        index="source", columns="len_bucket", values="score",
        aggfunc=lambda x: (x >= THRESHOLD).mean() * 100, observed=True,
    )

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    for ax, data, title, cbar_label, fmt, vmin, vmax, cmap in [
        (axes[0], pivot_mean, "Mean ToxinPred3 score", "mean score", ".2f", 0, 1, "viridis"),
        (axes[1], pivot_pct, "% predicted Toxin", "% Toxin", ".1f", 0, 100, "YlOrRd"),
    ]:
        im = ax.imshow(data.values, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_xticks(range(len(data.columns)))
        ax.set_xticklabels([str(c) for c in data.columns])
        ax.set_yticks(range(len(data.index)))
        ax.set_yticklabels(data.index)
        ax.set_xlabel("Peptide length (aa)")
        ax.set_ylabel("Source")
        ax.set_title(title)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                val = data.values[i, j]
                if np.isnan(val):
                    ax.text(j, i, "—", ha="center", va="center", color="gray")
                    continue
                txt_color = "white" if (vmax - val) / max(vmax - vmin, 1) < 0.5 else "black"
                ax.text(j, i, format(val, fmt), ha="center", va="center",
                        color=txt_color, fontsize=9)
        fig.colorbar(im, ax=ax, label=cbar_label, fraction=0.046, pad=0.04)
    fig.suptitle("ToxinPred3 patterns: source × peptide length", y=1.02, fontsize=13)
    fig.savefig(OUT_DIR / "05_heatmap_len_source.png")
    plt.close(fig)
    print(f"  -> 05_heatmap_len_source.png")


# ===================================================================== fig 6
def fig_score_density(df_by_len: pd.DataFrame):
    """KDE 各 length 段 score 密度"""
    df = df_by_len.copy()
    df["len_bucket"] = pd.cut(
        df["seq_len"], bins=[0, 5, 10, 15, 20, 25, 30, 50],
        labels=["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"],
        right=True, include_lowest=True,
    )
    order = ["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"]
    present = [b for b in order if (df["len_bucket"] == b).any()]
    sampled = df.groupby("len_bucket", observed=True).sample(
        n=min(80000, df.groupby("len_bucket", observed=True).size().min()),
        random_state=42)

    fig, ax = plt.subplots(figsize=(12, 6))
    cmap = plt.colormaps["viridis_r"]
    for i, b in enumerate(present):
        sub = sampled.loc[sampled["len_bucket"] == b, "score"].to_numpy()
        if len(sub) == 0:
            continue
        try:
            sub.plot(kind="kde", ax=ax, label=f"{b} (n={int(((df['len_bucket']==b)).sum()):,})",
                      color=cmap(i / max(1, len(present) - 1)), linewidth=2)
        except Exception:
            continue
    ax.axvline(THRESHOLD, color="black", linestyle="--", linewidth=1.6,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("ToxinPred3 score")
    ax.set_ylabel("Density (KDE)")
    ax.set_title("Score density by peptide length (80k sample per bucket)")
    ax.set_xlim(0, 1)
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(OUT_DIR / "06_score_density_by_length.png")
    plt.close(fig)
    print(f"  -> 06_score_density_by_length.png")


# ===================================================================== fig 7
def fig_score_ecdf(df_by_len: pd.DataFrame):
    """ECDF: 全量 + 各 length 段"""
    df = df_by_len.copy()
    df["len_bucket"] = pd.cut(
        df["seq_len"], bins=[0, 5, 10, 15, 20, 25, 30, 50],
        labels=["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"],
        right=True, include_lowest=True,
    )
    fig, ax = plt.subplots(figsize=(11, 6))
    # 全量 ECDF: 排序后取 1000 均匀点
    all_score = np.sort(df["score"].to_numpy())
    q = np.linspace(0, 1, 1001)
    sample = np.quantile(all_score, q)
    ax.plot(q, sample, color="black", linewidth=2.2, label="ALL (20.25M)")
    for i, b in enumerate(["1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31+"]):
        sub = df.loc[df["len_bucket"] == b, "score"]
        if len(sub) == 0:
            continue
        s = np.sort(sub.to_numpy())
        q2 = np.linspace(0, 1, 200)
        ax.plot(q2, np.quantile(s, q2),
                label=f"{b} (n={len(sub):,})",
                linewidth=1.5,
                color=plt.colormaps["viridis_r"](i / 6))
    ax.axvline(1 - THRESHOLD, color="#d62728", linestyle="--", alpha=0.4,
               label=f"threshold = {THRESHOLD}")
    # 也画水平线标阈值
    ax.axhline(THRESHOLD, color="#d62728", linestyle=":", alpha=0.4)
    ax.set_xlabel("Quantile")
    ax.set_ylabel("Score (quantile)")
    ax.set_title("ECDF of ToxinPred3 scores — overall and by length")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left", fontsize=8)
    fig.savefig(OUT_DIR / "07_score_ecdf.png")
    plt.close(fig)
    print(f"  -> 07_score_ecdf.png")


# ===================================================================== fig 8
def fig_score_2d_hist(df_by_len: pd.DataFrame):
    """2D 直方图: score vs length, log 色彩"""
    fig, ax = plt.subplots(figsize=(12, 6))
    sub = df_by_len.sample(n=min(2_000_000, len(df_by_len)), random_state=42)
    h = ax.hist2d(sub["seq_len"], sub["score"],
                  bins=[30, 100], cmap="viridis", norm=LogNorm())
    fig.colorbar(h[3], ax=ax, label="Count (log scale)")
    ax.axhline(THRESHOLD, color="white", linestyle="--", linewidth=1.6,
               label=f"threshold = {THRESHOLD}")
    ax.set_xlabel("Peptide length (aa)")
    ax.set_ylabel("ToxinPred3 score")
    ax.set_title("Joint distribution of peptide length × score (2M sample, log color)")
    ax.legend(loc="upper right")
    fig.savefig(OUT_DIR / "08_score_vs_length_2d.png")
    plt.close(fig)
    print(f"  -> 08_score_vs_length_2d.png")


def main():
    df_by_len = load_score_by_len()
    df_src = load_by_source()
    print("plotting ...")

    print("[1/8] score histogram")
    fig_score_histogram(df_by_len)
    print("[2/8] score by length (box)")
    fig_score_by_length(df_by_len)
    print("[3/8] toxin rate by length")
    fig_toxin_rate_by_length(df_by_len)
    print("[4/8] toxin rate by source")
    fig_toxin_rate_by_source(df_src)
    print("[5/8] heatmap len × source")
    fig_score_heatmap_len_x_source(df_src)
    print("[6/8] score density by length")
    fig_score_density(df_by_len)
    print("[7/8] score ECDF")
    fig_score_ecdf(df_by_len)
    print("[8/8] score × length 2D")
    fig_score_2d_hist(df_by_len)

    print("DONE. figures in", OUT_DIR)


if __name__ == "__main__":
    main()