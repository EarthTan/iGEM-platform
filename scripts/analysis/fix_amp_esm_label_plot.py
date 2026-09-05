#!/usr/bin/env python3
"""
修正 amp_esm_score_by_label.png 的可视化 bug:
  - 强制 x 轴 [0, 1],否则红色 AMP 区间被裁
  - 用双图(分轴 + 合轴)展示,避免覆盖
"""
import sys
from pathlib import Path
import numpy as np
import psycopg2
import psycopg2.extras
from scipy.stats import gaussian_kde
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
OUT = Path("results/plots/amp_esm/amp_esm_score_by_label_fixed.png")


def fetch():
    with psycopg2.connect(DSN) as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT score, label FROM peptide_enrichment
             WHERE tool='amp-esm' AND score IS NOT NULL
             ORDER BY random() LIMIT 200000
        """)
        rows = cur.fetchall()
    return (np.array([r["score"] for r in rows], dtype=np.float32),
            np.array([r["label"] or "" for r in rows]))


def kde(data, n_sub=50000):
    if len(data) > n_sub:
        data = np.random.default_rng(0).choice(data, n_sub, replace=False)
    kde = gaussian_kde(data, bw_method="scott")
    xs = np.linspace(0, 1, 400)
    return xs, kde(xs)


def main():
    score, label = fetch()
    amp = score[label == "AMP"]
    non = score[label == "non-AMP"]

    # 上图:合轴 KDE(同一 [0,1] x 轴),密度各自归一化
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(11, 9), sharex=True,
                                        gridspec_kw=dict(height_ratios=[2, 1.2]))

    for data, color, name in [(amp, "#E45756", "AMP"),
                              (non, "#54A24B", "non-AMP")]:
        xs, ys = kde(data)
        ax_top.plot(xs, ys, color=color, linewidth=2.2, label=f"{name} (n={len(data):,})")
        ax_top.fill_between(xs, 0, ys, color=color, alpha=0.20)
        # 底部 ECDF
        sorted_data = np.sort(data)
        ecdf = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
        ax_bot.plot(sorted_data, ecdf, color=color, linewidth=1.8, label=name)

    ax_top.axvline(0.5, color="grey", linestyle="--", alpha=0.6, label="threshold 0.5")
    ax_top.set_xlim(0, 1)
    ax_top.set_ylabel("density")
    ax_top.set_title("amp-esm score: AMP vs non-AMP (n=%d, sampled)" % len(score))
    ax_top.legend(loc="upper center")
    ax_top.grid(True, alpha=0.25, linewidth=0.5)

    ax_bot.axvline(0.5, color="grey", linestyle="--", alpha=0.6)
    ax_bot.set_xlim(0, 1)
    ax_bot.set_ylim(0, 1.02)
    ax_bot.set_xlabel("amp-esm score")
    ax_bot.set_ylabel("ECDF")
    ax_bot.legend(loc="upper center")
    ax_bot.grid(True, alpha=0.25, linewidth=0.5)

    fig.tight_layout()
    fig.savefig(OUT, dpi=130, bbox_inches="tight")
    print("wrote", OUT)


if __name__ == "__main__":
    main()