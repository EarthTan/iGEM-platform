#!/usr/bin/env python3
"""
plot_mhcflurry.py
针对 mhcflurry 在 ~135k 条 5–15aa 肽上的打分结果,
画 5 张"合适"的统计图(MHC 免疫学领域口径),输出到 results/plots/mhcflurry/。

图:
  1. mhcflurry_label_distribution.png  - 长度 × label 堆叠条
  2. mhcflurry_score_vs_affinity.png  - score vs log10(affinity_nM) hexbin
  3. mhcflurry_score_by_length.png     - 按长度+label 的 score box/violin
  4. mhcflurry_affinity_ecdf.png      - log10(affinity_nM) 的 ECDF,带 50/500nM 阈值线
  5. mhcflurry_overview.png           - 2x2 dashboard 缩略图
"""

数据: peptide_enrichment 表, tool='mhcflurry', details->>affinity_nM/peptide_length

用法:
  /tmp/plot_venv/bin/python scripts/analysis/plot_mhcflurry.py
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
from matplotlib.colors import LogNorm
from matplotlib.patches import Patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("plot-mhcflurry")

DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"

# label 顺序(按业务意义从弱到强)
LABEL_ORDER = ["Non-Binder", "Weak Binder", "Strong Binder"]
LABEL_COLOR = {
    "Non-Binder":   "#9CA3AF",  # 灰
    "Weak Binder":  "#F4B183",  # 暖橙
    "Strong Binder":"#4C8DBC",  # 强蓝
}
LABEL_COLOR_SOFT = {
    "Non-Binder":   "#D1D5DB",
    "Weak Binder":  "#FCE4CF",
    "Strong Binder":"#CDE0EE",
}
# MHC 领域两个经典阈值(nM,越小越好)
WEAK_CUTOFF_NM   = 500.0
STRONG_CUTOFF_NM = 50.0


# ---------------------------------------------------------------- data layer

def fetch_mhcflurry(cur) -> dict[str, np.ndarray]:
    """一次拉全量 ~135k 行(全量直接拉,无需 sampling)。"""
    cur.execute(
        """
        SELECT score,
               label,
               (details->>'affinity_nM')::float    AS affinity_nm,
               (details->>'peptide_length')::int   AS pep_len
          FROM peptide_enrichment
         WHERE tool = 'mhcflurry'
        """
    )
    rows = cur.fetchall()
    log.info("fetched %d rows from peptide_enrichment WHERE tool='mhcflurry'", len(rows))

    n = len(rows)
    out = {
        "score":        np.fromiter((r["score"]        for r in rows), dtype=np.float32, count=n),
        "affinity_nm":  np.fromiter((r["affinity_nm"]  for r in rows), dtype=np.float32, count=n),
        "pep_len":      np.fromiter((r["pep_len"]      for r in rows), dtype=np.int16,   count=n),
        "label":        np.fromiter((r["label"]        for r in rows), dtype=object,     count=n),
    }
    return out


# ----------------------------------------------------------------- fig 1

