#!/usr/bin/env python3
"""
plot_mhcflurry_multi.py
针对 mhcflurry 多 allele 全量重算结果,画跨 allele 聚合的免疫原性风险分布图,
与单 allele baseline 对比,产物写入 results/plots/mhcflurry_multi/。

聚合口径:
  - best:        跨 allele 取 max(score) / min(affinity_nM)  -> "广谱免疫原性风险上限"
  - n_bound:     跨 allele 中 affinity_nM <= 500 的 allele 数 (0..N)
  - n_strong:    跨 allele 中 affinity_nM <= 50 的 allele 数 (0..N)
  - consensus_3: n_bound >= 3 视为 "consensus binder" (跨人群一致)
  - consensus_5: n_bound >= 5 视为 "强共识 binder"

对比 baseline:
  - baseline 是 tool='mhcflurry' 单 allele A*02:01 baseline
  - 多 allele best 是"在 23 个 allele 中任一能结合"的最坏情况

图:
  1. mhcflurry_multi_overview.png     - 2x2 dashboard
  2. mhcflurry_multi_allele_yields.png  - 每个 allele 的 binder 率(对比 baseline A*02:01)
  3. mhcflurry_multi_best_distribution.png  - 跨 allele best 后的 score 分布(直方图)
  4. mhcflurry_multi_consensus_burden.png  - n_bound 的分布(strip / hist)
  5. mhcflurry_multi_length_heatmap.png  - 长度 × best label 堆叠 + heatmap

数据: peptide_enrichment WHERE tool LIKE 'mhcflurry-A%' (23 个派生 tool)
      + baseline WHERE tool = 'mhcflurry' (A*02:01)

用法:
  /tmp/plot_venv/bin/python scripts/analysis/plot_mhcflurry_multi.py
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from matplotlib.colors import LogNorm
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("plot-mhcflurry-multi")

DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"

# 经典 MHC 阈值 (nM)
WEAK_CUTOFF_NM   = 500.0
STRONG_CUTOFF_NM = 50.0

# 标签配色
LABEL_COLOR = {
    "Non-Binder":    "#9CA3AF",
    "Weak Binder":   "#F4B183",
    "Strong Binder": "#4C8DBC",
}
LABEL_ORDER = ["Non-Binder", "Weak Binder", "Strong Binder"]


# ---------------------------------------------------------------- data layer

def fetch_multi_allele(cur) -> dict:
    """拉 23 个 mhcflurry-A* 派生 tool 的全量数据(全量拉,3.1M 行内存足够)。"""
    cur.execute("""
        SELECT peptide_id,
               tool,
               score,
               label,
               (details->>'affinity_nM')::float   AS affinity_nm,
               (details->>'peptide_length')::int AS pep_len,
               details->>'allele'                AS allele
          FROM peptide_enrichment
         WHERE tool LIKE 'mhcflurry-%'
           AND tool <> 'mhcflurry'
    """)
    rows = cur.fetchall()
    n = len(rows)
    log.info("fetched %d rows from peptide_enrichment WHERE tool LIKE 'mhcflurry-%%' AND tool <> 'mhcflurry'", n)
    if n == 0:
        raise RuntimeError(
            "no mhcflurry-A* rows. did you run src/setup/enrich/run_multi_allele.sh?"
        )
    return {
        "peptide_id":  np.fromiter((r["peptide_id"]  for r in rows), dtype=np.int64, count=n),
        "tool":        np.array([r["tool"]            for r in rows], dtype=object),
        "score":       np.fromiter((r["score"]         for r in rows), dtype=np.float32, count=n),
        "label":       np.array([r["label"]           for r in rows], dtype=object),
        "affinity_nm": np.fromiter((r["affinity_nm"]   for r in rows), dtype=np.float32, count=n),
        "pep_len":     np.fromiter((r["pep_len"]            for r in rows), dtype=np.int16, count=n),
        "allele":      np.array([r["allele"]          for r in rows], dtype=object),
    }


def fetch_baseline(cur) -> dict:
    """拉 baseline tool='mhcflurry' (A*02:01 single allele)。"""
    cur.execute("""
        SELECT peptide_id,
               score,
               label,
               (details->>'affinity_nM')::float AS affinity_nm,
               (details->>'peptide_length')::int AS pep_len
          FROM peptide_enrichment
         WHERE tool = 'mhcflurry'
    """)
    rows = cur.fetchall()
    n = len(rows)
    log.info("fetched %d baseline rows from peptide_enrichment WHERE tool='mhcflurry'", n)
    return {
        "peptide_id":  np.fromiter((r["peptide_id"]  for r in rows), dtype=np.int64, count=n),
        "score":       np.fromiter((r["score"]         for r in rows), dtype=np.float32, count=n),
        "label":       np.array([r["label"]           for r in rows], dtype=object),
        "affinity_nm": np.fromiter((r["affinity_nm"]   for r in rows), dtype=np.float32, count=n),
        "pep_len":     np.fromiter((r["pep_len"]            for r in rows), dtype=np.int16, count=n),
    }


# ---------------------------------------------------------------- aggregation

def aggregate_per_peptide(multi: dict) -> dict:
    """对每个 peptide 跨 23 个 allele 聚合。
    输出 per-peptide 视角:
      best_score:   max(score)
      best_aff_nm:  min(affinity_nm)  (>=0)
      best_label:   Strong/Weak/Non 由 best_aff_nm 决定(<=50/<=500/>500)
      n_bound:      count(allele with aff <= 500)
      n_strong:     count(allele with aff <= 50)
      consensus_3:  n_bound >= 3
      consensus_5:  n_bound >= 5
    """
    log.info("aggregating per-peptide across alleles ...")
    pid = multi["peptide_id"]
    allele = multi["tool"]
    score = multi["score"]
    aff = multi["affinity_nm"]
    plen = multi["pep_len"]

    # group by peptide_id
    order = np.argsort(pid, kind="stable")
    pid_sorted = pid[order]
    allele_sorted = allele[order]
    score_sorted = score[order]
    aff_sorted = aff[order]
    plen_sorted = plen[order]

    # 找 unique peptide_id 边界
    boundaries = np.where(np.diff(pid_sorted) != 0)[0] + 1
    groups = np.split(np.arange(len(pid)), boundaries)

    n = len(groups)
    out = {
        "peptide_id":     np.empty(n, dtype=np.int64),
        "best_score":     np.empty(n, dtype=np.float32),
        "best_aff_nm":    np.empty(n, dtype=np.float32),
        "best_label":     np.empty(n, dtype=object),
        "n_bound":        np.empty(n, dtype=np.int16),
        "n_strong":       np.empty(n, dtype=np.int16),
        "pep_len":        np.empty(n, dtype=np.int16),
        "any_allele":     [None] * n,
    }

    for i, g in enumerate(groups):
        pid_g = pid_sorted[g[0]]
        scores_g = score_sorted[g]
        affs_g = aff_sorted[g]
        plen_g = plen_sorted[g][0]
        alleles_g = allele_sorted[g]

        # valid: affinity > 0(排除 Invalid Length 类的 0)
        valid = affs_g > 0
        if valid.any():
            best_idx = np.argmin(affs_g)
            best_score = float(scores_g[best_idx])
            best_aff = float(affs_g[best_idx])
            best_allele = alleles_g[best_idx]
        else:
            best_score = 0.0
            best_aff = 36500.0
            best_allele = ""

        # label from best affinity
        if best_aff <= STRONG_CUTOFF_NM:
            best_label = "Strong Binder"
        elif best_aff <= WEAK_CUTOFF_NM:
            best_label = "Weak Binder"
        else:
            best_label = "Non-Binder"

        n_bound  = int((affs_g <= WEAK_CUTOFF_NM).sum())
        n_strong = int((affs_g <= STRONG_CUTOFF_NM).sum())

        out["peptide_id"][i]  = pid_g
        out["best_score"][i]  = best_score
        out["best_aff_nm"][i] = best_aff
        out["best_label"][i]  = best_label
        out["n_bound"][i]     = n_bound
        out["n_strong"][i]    = n_strong
        out["pep_len"][i]     = plen_g
        out["any_allele"][i]  = best_allele
    log.info("aggregated %d unique peptides from %d total rows", n, len(pid))
    return out


# ---------------------------------------------------------------- figures

def fig_allele_yields(multi, baseline, out_dir: Path) -> dict:
    """每个 allele 的 binder 率横向条形图,与 baseline A*02:01 对比。"""
    tools = sorted(set(multi["tool"]))
    # counts per tool per label
    rates = {}  # rates[tool] = (n_strong, n_weak, n_non, total, pct_strong, pct_weak)
    for t in tools:
        m = multi["tool"] == t
        s = (multi["label"] == "Strong Binder")[m].sum()
        w = (multi["label"] == "Weak Binder")[m].sum()
        nb = (multi["label"] == "Non-Binder")[m].sum()
        total = int(m.sum())
        rates[t] = (int(s), int(w), int(nb), total,
                    100.0 * s / total if total else 0,
                    100.0 * w / total if total else 0)

    # baseline
    b_strong = (baseline["label"] == "Strong Binder").sum()
    b_weak   = (baseline["label"] == "Weak Binder").sum()
    b_total  = len(baseline["label"])

    fig, ax = plt.subplots(figsize=(11, 8.0))

    y = np.arange(len(tools))
    # 把 allele 名清理显示
    labels_disp = [t.replace("mhcflurry-", "") for t in tools]

    strong_pcts = [rates[t][4] for t in tools]
    weak_pcts   = [rates[t][5] for t in tools]

    # bar: stacked strong + weak
    ax.barh(y, strong_pcts, color=LABEL_COLOR["Strong Binder"],
            edgecolor="white", linewidth=0.4, label="Strong Binder (≤50 nM)")
    ax.barh(y, weak_pcts, left=strong_pcts, color=LABEL_COLOR["Weak Binder"],
            edgecolor="white", linewidth=0.4, label="Weak Binder (50–500 nM)")

    # 在条上标 strong 数字
    for i, t in enumerate(tools):
        ns, nw = rates[t][0], rates[t][1]
        if ns > 0:
            ax.text(strong_pcts[i] / 2, i, f"{ns}", ha="center", va="center",
                    fontsize=8, color="white", fontweight="bold")
        if nw > 0:
            x_pos = strong_pcts[i] + weak_pcts[i] / 2
            ax.text(x_pos, i, f"{nw}", ha="center", va="center",
                    fontsize=8, color="#1F2937")

    # baseline 参考线
    b_pct_strong = 100.0 * b_strong / b_total
    b_pct_weak   = 100.0 * b_weak   / b_total
    # 一条虚线表示 baseline A*02:01 的 strong %
    ax.axvline(b_pct_strong, color=LABEL_COLOR["Strong Binder"],
               linestyle="--", linewidth=1.4, alpha=0.7)
    ax.text(b_pct_strong, len(tools) + 0.3,
            f" baseline A*02:01\n strong={b_pct_strong:.2f}%\n weak={b_pct_weak:.2f}%",
            ha="left", va="bottom", fontsize=8, color="#1F2937",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="#9CA3AF", alpha=0.85))

    ax.set_yticks(y)
    ax.set_yticklabels(labels_disp, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("binder rate (%)")
    ax.set_title(f"per-allele binder rate (n=134,888 peptides × {len(tools)} alleles)",
                 fontsize=12)
    ax.grid(True, axis="x", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=True, fontsize=9)
    ax.set_xlim(0, max(strong_pcts + weak_pcts + [b_pct_strong + b_pct_weak]) * 1.15)

    fig.tight_layout()
    out_png = out_dir / "mhcflurry_multi_allele_yields.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    return {"per_allele": rates, "baseline_a02": {"strong": int(b_strong), "weak": int(b_weak), "total": int(b_total)}}


def fig_best_distribution(per_pep, out_dir: Path) -> dict:
    """best 视角下 score / affinity_nM 的总分布(直方图 + KDE)。"""
    s = per_pep["best_score"]
    a = per_pep["best_aff_nm"]
    labels = per_pep["best_label"]

    fig = plt.figure(figsize=(12, 6))
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1, 1], wspace=0.28)
    ax_s = fig.add_subplot(gs[0, 0])
    ax_a = fig.add_subplot(gs[0, 1])

    # 左:best score 直方图,带 label 颜色
    bins = np.linspace(0, 1, 51)
    for lab in LABEL_ORDER:
        m = labels == lab
        if m.sum() == 0:
            continue
        ax_s.hist(s[m], bins=bins, alpha=0.7, color=LABEL_COLOR[lab],
                  edgecolor="white", linewidth=0.3, label=f"{lab} (n={m.sum():,})")

    ax_s.set_xlabel("best score (max across alleles)")
    ax_s.set_ylabel("count")
    ax_s.set_yscale("log")
    ax_s.set_title("per-peptide BEST score (best-case binder across alleles)",
                   fontsize=12)
    ax_s.legend(loc="upper center", frameon=True, fontsize=9)
    ax_s.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax_s.set_axisbelow(True)

    # 右:best affinity_nM 的 ECDF
    sort_idx = np.argsort(a)
    a_sorted = a[sort_idx]
    y_ecdf = np.arange(1, len(a_sorted) + 1) / len(a_sorted)
    for lab in LABEL_ORDER:
        m = labels == lab
        if m.sum() == 0:
            continue
        ax_a.plot(np.sort(a[m]), np.arange(1, m.sum() + 1) / m.sum(),
                  drawstyle="steps-post", linewidth=2.0, color=LABEL_COLOR[lab],
                  label=f"{lab} (n={m.sum():,})")
    # 阈值竖线
    for thr, name in [(STRONG_CUTOFF_NM, "strong ≤ 50 nM"),
                     (WEAK_CUTOFF_NM,   "weak   ≤ 500 nM")]:
        ax_a.axvline(thr, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.55)
        ax_a.text(thr, 0.02, name, rotation=90, va="bottom", ha="right",
                  fontsize=8.5, color="#DC2626",
                  transform=ax_a.get_xaxis_transform())

    ax_a.set_xscale("log")
    ax_a.set_xlabel("best affinity_nM (min across alleles, log scale)")
    ax_a.set_ylabel("ECDF (cumulative fraction)")
    ax_a.set_xlim(5, 1e5)
    ax_a.set_title("ECDF: best-case affinity per peptide", fontsize=12)
    ax_a.legend(loc="lower right", frameon=True, fontsize=9)
    ax_a.grid(True, alpha=0.25, linewidth=0.5)
    ax_a.set_axisbelow(True)

    fig.suptitle("multi-allele aggregation: best (max score / min affinity)",
                 fontsize=13, y=1.02)
    fig.tight_layout()
    out_png = out_dir / "mhcflurry_multi_best_distribution.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    stats = {
        "best_score_mean": float(s.mean()),
        "best_score_median": float(np.median(s)),
        "best_aff_nm_median": float(np.median(a)),
        "best_label_counts": {lab: int((labels == lab).sum()) for lab in LABEL_ORDER},
    }
    return stats


def fig_consensus_burden(per_pep, out_dir: Path) -> dict:
    """每个 peptide 在多少个 allele 上 ≤ 500 nM?分布 + 与单 allele baseline 对比。"""
    n_bound  = per_pep["n_bound"]
    n_strong = per_pep["n_strong"]

    fig = plt.figure(figsize=(12, 6))
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1.4, 1.0], wspace=0.28)
    ax_h = fig.add_subplot(gs[0, 0])
    ax_s = fig.add_subplot(gs[0, 1])

    # 左:n_bound 分布(直方图, x = bound allele 数)
    n_alleles = int(n_bound.max())
    bins = np.arange(-0.5, n_alleles + 1.5, 1)
    counts, _ = np.histogram(n_bound, bins=bins)

    cmap = plt.cm.YlOrRd
    norm = plt.matplotlib.colors.Normalize(vmin=0, vmax=n_alleles)
    colors = [cmap(norm(i)) for i in range(n_alleles + 1)]
    ax_h.bar(np.arange(n_alleles + 1), counts, color=colors,
             edgecolor="white", linewidth=0.4)
    for i, c in enumerate(counts):
        if c > 0:
            ax_h.text(i, c, f"{c:,}", ha="center", va="bottom", fontsize=7)
    ax_h.set_xlabel("# alleles where affinity ≤ 500 nM (binder)")
    ax_h.set_ylabel("# peptides")
    ax_h.set_yscale("log")
    ax_h.set_xticks(np.arange(0, n_alleles + 1, max(1, n_alleles // 10)))
    ax_h.set_title("burden: how many alleles does each peptide bind?",
                   fontsize=12)
    ax_h.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax_h.set_axisbelow(True)

    # 右:n_bound vs n_strong 的散点
    rng = np.random.default_rng(0)
    # jitter x for visual
    jit = rng.uniform(-0.3, 0.3, size=len(n_bound))
    ax_s.scatter(n_bound + jit, n_strong + jit,
                 s=3, alpha=0.25, color=LABEL_COLOR["Strong Binder"],
                 edgecolors="none")
    ax_s.plot([0, n_alleles], [0, n_alleles], "--", color="#9CA3AF", linewidth=1,
              alpha=0.5, label="y=x")
    ax_s.set_xlabel("# alleles ≤ 500 nM")
    ax_s.set_ylabel("# alleles ≤ 50 nM")
    ax_s.set_xlim(-0.5, n_alleles + 0.5)
    ax_s.set_ylim(-0.5, n_alleles + 0.5)
    ax_s.set_title("weak vs strong binding allele count per peptide",
                   fontsize=12)
    ax_s.grid(True, alpha=0.25, linewidth=0.5)
    ax_s.set_axisbelow(True)
    ax_s.legend(loc="upper left", frameon=True, fontsize=9)
    # 在右下角标 n
    ax_s.text(0.98, 0.02, f"n={len(n_bound):,} peptides",
              transform=ax_s.transAxes, ha="right", va="bottom",
              fontsize=8, family="monospace",
              bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                        edgecolor="none", alpha=0.85))

    fig.tight_layout()
    out_png = out_dir / "mhcflurry_multi_consensus_burden.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    stats = {
        "n_bound_distribution": {int(i): int(c) for i, c in enumerate(counts)},
        "n_strong_at_least_1": int((n_strong >= 1).sum()),
        "consensus_3_or_more": int((n_bound >= 3).sum()),
        "consensus_5_or_more": int((n_bound >= 5).sum()),
        "max_bound_alleles_observed": int(n_alleles),
        "panel_size": 23,
        "n_peptides_total": int(len(n_bound)),
    }
    return stats


def fig_length_heatmap(per_pep, out_dir: Path) -> dict:
    """best 视角下:peptide 长度 × best label 的计数热图。"""
    lens = sorted(set(int(x) for x in per_pep["pep_len"]))
    table = {l: {lab: 0 for lab in LABEL_ORDER} for l in lens}
    for l, lab in zip(per_pep["pep_len"], per_pep["best_label"]):
        table[int(l)][lab] += 1

    # 画成"长度 × label 的 100% stacked" + heatmap (binder rate)
    fig, (ax, ax2) = plt.subplots(
        1, 2, figsize=(13, 6),
        gridspec_kw={"width_ratios": [1.0, 1.2], "wspace": 0.30}
    )

    # 左:100% stacked 条
    pct_arr = np.array([
        [100.0 * table[l][lab] / max(1, sum(table[l].values())) for lab in LABEL_ORDER]
        for l in lens
    ])
    bottom = np.zeros(len(lens))
    for i, lab in enumerate(LABEL_ORDER):
        ax.bar(np.arange(len(lens)), pct_arr[:, i], bottom=bottom,
               color=LABEL_COLOR[lab], edgecolor="white", linewidth=0.4,
               label=lab)
        # 段中央标百分比(只在 >=5% 时)
        for j, v in enumerate(pct_arr[:, i]):
            if v >= 5.0:
                ax.text(j, bottom[j] + v / 2, f"{v:.1f}%",
                        ha="center", va="center", fontsize=8, color="#1F2937")
        bottom += pct_arr[:, i]

    ax.set_xticks(np.arange(len(lens)))
    ax.set_xticklabels([str(l) for l in lens])
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("fraction (%)")
    ax.set_ylim(0, 100)
    ax.set_title("best-case label composition by peptide length",
                 fontsize=11)
    ax.legend(loc="lower right", frameon=True, fontsize=9)
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)

    # 右:每个长度的 strong binder 计数(用 symlog 看小数量级)
    strong_per_len = [table[l]["Strong Binder"] for l in lens]
    weak_per_len = [table[l]["Weak Binder"] for l in lens]

    ax2.bar(np.arange(len(lens)) - 0.2, strong_per_len, width=0.4,
            color=LABEL_COLOR["Strong Binder"], label="Strong Binder")
    ax2.bar(np.arange(len(lens)) + 0.2, weak_per_len, width=0.4,
            color=LABEL_COLOR["Weak Binder"], label="Weak Binder")
    for i, (sc, wc) in enumerate(zip(strong_per_len, weak_per_len)):
        if sc > 0:
            ax2.text(i - 0.2, sc, f"{sc}", ha="center", va="bottom", fontsize=8)
        if wc > 0:
            ax2.text(i + 0.2, wc, f"{wc}", ha="center", va="bottom", fontsize=8)
    ax2.set_xticks(np.arange(len(lens)))
    ax2.set_xticklabels([str(l) for l in lens])
    ax2.set_xlabel("peptide length (aa)")
    ax2.set_ylabel("# binders (best-case)")
    ax2.set_yscale("symlog", linthresh=10)
    ax2.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax2.set_axisbelow(True)
    ax2.legend(loc="upper right", frameon=True, fontsize=9)
    ax2.set_title("best-case binder counts per length", fontsize=11)

    fig.suptitle("multi-allele aggregation: by peptide length (best-case)",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    out_png = out_dir / "mhcflurry_multi_length_heatmap.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    return {"per_length": {str(l): table[l] for l in lens}}


def fig_overview(fig_files: list[Path], out_path: Path, n_alleles: int, n_peptides: int) -> None:
    """2x2 dashboard."""
    from PIL import Image, ImageDraw, ImageFont
    imgs = [Image.open(p) for p in fig_files]
    w = min(im.width for im in imgs)
    h = min(im.height for im in imgs)
    imgs = [im.resize((w, h), Image.LANCZOS) for im in imgs]

    pad = 18
    title_h = 70
    canvas_w = w * 2 + pad * 3
    canvas_h = h * 2 + pad * 3 + title_h
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")

    for i, im in enumerate(imgs[:4]):
        row, col = divmod(i, 2)
        x = pad + col * (w + pad)
        y = title_h + pad + row * (h + pad)
        canvas.paste(im, (x, y))

    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    draw.text((pad, 14),
              f"mhcflurry multi-allele — {n_alleles} HLA alleles, n={n_peptides:,} peptides",
              fill="#111827", font=font)
    draw.text((pad, 44),
              "top-left: per-allele binder %    |    top-right: best score/affinity ECDF    "
              "|    bottom-left: binder allele burden    |    bottom-right: by-length best case",
              fill="#374151", font=font_small)

    canvas.save(out_path, dpi=(160, 160))


# ---------------------------------------------------------------- summary

def write_summary(out_dir: Path, stats: dict, n_alleles: int, n_peptides: int) -> None:
    csv_path = out_dir / "mhcflurry_multi_summary.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["# mhcflurry multi-allele aggregation"])
        w.writerow(["generated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")])
        w.writerow(["n_alleles_in_panel", n_alleles])
        w.writerow(["n_peptides", n_peptides])
        w.writerow([])

        # best label counts
        bc = stats["best"]["best_label_counts"]
        w.writerow(["# best-case (max score / min affinity across alleles)"])
        for lab in LABEL_ORDER:
            w.writerow([f"  {lab}", bc.get(lab, 0),
                        f"{100*bc.get(lab,0)/n_peptides:.4f}%"])
        w.writerow([])

        # consensus
        cn = stats["consensus"]
        w.writerow(["# consensus (peptides binding across N alleles)"])
        for k, v in cn.items():
            w.writerow([f"  {k}", v])
        w.writerow([])

        # per-allele
        w.writerow(["# per-allele binder counts"])
        w.writerow(["tool", "strong", "weak", "nonbinder", "total",
                    "pct_strong", "pct_weak"])
        for tool, r in sorted(stats["allele_yields"]["per_allele"].items()):
            ns, nw, nb, tot, ps, pw = r
            w.writerow([tool, ns, nw, nb, tot, f"{ps:.4f}", f"{pw:.4f}"])
        w.writerow([])

        # baseline
        bl = stats["allele_yields"]["baseline_a02"]
        w.writerow(["# baseline (A*02:01 single-allele, historical)"])
        w.writerow(["strong", bl["strong"], f"{100*bl['strong']/bl['total']:.4f}%"])
        w.writerow(["weak",   bl["weak"],   f"{100*bl['weak']/bl['total']:.4f}%"])
        w.writerow(["total",  bl["total"]])
    log.info("wrote %s", csv_path)

    json_path = out_dir / "mhcflurry_multi_summary.json"
    with json_path.open("w") as f:
        json.dump(stats, f, indent=2, default=str)
    log.info("wrote %s", json_path)


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots/mhcflurry_multi")
    p.add_argument("--dsn", default=DB_DSN)
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("connecting %s", args.dsn.split("@")[-1])
    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    multi = fetch_multi_allele(cur)
    base  = fetch_baseline(cur)
    cur.close()
    conn.close()

    per_pep = aggregate_per_peptide(multi)
    n_peptides = len(per_pep["peptide_id"])
    n_alleles = len(set(multi["tool"]))

    log.info("plotting fig1: per-allele yields ...")
    s_yields = fig_allele_yields(multi, base, out_dir)
    log.info("plotting fig2: best distribution ...")
    s_best = fig_best_distribution(per_pep, out_dir)
    log.info("plotting fig3: consensus burden ...")
    s_cons = fig_consensus_burden(per_pep, out_dir)
    log.info("plotting fig4: length heatmap ...")
    s_len = fig_length_heatmap(per_pep, out_dir)

    log.info("plotting fig5: dashboard overview ...")
    fig_overview([
        out_dir / "mhcflurry_multi_allele_yields.png",
        out_dir / "mhcflurry_multi_best_distribution.png",
        out_dir / "mhcflurry_multi_consensus_burden.png",
        out_dir / "mhcflurry_multi_length_heatmap.png",
    ], out_dir / "mhcflurry_multi_overview.png", n_alleles, n_peptides)

    stats = {
        "best": s_best,
        "consensus": s_cons,
        "allele_yields": s_yields,
        "length": s_len,
    }
    write_summary(out_dir, stats, n_alleles, n_peptides)
    log.info("done.")


if __name__ == "__main__":
    sys.exit(main())