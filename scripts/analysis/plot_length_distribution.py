#!/usr/bin/env python3
"""
plot_length_distribution.py — peptide 长度分布(3..30 aa)

输出:
  - length_distribution.png        bar + 累计比例(双轴)
  - length_distribution_log.png    log scale bar(看清楚短肽)
  - length_distribution.csv        表
"""
from __future__ import annotations

import csv
from pathlib import Path

import psycopg2
import psycopg2.extras

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
OUT_DIR = Path("results/plots/library")


def fetch_lengths() -> list[tuple[int, int]]:
    with psycopg2.connect(DSN) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT length, count(*) AS rows "
                "FROM peptides WHERE length BETWEEN 3 AND 30 "
                "GROUP BY length ORDER BY length"
            )
            return [(r["length"], r["rows"]) for r in cur.fetchall()]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch_lengths()
    lens = np.array([d[0] for d in data])
    counts = np.array([d[1] for d in data], dtype=np.int64)
    total = int(counts.sum())
    cum_pct = np.cumsum(counts) / total * 100.0

    print(f"total rows 3..30: {total:,}")
    print(f"short (3..19):    {int(counts[:17].sum()):,}  ({counts[:17].sum()/total*100:.2f}%)")
    print(f"long  (20..30):   {int(counts[17:].sum()):,}  ({counts[17:].sum()/total*100:.2f}%)")

    # --- 图 1: bar + 累计曲线(双轴)
    fig, ax1 = plt.subplots(figsize=(11, 6))
    bars = ax1.bar(lens, counts, color="#4C78A8", edgecolor="white", linewidth=0.4)
    ax1.set_xlabel("peptide length (aa)")
    ax1.set_ylabel("row count", color="#4C78A8")
    ax1.set_xticks(lens)
    ax1.tick_params(axis="y", labelcolor="#4C78A8")
    ax1.set_axisbelow(True)
    ax1.grid(True, axis="y", alpha=0.25, linewidth=0.5)

    # 每根柱子顶部标数
    for b, c in zip(bars, counts):
        if c >= total * 0.005:
            ax1.text(b.get_x() + b.get_width()/2, b.get_height(),
                     f"{c/1e6:.2f}M" if c >= 1e6 else f"{c/1e3:.0f}K",
                     ha="center", va="bottom", fontsize=7, color="#1f3a5f")

    ax2 = ax1.twinx()
    ax2.plot(lens, cum_pct, color="#E45756", marker="o", linewidth=2, markersize=5)
    ax2.set_ylabel("cumulative %", color="#E45756")
    ax2.tick_params(axis="y", labelcolor="#E45756")
    ax2.set_ylim(0, 105)
    ax2.axhline(50, color="#E45756", linestyle=":", alpha=0.3)
    ax2.axhline(95, color="#E45756", linestyle=":", alpha=0.3)

    ax1.set_title(f"peptide length distribution  (total = {total:,})",
                  fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "length_distribution.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # --- 图 2: log scale bar(看清短肽)
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.bar(lens, counts, color="#6BAED6", edgecolor="white", linewidth=0.4)
    ax.set_yscale("log")
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("row count (log)")
    ax.set_xticks(lens)
    ax.set_title(f"peptide length distribution (log scale)  total = {total:,}", fontsize=13)
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.5, which="both")
    for x, c in zip(lens, counts):
        if c > 0:
            ax.text(x, c, f"{c:,}", ha="center", va="bottom",
                    fontsize=7, color="#1f3a5f", rotation=0)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "length_distribution_log.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # --- CSV
    csv_path = OUT_DIR / "length_distribution.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["length", "rows", "pct", "cumulative_pct"])
        cum = 0
        for L, c in zip(lens, counts):
            cum += c
            w.writerow([int(L), int(c), round(100*c/total, 4), round(100*cum/total, 4)])
    print(f"wrote {csv_path}")

    print("wrote:")
    print(" ", OUT_DIR / "length_distribution.png")
    print(" ", OUT_DIR / "length_distribution_log.png")


if __name__ == "__main__":
    main()