def fig1_label_by_length(data, out_dir: Path) -> dict:
    """长度 × label 堆叠条(归一化百分比)+ 顶部绝对数标签。
    信息密度: 同时看占比 + 总数 + 每长度绝对分布。"""
    lens = sorted(set(int(x) for x in data["pep_len"]))
    # 统计
    table = {l: {lab: 0 for lab in LABEL_ORDER} for l in lens}
    for s, lab, ln in zip(data["score"], data["label"], data["pep_len"]):
        table[int(ln)][lab] += 1

    # 归一化百分比
    pct = {l: [] for l in lens}
    totals = {}
    for l in lens:
        tot = sum(table[l].values())
        totals[l] = tot
        denom = tot if tot > 0 else 1
        for lab in LABEL_ORDER:
            pct[l].append(100.0 * table[l][lab] / denom)

    pct_arr = np.array([pct[l] for l in lens])  # (n_len, 3)

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 6.5),
        gridspec_kw={"height_ratios": [3, 1.2], "hspace": 0.35},
    )

    # 顶部: 归一化堆叠条
    bottom = np.zeros(len(lens))
    x = np.arange(len(lens))
    for i, lab in enumerate(LABEL_ORDER):
        ax.bar(x, pct_arr[:, i], bottom=bottom,
               color=LABEL_COLOR[lab], edgecolor="white", linewidth=0.6,
               label=lab, width=0.78)
        # 段中央写百分比(只在 ≥5% 时显示)
        for j, v in enumerate(pct_arr[:, i]):
            if v >= 5.0:
                ax.text(x[j], bottom[j] + v / 2, f"{v:.1f}%",
                        ha="center", va="center", fontsize=8, color="#1F2937")
        bottom += pct_arr[:, i]

    ax.set_xticks(x)
    ax.set_xticklabels([str(l) for l in lens])
    ax.set_xlabel("peptide length (aa)")
    ax.set_ylabel("fraction (%)")
    ax.set_ylim(0, 100)
    ax.set_title("mhcflurry label composition by peptide length (HLA-A*02:01, n=134,888)",
                 fontsize=12)
    ax.legend(loc="lower right", frameon=True, fontsize=9)
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)

    # 底部: 每长度总数(粗略告诉用户每长度的样本规模)
    bar_colors = ["#4C8DBC" if (totals[l] / max(totals.values())) > 0.7 else "#94A3B8"
                  for l in lens]
    ax2.bar(x, [totals[l] for l in lens], color=bar_colors, width=0.78, edgecolor="white")
    for j, l in enumerate(lens):
        ax2.text(x[j], totals[l], f"{totals[l]:,}",
                 ha="center", va="bottom", fontsize=8, color="#1F2937")
    ax2.set_xticks(x)
    ax2.set_xticklabels([str(l) for l in lens])
    ax2.set_xlabel("peptide length (aa)")
    ax2.set_ylabel("count")
    ax2.set_yscale("log")
    ax2.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax2.set_axisbelow(True)
    ax2.set_title("sample size per length (log scale)", fontsize=10, color="#374151")

    fig.tight_layout()
    out_png = out_dir / "mhcflurry_label_distribution.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    # 摘要: binder 占比 + 每长度强 binder 数
    total_n = sum(totals.values())
    n_strong = sum(table[l]["Strong Binder"] for l in lens)
    n_weak   = sum(table[l]["Weak Binder"]   for l in lens)
    summary = {
        "total_n":      total_n,
        "n_strong":     n_strong,
        "n_weak":       n_weak,
        "n_nonbinder":  total_n - n_strong - n_weak,
        "pct_strong":   100 * n_strong / total_n,
        "pct_weak":     100 * n_weak   / total_n,
        "pct_nonbinder": 100 * (total_n - n_strong - n_weak) / total_n,
        "per_length":   {
            str(l): {
                "total":  totals[l],
                "strong": table[l]["Strong Binder"],
                "weak":   table[l]["Weak Binder"],
                "nonbinder": table[l]["Non-Binder"],
                "binder_rate_pct": 100 * (table[l]["Strong Binder"] + table[l]["Weak Binder"]) / totals[l]
                                   if totals[l] else 0.0,
            } for l in lens
        },
    }
    return summary


# ----------------------------------------------------------------- fig 2

def fig2_score_vs_affinity(data, out_dir: Path) -> dict:
    """score vs log10(affinity_nM) hexbin,自动排除 affinity=0 等极端。"""
    score   = data["score"]
    aff     = data["affinity_nm"]
    mask = (score >= 0) & (score <= 1) & np.isfinite(aff) & (aff > 0)
    s = score[mask]
    a = aff[mask]

    log_aff = np.log10(a)

    fig, ax = plt.subplots(figsize=(10, 6.5))
    hb = ax.hexbin(s, log_aff, gridsize=60, mincnt=1,
                   norm=LogNorm(), cmap="viridis", linewidths=0)
    cb = fig.colorbar(hb, ax=ax, pad=0.02)
    cb.set_label("count (log scale)")

    # 经典阈值横线
    for thr_nm, lab, c in [(STRONG_CUTOFF_NM, f"strong ≤ {STRONG_CUTOFF_NM:.0f} nM", "#DC2626"),
                           (WEAK_CUTOFF_NM,   f"weak   ≤ {WEAK_CUTOFF_NM:.0f} nM",   "#F59E0B")]:
        ax.axhline(math.log10(thr_nm), color=c, linestyle="--", linewidth=1.2, alpha=0.8)
        # 标签放在左内侧,避免被右侧色条挡住
        ax.text(0.005, math.log10(thr_nm), lab, transform=ax.get_yaxis_transform(),
                va="bottom", ha="left", fontsize=8.5, color=c,
                bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                          edgecolor="none", alpha=0.85))

    ax.set_xlabel("mhcflurry score  (0–1)")
    ax.set_ylabel(r"affinity  $\log_{10}$(nM)")
    ax.set_xlim(0, 1)
    ax.set_title("mhcflurry score vs predicted binding affinity (hexbin, n=134,888)",
                 fontsize=12)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)

    fig.tight_layout()
    out_png = out_dir / "mhcflurry_score_vs_affinity.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    # 摘要: 在每个 label 上 score↔affinity 的 spearman 不算(轻量),给分位数
    summary = {}
    for lab in LABEL_ORDER:
        m = (data["label"] == lab) & (data["affinity_nm"] > 0)
        if m.sum() == 0:
            continue
        a_lab = data["affinity_nm"][m]
        s_lab = data["score"][m]
        summary[lab] = {
            "n":             int(m.sum()),
            "score_mean":    float(s_lab.mean()),
            "score_median":  float(np.median(s_lab)),
            "aff_p10_nm":    float(np.percentile(a_lab, 10)),
            "aff_median_nm": float(np.percentile(a_lab, 50)),
            "aff_p90_nm":    float(np.percentile(a_lab, 90)),
        }
    return summary


