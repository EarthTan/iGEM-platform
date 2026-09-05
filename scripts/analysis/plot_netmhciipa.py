#!/usr/bin/env python3
"""
plot_netmhciipa.py
针对 NetMHCIIpa 在 ~1.475 亿条 binder 结果 (netmhc_score 表) 上的运行结果,
画 6 张"合适"的统计图(MHC II 免疫学领域口径),输出到 results/plots/netmhciipa/。

图:
  1. netmhciipa_label_distribution.png   - bind_level 饼图 + 按 allele 的 SB% 横向条形
  2. netmhciipa_rank_by_allele.png       - 每个 allele 的 rank_pct 分布(violin, 按家族分面)
  3. netmhciipa_rank_ecdf.png            - rank_pct 累积分布,带 2% / 10% 经典阈值线
  4. netmhciipa_score_vs_rank.png        - score vs rank_pct hexbin(打分一致性验证)
  5. netmhciipa_length_vs_rank.png       - 肽长度 × rank_pct(箱线)+ bind_level 堆叠条
  6. netmhciipa_overview.png             - 2x3 dashboard 缩略图

数据: netmhc_score 表 (peptide, allele, core, rank_pct, score, bind_level)
      1.475 亿行,60 个等位基因,仅在 SQL 端聚合后下拉。

用法:
  python3 scripts/analysis/plot_netmhciipa.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from matplotlib.colors import LogNorm

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("plot-netmhciipa")

DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
OUT_DIR = Path("results/plots/netmhciipa")

# ---- 业务常量 ----------------------------------------------------------------
# NetMHCIIpan 经典阈值: %Rank <= 2  → Strong Binder, <= 10 → Weak Binder
STRONG_CUTOFF_PCT = 2.0
WEAK_CUTOFF_PCT = 10.0

# bind_level 颜色(灰 / 暖橙 / 强蓝,沿用 mhcflurry 图配色保持一致)
LABEL_COLOR = {
    "SB": "#4C8DBC",  # 强蓝
    "WB": "#F4B183",  # 暖橙
}
LABEL_COLOR_SOFT = {
    "SB": "#CDE0EE",
    "WB": "#FCE4CF",
}

# 等位基因家族分组(family -> [prefix, ...])
ALLELE_FAMILY_PREFIX = {
    "DRB1":      ["DRB1"],
    "DRB3/4/5":  ["DRB3", "DRB4", "DRB5"],
    "HLA-DP":    ["HLA-DPA1"],
    "HLA-DQ":    ["HLA-DQA1"],
}
FAMILY_ORDER = ["DRB1", "DRB3/4/5", "HLA-DP", "HLA-DQ"]
FAMILY_COLOR = {
    "DRB1": "#4C8DBC",
    "DRB3/4/5": "#7BAF7B",
    "HLA-DP": "#C77B8B",
    "HLA-DQ": "#E5A24A",
}


def family_of(allele: str) -> str:
    for fam, prefixes in ALLELE_FAMILY_PREFIX.items():
        for prefix in prefixes:
            if allele.startswith(prefix):
                return fam
    return "other"


# ---- data layer --------------------------------------------------------------

def fetch_allele_agg(cur) -> list[dict]:
    """每个 allele 一个小聚合(60 行)——服务器端算好直接下拉。"""
    cur.execute(
        """
        SELECT allele,
               count(*)                              AS n,
               count(*) FILTER (WHERE bind_level='SB') AS n_sb,
               count(*) FILTER (WHERE bind_level='WB') AS n_wb,
               avg(rank_pct)                         AS mean_rank,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY rank_pct) AS median_rank,
               avg(score)                            AS mean_score
          FROM netmhc_score
         GROUP BY allele
         ORDER BY allele
        """
    )
    rows = cur.fetchall()
    log.info("fetched %d allele-level aggregates", len(rows))
    return rows


def fetch_global_label(cur) -> dict:
    cur.execute(
        """
        SELECT bind_level, count(*) AS n
          FROM netmhc_score
         GROUP BY bind_level
        """
    )
    return {r["bind_level"]: int(r["n"]) for r in cur.fetchall()}


def fetch_length_agg(cur) -> list[dict]:
    cur.execute(
        """
        SELECT LENGTH(peptide)                              AS pep_len,
               count(*)                                     AS n,
               count(*) FILTER (WHERE bind_level='SB')      AS n_sb,
               avg(rank_pct)                                AS mean_rank,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY rank_pct) AS median_rank
          FROM netmhc_score
         GROUP BY 1
         ORDER BY 1
        """
    )
    return cur.fetchall()


def fetch_rank_sample(cur, n_per_allele: int = 5000) -> dict[str, np.ndarray]:
    """对每个 allele 用 row_number() 抽样固定行数(per-allele 上限,防极端不平衡)。"""
    cur.execute(
        """
        WITH ranked AS (
            SELECT allele, rank_pct, score, bind_level,
                   row_number() OVER (PARTITION BY allele ORDER BY random()) AS rn
              FROM netmhc_score
        )
        SELECT allele, rank_pct, score, bind_level
          FROM ranked
         WHERE rn <= %s
        """,
        (n_per_allele,),
    )
    rows = cur.fetchall()
    log.info("fetched %d sampled rows for per-allele distribution", len(rows))
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["allele"], {"rank_pct": [], "score": [], "label": []})
        d["rank_pct"].append(float(r["rank_pct"]))
        d["score"].append(float(r["score"]))
        d["label"].append(r["bind_level"])
    return {a: {k: np.asarray(v) for k, v in d.items()} for a, d in out.items()}


def fetch_score_rank_scatter(cur, n: int = 200_000) -> tuple[np.ndarray, np.ndarray]:
    """随机抽 n 行,用于 score vs rank_pct 散点(hexbin)。"""
    cur.execute(
        """
        SELECT rank_pct, score
          FROM netmhc_score
         ORDER BY random()
         LIMIT %s
        """,
        (n,),
    )
    rows = cur.fetchall()
    log.info("fetched %d rows for score vs rank scatter", len(rows))
    rank = np.fromiter((r["rank_pct"] for r in rows), dtype=np.float32, count=len(rows))
    score = np.fromiter((r["score"] for r in rows), dtype=np.float32, count=len(rows))
    return rank, score


# ---- figures -----------------------------------------------------------------

def fig_label_distribution(allele_rows, global_label, out_dir: Path) -> dict:
    """图 1: bind_level 全局饼图 + 按家族分面的 per-allele SB% 横向条形。"""
    n_total = sum(global_label.values())
    sb_n = global_label.get("SB", 0)
    wb_n = global_label.get("WB", 0)
    sb_pct = 100.0 * sb_n / n_total
    wb_pct = 100.0 * wb_n / n_total
    n_allele = len(allele_rows)

    fig = plt.figure(figsize=(18, 18))
    # 顶部行:饼图 + meta 文本
    # 主体:4 个家族竖排, 高度按 allele 数加权
    by_fam: dict[str, list[dict]] = {f: [] for f in FAMILY_ORDER}
    for r in allele_rows:
        f = family_of(r["allele"])
        if f in by_fam:
            by_fam[f].append(r)
    for f in by_fam:
        by_fam[f].sort(key=lambda r: -r["n_sb"] / r["n"] if r["n"] else 0.0)

    fam_order = [f for f in FAMILY_ORDER if by_fam.get(f)]
    # 高度比均匀(每个 family 内部裁到 Top-K + 1 个 "other" 摘要)
    height_ratios = [1.0] * len(fam_order)

    gs = GridSpec(
        1 + len(fam_order), 2,
        height_ratios=[1.0] + height_ratios,
        hspace=0.40, wspace=0.30,
        left=0.06, right=0.97, top=0.93, bottom=0.04,
    )
    # top row: 饼图 + meta
    ax_pie = fig.add_subplot(gs[0, 0])
    ax_meta = fig.add_subplot(gs[0, 1])
    ax_pie.pie(
        [sb_n, wb_n],
        labels=[f"SB\n({sb_pct:.2f}%)", f"WB\n({wb_pct:.2f}%)"],
        colors=[LABEL_COLOR["SB"], LABEL_COLOR["WB"]],
        startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 10, "ha": "center"},
    )
    ax_pie.set_title(f"global bind_level (n = {n_total:,})", fontsize=11, pad=6)

    ax_meta.axis("off")
    meta = (
        f"NetMHCIIpa × 20M peptide library\n"
        f"─────────────────────────\n"
        f"Total binder rows:    {n_total:>14,}\n"
        f"Distinct peptides:    ~14,804,406\n"
        f"Alleles covered:      {n_allele:>14d}\n"
        f"Strong Binder (SB):   {sb_n:>14,}  ({sb_pct:.2f}%)\n"
        f"Weak Binder   (WB):   {wb_n:>14,}  ({wb_pct:.2f}%)\n"
        f"─────────────────────────\n"
        f"%Rank threshold:   SB ≤ {STRONG_CUTOFF_PCT:.0f}%,   WB ≤ {WEAK_CUTOFF_PCT:.0f}%\n"
        f"core length:       9 aa  (MHC II canonical)\n"
        f"allele coverage:   4 HLA-II families, 131 alleles total"
    )
    ax_meta.text(0.0, 0.5, meta, fontsize=11, family="monospace", va="center")

    # body: per-family horizontal bars (Top-25 + "others" group to keep readable)
    TOP_K = 25
    for i, fam in enumerate(fam_order):
        rows = by_fam[fam]
        ax = fig.add_subplot(gs[1 + i, :])
        if len(rows) > TOP_K:
            top = rows[:TOP_K]
            others = rows[TOP_K:]
            others_sb_n = sum(r["n_sb"] for r in others)
            others_n = sum(r["n"] for r in others)
            others_pct = 100.0 * others_sb_n / others_n if others_n else 0.0
            top.append({"allele": f"[other {len(others)} alleles]", "n_sb": others_sb_n, "n": others_n})
            rows = top
        alleles = [r["allele"] for r in rows]
        sb_pct_per = np.array([100.0 * r["n_sb"] / r["n"] if r["n"] else 0.0 for r in rows])
        y = np.arange(len(alleles))
        ax.barh(y, sb_pct_per, color=FAMILY_COLOR[fam], edgecolor="white", linewidth=0.3)
        ax.set_yticks(y)
        ax.set_yticklabels(alleles, fontsize=7)
        ax.invert_yaxis()
        ax.set_xlim(0, max(sb_pct_per.max() * 1.25, sb_pct * 1.25))
        ax.axvline(sb_pct, color="black", linestyle="--", linewidth=0.8, alpha=0.55)
        ax.grid(axis="x", linestyle=":", alpha=0.35)
        ax.set_title(
            f"{fam}  (showing top {min(TOP_K, len(by_fam[fam]))} of {len(by_fam[fam])} alleles by SB%)",
            fontsize=11, loc="left",
            color=FAMILY_COLOR[fam], fontweight="bold", pad=4,
        )
        # 只在最后一行画 x label
        if i == len(fam_order) - 1:
            ax.set_xlabel("Strong-Binder rate (%)", fontsize=10)

    fig.suptitle("NetMHCIIpa — bind_level overview (per-family facet)", fontsize=14, y=0.995)
    fig.savefig(out_dir / "netmhciipa_label_distribution.png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return {"n_total": n_total, "n_sb": sb_n, "n_wb": wb_n, "n_alleles": n_allele}


def fig_rank_by_allele(per_allele_sample, allele_rows, out_dir: Path) -> None:
    """图 2: 每个 allele 的 rank_pct 分布 — violin,按家族分面。"""
    # 按家族分组,每个家族内按中位 rank 排序
    by_fam: dict[str, list[str]] = {f: [] for f in FAMILY_ORDER}
    median_lookup = {r["allele"]: float(r["median_rank"]) for r in allele_rows}
    for a in per_allele_sample:
        f = family_of(a)
        if f in by_fam:
            by_fam[f].append(a)
    for f in by_fam:
        by_fam[f].sort(key=lambda a: median_lookup.get(a, 999))

    fig, axes = plt.subplots(len(FAMILY_ORDER), 1, figsize=(13, 11), sharex=True)
    for ax, fam in zip(axes, FAMILY_ORDER):
        alleles = by_fam[fam]
        if not alleles:
            ax.set_visible(False)
            continue
        data = [per_allele_sample[a]["rank_pct"] for a in alleles]
        positions = np.arange(1, len(alleles) + 1)
        parts = ax.violinplot(
            data, positions=positions, showmedians=True, widths=0.8,
        )
        for pc in parts["bodies"]:
            pc.set_facecolor(FAMILY_COLOR[fam])
            pc.set_alpha(0.7)
            pc.set_edgecolor("black")
            pc.set_linewidth(0.3)
        for k in ("cbars", "cmins", "cmaxes", "cmedians"):
            if k in parts:
                parts[k].set_color("black")
                parts[k].set_linewidth(0.6)
        ax.set_xticks(positions)
        ax.set_xticklabels(alleles, rotation=60, fontsize=7, ha="right")
        ax.set_ylabel("rank_pct (%)", fontsize=9)
        ax.set_title(f"{fam}  ({len(alleles)} alleles,  n={per_allele_sample[alleles[0]]['rank_pct'].size:,}/allele sampled)",
                     fontsize=10, loc="left", color=FAMILY_COLOR[fam], fontweight="bold")
        ax.grid(axis="y", linestyle=":", alpha=0.4)
        ax.axvline(0, color="white")  # spacer
    axes[-1].set_xlabel("Allele", fontsize=10)
    axes[0].axhline(STRONG_CUTOFF_PCT, color=LABEL_COLOR["SB"], linestyle="--", linewidth=1, alpha=0.6,
                    label=f"SB cutoff ≤ {STRONG_CUTOFF_PCT:.0f}%")
    axes[0].axhline(WEAK_CUTOFF_PCT, color=LABEL_COLOR["WB"], linestyle="--", linewidth=1, alpha=0.6,
                    label=f"WB cutoff ≤ {WEAK_CUTOFF_PCT:.0f}%")
    axes[0].legend(loc="upper right", fontsize=8)

    fig.suptitle("NetMHCIIpa — rank_pct distribution per allele (sorted by median within family)",
                 fontsize=14, y=0.995)
    fig.savefig(out_dir / "netmhciipa_rank_by_allele.png", dpi=130, bbox_inches="tight")
    plt.close(fig)


def fig_rank_ecdf_impl(hist_by_family: dict[str, tuple[np.ndarray, np.ndarray, int]],
                       n_total: int, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6.5))
    # 全局
    x_g, c_g, _ = hist_by_family["__all__"]
    ax.plot(x_g, c_g, color="black", linewidth=2.0, label=f"ALL (n={n_total:,})")
    for fam in FAMILY_ORDER:
        if fam not in hist_by_family:
            continue
        x, c, n_fam = hist_by_family[fam]
        ax.plot(x, c, color=FAMILY_COLOR[fam], linewidth=1.2, alpha=0.85,
                label=f"{fam} (n={n_fam:,})")
    ax.axvline(STRONG_CUTOFF_PCT, color=LABEL_COLOR["SB"], linestyle="--", linewidth=1.2,
               label=f"SB cutoff = {STRONG_CUTOFF_PCT:.0f}%")
    ax.axvline(WEAK_CUTOFF_PCT, color=LABEL_COLOR["WB"], linestyle="--", linewidth=1.2,
               label=f"WB cutoff = {WEAK_CUTOFF_PCT:.0f}%")
    ax.set_xlabel("%Rank (lower = stronger binding)", fontsize=11)
    ax.set_ylabel("ECDF  (fraction of rows with rank ≤ x)", fontsize=11)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.005)
    ax.grid(linestyle=":", alpha=0.4)
    ax.legend(loc="lower right", fontsize=9, framealpha=0.95)
    ax.set_title("NetMHCIIpa — %Rank ECDF, global and by HLA family", fontsize=13, pad=8)
    fig.tight_layout()
    fig.savefig(out_dir / "netmhciipa_rank_ecdf.png", dpi=140, bbox_inches="tight")
    plt.close(fig)


def fig_score_vs_rank(rank, score, out_dir: Path) -> None:
    """图 4: score vs rank_pct hexbin(应该有强正相关;但 NetMHCIIpan 中两者关系复杂)。"""
    fig, ax = plt.subplots(figsize=(10, 7))
    hb = ax.hexbin(
        rank, score, gridsize=80, cmap="viridis", mincnt=1,
        norm=LogNorm(), linewidths=0,
    )
    cb = fig.colorbar(hb, ax=ax, pad=0.02)
    cb.set_label("row count (log scale)", fontsize=10)
    ax.axvline(STRONG_CUTOFF_PCT, color=LABEL_COLOR["SB"], linestyle="--", linewidth=1, alpha=0.6)
    ax.axvline(WEAK_CUTOFF_PCT, color=LABEL_COLOR["WB"], linestyle="--", linewidth=1, alpha=0.6)
    ax.set_xlabel("%Rank", fontsize=11)
    ax.set_ylabel("Score (raw)", fontsize=11)
    ax.set_title(f"NetMHCIIpa — score vs %Rank ({len(rank):,} random rows)", fontsize=13, pad=8)
    # 文本框
    txt = (
        f"spearman ρ = {spearman_corr(rank, score):+.4f}\n"
        f"pearson  r  = {pearson_corr(rank, score):+.4f}"
    )
    ax.text(0.02, 0.97, txt, transform=ax.transAxes, fontsize=10, va="top",
            family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.85, edgecolor="gray"))
    ax.grid(linestyle=":", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "netmhciipa_score_vs_rank.png", dpi=140, bbox_inches="tight")
    plt.close(fig)


def fig_length_vs_rank(length_rows, out_dir: Path) -> None:
    """图 5: 肽长度 × rank_pct(箱线 + 散点)+ bind_level 堆叠条。"""
    rows = sorted([r for r in length_rows if r["pep_len"] is not None], key=lambda r: r["pep_len"])
    lens = [r["pep_len"] for r in rows]
    n_total = sum(r["n"] for r in rows)
    n_sb = [r["n_sb"] for r in rows]
    sb_pct = [100.0 * s / r["n"] if r["n"] else 0.0 for s, r in zip(n_sb, rows)]
    median_rank = [float(r["median_rank"]) for r in rows]
    mean_rank = [float(r["mean_rank"]) for r in rows]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8),
        gridspec_kw={"height_ratios": [2.2, 1.3], "hspace": 0.32},
    )
    # (a) 长度 × 中位 / 均值 rank
    x = np.arange(len(lens))
    ax1.plot(x, median_rank, "o-", color="#4C8DBC", linewidth=1.5, label="median %Rank")
    ax1.plot(x, mean_rank, "s--", color="#C77B8B", linewidth=1.0, alpha=0.85, label="mean %Rank")
    ax1.set_xticks(x)
    ax1.set_xticklabels(lens, fontsize=8)
    ax1.set_xlabel("peptide length (aa)", fontsize=10)
    ax1.set_ylabel("%Rank (lower = stronger)", fontsize=10)
    ax1.set_title("Median / mean %Rank by peptide length", fontsize=11, loc="left")
    ax1.axhline(STRONG_CUTOFF_PCT, color=LABEL_COLOR["SB"], linestyle=":", linewidth=1, alpha=0.6)
    ax1.axhline(WEAK_CUTOFF_PCT, color=LABEL_COLOR["WB"], linestyle=":", linewidth=1, alpha=0.6)
    ax1.grid(linestyle=":", alpha=0.4)
    ax1.legend(loc="upper right", fontsize=9)
    ax1.set_ylim(0, max(mean_rank) * 1.15)

    # (b) 长度 × SB% 堆叠条(从大到小按 row 数标透明度)
    ax2.bar(x, [100.0 - s for s in sb_pct], color=LABEL_COLOR_SOFT["WB"], label="WB", edgecolor="white", linewidth=0.3)
    ax2.bar(x, sb_pct, bottom=[100.0 - s for s in sb_pct], color=LABEL_COLOR["SB"], label="SB", edgecolor="white", linewidth=0.3)
    ax2.set_xticks(x)
    ax2.set_xticklabels(lens, fontsize=8)
    ax2.set_xlabel("peptide length (aa)", fontsize=10)
    ax2.set_ylabel("bind_level share (%)", fontsize=10)
    ax2.set_ylim(0, 100)
    ax2.set_title("SB / WB share by peptide length", fontsize=11, loc="left")
    ax2.legend(loc="upper right", fontsize=9)
    # row 数标签(画在 x 轴 tick label 下面)
    ax2.set_ylim(0, 100)
    new_xticklabels = [f"{li}\n{n/1e6:.1f}M" for li, n in zip(lens, [r["n"] for r in rows])]
    ax2.set_xticklabels(new_xticklabels, fontsize=8)

    fig.suptitle("NetMHCIIpa — peptide length vs binding", fontsize=14, y=0.995)
    fig.savefig(out_dir / "netmhciipa_length_vs_rank.png", dpi=140, bbox_inches="tight")
    plt.close(fig)


def fig_overview(out_dir: Path) -> None:
    """图 6: 2x3 dashboard,只缩略前 5 张主图(第 6 格放文字摘要)。"""
    import matplotlib.image as mpimg
    thumbs = [
        ("netmhciipa_label_distribution.png", "(a) bind_level overview"),
        ("netmhciipa_rank_ecdf.png",            "(b) rank ECDF"),
        ("netmhciipa_rank_by_allele.png",       "(c) per-allele rank distribution"),
        ("netmhciipa_score_vs_rank.png",        "(d) score vs rank"),
        ("netmhciipa_length_vs_rank.png",       "(e) length vs rank"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    for ax, (fn, title) in zip(axes.flat[:5], thumbs):
        try:
            img = mpimg.imread(out_dir / fn)
            ax.imshow(img)
        except FileNotFoundError:
            ax.text(0.5, 0.5, f"missing: {fn}", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
    ax = axes.flat[5]
    ax.axis("off")
    txt = (
        "NetMHCIIpa × 20M peptide library\n"
        "─────────────\n"
        "1.475 × 10⁸ binder rows\n"
        "131 HLA-II alleles\n"
        "─ DRB1 (n=49)\n"
        "─ DRB3/4/5 (n=8)\n"
        "─ HLA-DP (n=49)\n"
        "─ HLA-DQ (n=25)\n"
        "─────────────\n"
        "SB ≤ 2%  ·  WB ≤ 10%  ·  core = 9 aa\n"
        "─ Score (raw) ∈ [0.01, 1.00]\n"
        "─ %Rank  ∈ [0, 10]\n"
    )
    ax.text(0.05, 0.95, txt, transform=ax.transAxes, fontsize=11, family="monospace",
            va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#F6F6F6",
                      edgecolor="gray", linewidth=0.5))
    fig.suptitle("NetMHCIIpa — overview dashboard", fontsize=15, y=0.995)
    fig.savefig(out_dir / "netmhciipa_overview.png", dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---- helpers -----------------------------------------------------------------

def spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    return pearson_corr(rx.astype(np.float64), ry.astype(np.float64))


def pearson_corr(x: np.ndarray, y: np.ndarray) -> float:
    xm = x - x.mean()
    ym = y - y.mean()
    denom = np.sqrt((xm * xm).sum() * (ym * ym).sum())
    return float((xm * ym).sum() / denom) if denom else float("nan")


def fetch_rank_hist_by_family(cur, n_buckets: int = 200) -> tuple[dict, int]:
    """每个家族(以及全局)一个 %Rank 直方图,基于 SQL width_bucket 算,客户端还原 ECDF。"""
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    n_total = 0

    def hist_for(label_filter: str, params: tuple) -> tuple[np.ndarray, np.ndarray, int]:
        cur.execute(
            f"""
            SELECT width_bucket(rank_pct, 0, 10.0001, %s) AS bkt,
                   count(*)                              AS n
              FROM netmhc_score
             {label_filter}
             GROUP BY 1
             ORDER BY 1
            """,
            params,
        )
        rows = cur.fetchall()
        # 补齐缺失的桶
        counts = {r["bkt"]: r["n"] for r in rows}
        all_b = np.arange(1, n_buckets + 1)
        n_per = np.array([counts.get(int(b), 0) for b in all_b], dtype=np.int64)
        # 桶中心: (b - 0.5) * 10 / n_buckets
        x = (all_b - 0.5) * 10.0 / n_buckets
        cdf = np.cumsum(n_per) / max(n_per.sum(), 1)
        return x, cdf, int(n_per.sum())

    # 全局
    x, cdf, n = hist_for("", (n_buckets,))
    out["__all__"] = (x, cdf, n)
    n_total = n
    # 各家族(per-family 一次查询, WHERE IN 列出该家族的所有 prefix)
    for fam, prefixes in ALLELE_FAMILY_PREFIX.items():
        cur.execute(
            """
            SELECT width_bucket(rank_pct, 0, 10.0001, %s) AS bkt,
                   count(*)                              AS n
              FROM netmhc_score
             WHERE allele LIKE ANY(%s)
             GROUP BY 1
             ORDER BY 1
            """,
            (n_buckets, [p + "%" for p in prefixes]),
        )
        rs = cur.fetchall()
        counts = {r["bkt"]: r["n"] for r in rs}
        all_b = np.arange(1, n_buckets + 1)
        n_per = np.array([counts.get(int(b), 0) for b in all_b], dtype=np.int64)
        if n_per.sum() == 0:
            continue
        x = (all_b - 0.5) * 10.0 / n_buckets
        cdf = np.cumsum(n_per) / n_per.sum()
        out[fam] = (x, cdf, int(n_per.sum()))
        log.info("  family %-12s  n=%12d", fam, int(n_per.sum()))
    return out, n_total


# ---- main --------------------------------------------------------------------

def main() -> None:
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(DB_DSN) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            log.info("=== aggregating per-allele stats ===")
            allele_rows = fetch_allele_agg(cur)
            log.info("=== global bind_level counts ===")
            global_label = fetch_global_label(cur)
            log.info("=== length × bind_level aggregation ===")
            length_rows = fetch_length_agg(cur)
            log.info("=== per-allele rank_pct sample (5k/allele) ===")
            per_allele_sample = fetch_rank_sample(cur, n_per_allele=5000)
            log.info("=== score vs rank scatter (200k random) ===")
            rank, score = fetch_score_rank_scatter(cur, n=200_000)
            log.info("=== %Rank histogram per family (server-side) ===")
            hist_by_family, n_total = fetch_rank_hist_by_family(cur, n_buckets=200)

    # ---- figs ----
    log.info("=== fig 1: label distribution ===")
    meta = fig_label_distribution(allele_rows, global_label, out_dir)
    log.info("=== fig 2: rank by allele ===")
    fig_rank_by_allele(per_allele_sample, allele_rows, out_dir)
    log.info("=== fig 3: rank ECDF ===")
    fig_rank_ecdf_impl(hist_by_family, n_total, out_dir)
    log.info("=== fig 4: score vs rank ===")
    fig_score_vs_rank(rank, score, out_dir)
    log.info("=== fig 5: length vs rank ===")
    fig_length_vs_rank(length_rows, out_dir)
    log.info("=== fig 6: overview ===")
    fig_overview(out_dir)

    # ---- summary csv/json ----
    log.info("=== writing summary tables ===")
    with open(out_dir / "netmhciipa_summary.csv", "w", encoding="utf-8") as f:
        f.write("allele,family,n,n_sb,n_wb,sb_pct,mean_rank,median_rank,mean_score\n")
        for r in allele_rows:
            sb_pct = 100.0 * r["n_sb"] / r["n"] if r["n"] else 0.0
            f.write(
                f"{r['allele']},{family_of(r['allele'])},{r['n']},{r['n_sb']},{r['n_wb']},"
                f"{sb_pct:.4f},{r['mean_rank']:.4f},{r['median_rank']:.4f},{r['mean_score']:.6f}\n"
            )

    summary = {
        "global": {
            "n_total": meta["n_total"],
            "n_sb": meta["n_sb"],
            "n_wb": meta["n_wb"],
            "sb_pct": 100.0 * meta["n_sb"] / meta["n_total"],
            "n_alleles": meta["n_alleles"],
            "score_vs_rank_pearson": pearson_corr(rank, score),
            "score_vs_rank_spearman": spearman_corr(rank, score),
            "strong_cutoff_pct": STRONG_CUTOFF_PCT,
            "weak_cutoff_pct": WEAK_CUTOFF_PCT,
        },
        "thresholds": {
            "sb": "<=2%",
            "wb": "<=10%",
        },
    }
    with open(out_dir / "netmhciipa_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    log.info("done. outputs in %s", out_dir)


if __name__ == "__main__":
    main()