# ----------------------------------------------------------------- fig 3

def fig3_score_by_length(data, out_dir: Path) -> dict:
    """strip plot + count bar。
    为什么不用 boxplot: 每长度 binder 数量只有 0–884 个(box 退化)+样本不均衡,
    strip 能直接显示每个 binder 的精确位置。
    """
    lens = sorted(set(int(x) for x in data["pep_len"]))
    rng = np.random.default_rng(0)

    # 计数
    weak_per_len   = []
    strong_per_len = []
    for l in lens:
        weak_per_len.append(int(((data["pep_len"] == l) & (data["label"] == "Weak Binder")).sum()))
        strong_per_len.append(int(((data["pep_len"] == l) & (data["label"] == "Strong Binder")).sum()))

    fig = plt.figure(figsize=(12, 5.6))
    gs = GridSpec(1, 2, figure=fig, width_ratios=[4.2, 1.0], wspace=0.30)
    ax_strip = fig.add_subplot(gs[0, 0])
    ax_cnt   = fig.add_subplot(gs[0, 1])  # 不 sharey:y 轴语义不同

    # --- 左: strip plot,按 label 分色 ---
    for l_idx, l in enumerate(lens):
        for lab, marker, size, alpha in [
            ("Strong Binder", "o", 26, 0.85),
            ("Weak Binder",   "s", 18, 0.55),
        ]:
            m = (data["pep_len"] == l) & (data["label"] == lab)
            ss = data["score"][m]
            if len(ss) == 0:
                continue
            jit = rng.uniform(-0.32, 0.32, size=len(ss))
            ax_strip.scatter(
                np.full_like(ss, l_idx, dtype=float) + jit, ss,
                marker=marker, s=size, color=LABEL_COLOR[lab], alpha=alpha,
                edgecolors="white", linewidths=0.3, zorder=3,
                label=lab if l_idx == 0 else None,  # legend 只一次
            )

    ax_strip.set_xticks(range(len(lens)))
    ax_strip.set_xticklabels([str(l) for l in lens])
    ax_strip.set_xlabel("peptide length (aa)")
    ax_strip.set_ylabel("mhcflurry score")
    ax_strip.set_ylim(-0.02, 1.02)
    ax_strip.set_xlim(-0.7, len(lens) - 0.3)
    ax_strip.grid(True, axis="y", alpha=0.25, linewidth=0.5)
    ax_strip.set_axisbelow(True)
    ax_strip.set_title("per-binder scores, jittered (each dot = one peptide)",
                       fontsize=11)

    # 自定义图例(避免 label 重复)
    handles = [
        plt.Line2D([0], [0], marker="o", linestyle="none", markersize=8,
                   markerfacecolor=LABEL_COLOR["Strong Binder"],
                   markeredgecolor="white", label="Strong Binder"),
        plt.Line2D([0], [0], marker="s", linestyle="none", markersize=7,
                   markerfacecolor=LABEL_COLOR["Weak Binder"],
                   markeredgecolor="white", label="Weak Binder"),
    ]
    ax_strip.legend(handles=handles, loc="lower right", frameon=True, fontsize=9)

    # --- 右: 每长度的 strong + weak 计数 ---
    y = np.arange(len(lens))
    bar_h = 0.4
    ax_cnt.barh(y - bar_h/2, strong_per_len, height=bar_h,
                color=LABEL_COLOR["Strong Binder"], alpha=0.9,
                edgecolor="white", label="Strong")
    ax_cnt.barh(y + bar_h/2, weak_per_len,   height=bar_h,
                color=LABEL_COLOR["Weak Binder"],   alpha=0.9,
                edgecolor="white", label="Weak")

    # 在条上标数
    for i, (sc, wc) in enumerate(zip(strong_per_len, weak_per_len)):
        if sc > 0:
            ax_cnt.text(sc, i - bar_h/2, f" {sc}", va="center", ha="left",
                        fontsize=8, color="#111827")
        if wc > 0:
            ax_cnt.text(wc, i + bar_h/2, f" {wc}", va="center", ha="left",
                        fontsize=8, color="#111827")

    ax_cnt.set_yticks(y)
    ax_cnt.set_yticklabels([str(l) for l in lens])
    ax_cnt.invert_yaxis()
    ax_cnt.set_xlabel("# binders per length")
    ax_cnt.set_xscale("symlog", linthresh=10)
    ax_cnt.grid(True, axis="x", alpha=0.25, linewidth=0.5)
    ax_cnt.set_axisbelow(True)
    ax_cnt.legend(loc="lower right", frameon=True, fontsize=8)
    ax_cnt.set_title("binder counts", fontsize=10)

    fig.suptitle("mhcflurry predicted binders by peptide length  —  HLA-A*02:01",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    out_png = out_dir / "mhcflurry_score_by_length.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)

    summary = {
        "strong_per_length": {str(l): c for l, c in zip(lens, strong_per_len)},
        "weak_per_length":   {str(l): c for l, c in zip(lens, weak_per_len)},
        "binder_total_per_length": {
            str(l): s + w for l, s, w in zip(lens, strong_per_len, weak_per_len)
        },
    }
    return summary


# ----------------------------------------------------------------- fig 4

def fig4_affinity_ecdf(data, out_dir: Path) -> dict:
    """affinity_nM 的 ECDF,按 label 分三条线 + 经典 50/500 nM 阈值竖线。"""
    fig, ax = plt.subplots(figsize=(10, 6.5))

    stats = {}
    for lab in LABEL_ORDER:
        m = (data["label"] == lab) & (data["affinity_nm"] > 0)
        a = np.sort(data["affinity_nm"][m])
        if len(a) == 0:
            continue
        x = np.log10(a)
        y = np.arange(1, len(a) + 1) / len(a)
        ax.plot(x, y, drawstyle="steps-post",
                color=LABEL_COLOR[lab], linewidth=2.0, label=f"{lab}  (n={len(a):,})")
        stats[lab] = {
            "n":        int(len(a)),
            "p10_nM":   float(np.percentile(a, 10)),
            "p50_nM":   float(np.percentile(a, 50)),
            "p90_nM":   float(np.percentile(a, 90)),
            "frac_le_500":  float((a <= WEAK_CUTOFF_NM).mean()),
            "frac_le_50":   float((a <= STRONG_CUTOFF_NM).mean()),
        }

    # 阈值竖线
    for thr_nm, lab in [(STRONG_CUTOFF_NM, f"strong ≤ {STRONG_CUTOFF_NM:.0f} nM"),
                        (WEAK_CUTOFF_NM,   f"weak   ≤ {WEAK_CUTOFF_NM:.0f} nM")]:
        ax.axvline(math.log10(thr_nm), color="#DC2626", linestyle="--",
                   linewidth=1.0, alpha=0.55)
        ax.text(math.log10(thr_nm), 0.02, lab,
                rotation=90, va="bottom", ha="right", fontsize=8.5,
                color="#DC2626", transform=ax.get_xaxis_transform())

    ax.set_xlabel(r"predicted affinity  $\log_{10}$(nM)  —  smaller = stronger binding")
    ax.set_ylabel("ECDF  (cumulative fraction)")
    ax.set_xlim(math.log10(5), 5.0)  # 1e1 ~ 1e5
    ax.set_ylim(0, 1.02)
    ax.set_title("ECDF of mhcflurry predicted binding affinity, by label",
                 fontsize=12)
    ax.legend(loc="lower right", frameon=True, fontsize=9)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)

    fig.tight_layout()
    out_png = out_dir / "mhcflurry_affinity_ecdf.png"
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    log.info("wrote %s", out_png)
    return stats


# ----------------------------------------------------------------- fig 5

def fig5_overview(fig_files: list[Path], out_path: Path) -> None:
    """2x2 dashboard —— 用 PIL 把已有的 png 缩略贴起来,不要再读 DB。"""
    from PIL import Image
    imgs = [Image.open(p) for p in fig_files]
    # 统一尺寸: 取最小宽高
    w = min(im.width for im in imgs)
    h = min(im.height for im in imgs)
    imgs = [im.resize((w, h), Image.LANCZOS) for im in imgs]

    pad = 18
    title_h = 60
    canvas_w = w * 2 + pad * 3
    canvas_h = h * 2 + pad * 3 + title_h
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")

    for i, im in enumerate(imgs[:4]):
        row, col = divmod(i, 2)
        x = pad + col * (w + pad)
        y = title_h + pad + row * (h + pad)
        canvas.paste(im, (x, y))

    # 标题
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    draw.text((pad, 14),
              "mhcflurry on iGEM peptide corpus — 5–15 aa, HLA-A*02:01, n=134,888",
              fill="#111827", font=font)
    draw.text((pad, 42),
              "top-left: label × length   |   top-right: score vs affinity   "
              "|   bottom-left: binder score by length   |   bottom-right: affinity ECDF",
              fill="#374151", font=font_small)

    canvas.save(out_path, dpi=(160, 160))
    log.info("wrote %s", out_path)


# ----------------------------------------------------------------- summary

def write_summary(out_dir: Path, stats: dict) -> None:
    csv_path = out_dir / "mhcflurry_summary.csv"
    # 1) length × label 透视
    rows = stats["fig1"]["per_length"]
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["length", "total", "strong", "weak", "nonbinder", "binder_rate_pct"])
        for ln in sorted(rows.keys(), key=int):
            r = rows[ln]
            w.writerow([ln, r["total"], r["strong"], r["weak"],
                        r["nonbinder"], f"{r['binder_rate_pct']:.3f}"])
        w.writerow([])
        w.writerow(["# fig1 totals"])
        f1 = stats["fig1"]
        w.writerow(["total_n",      f1["total_n"]])
        w.writerow(["strong",       f1["n_strong"]])
        w.writerow(["weak",         f1["n_weak"]])
        w.writerow(["nonbinder",    f1["n_nonbinder"]])
        w.writerow(["pct_strong",   f"{f1['pct_strong']:.4f}"])
        w.writerow(["pct_weak",     f"{f1['pct_weak']:.4f}"])
        w.writerow(["pct_nonbinder", f"{f1['pct_nonbinder']:.4f}"])
    log.info("wrote %s", csv_path)

    json_path = out_dir / "mhcflurry_summary.json"
    with json_path.open("w") as f:
        json.dump(stats, f, indent=2, default=str)
    log.info("wrote %s", json_path)


# ----------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots/mhcflurry")
    p.add_argument("--dsn", default=DB_DSN)
    p.add_argument("--skip-overview", action="store_true")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("connecting %s", args.dsn.split("@")[-1])
    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    data = fetch_mhcflurry(cur)
    cur.close()
    conn.close()

    log.info("plotting fig1: label × length …")
    s1 = fig1_label_by_length(data, out_dir)
    log.info("plotting fig2: score vs affinity hexbin …")
    s2 = fig2_score_vs_affinity(data, out_dir)
    log.info("plotting fig3: score by length (binder subset) …")
    s3 = fig3_score_by_length(data, out_dir)
    log.info("plotting fig4: affinity ECDF …")
    s4 = fig4_affinity_ecdf(data, out_dir)

    fig_files = [
        out_dir / "mhcflurry_label_distribution.png",
        out_dir / "mhcflurry_score_vs_affinity.png",
        out_dir / "mhcflurry_score_by_length.png",
        out_dir / "mhcflurry_affinity_ecdf.png",
    ]
    if not args.skip_overview:
        log.info("plotting fig5: dashboard overview …")
        fig5_overview(fig_files, out_dir / "mhcflurry_overview.png")

    stats = {"fig1": s1, "fig2": s2, "fig3": s3, "fig4": s4}
    write_summary(out_dir, stats)
    log.info("done.")


if __name__ == "__main__":
    sys.exit(main())