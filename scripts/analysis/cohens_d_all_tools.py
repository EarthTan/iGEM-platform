#!/usr/bin/env python3
"""
cohens_d_all_tools.py — 每个工具的阳性 vs 阴性 score 分离强度

对每个工具:
  - 阳性 label:看 README/约定(TIP 在 tipred 里反而 score 高,需要专门处理)
  - Cohen's d = (mean_pos - mean_neg) / pooled_sd
  - 输出 cohen_d_table.csv + cohens_d_all_tools.png(横向条形图)
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
OUT_DIR = Path("results/plots")


def fetch_tool_scores(conn, tool: str, cap: int = 200_000):
    """拉 score + label + 抽样,够算 Cohen's d,200k 足够稳定。"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT score, label FROM peptide_enrichment "
            "WHERE tool=%s AND score IS NOT NULL ORDER BY random() LIMIT %s",
            (tool, cap),
        )
        rows = cur.fetchall()
    s = np.array([r["score"] for r in rows], dtype=np.float32)
    l = np.array([r["label"] or "" for r in rows])
    return s, l


def cohens_d(pos: np.ndarray, neg: np.ndarray) -> float:
    """Cohen's d = (mean_pos - mean_neg) / pooled_sd
    pooled_sd = sqrt(((n1-1)*var1 + (n2-1)*var2) / (n1+n2-2))
    """
    n1, n2 = len(pos), len(neg)
    if n1 < 2 or n2 < 2:
        return float("nan")
    m1, m2 = pos.mean(), neg.mean()
    v1, v2 = pos.var(ddof=1), neg.var(ddof=1)
    pooled = np.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    if pooled < 1e-12:
        return float("nan")
    return (m1 - m2) / pooled


# 定义每个工具的"阳性 label"和"阴性 label"
# tipred 特殊:按 score 推断 TIP 是更"积极"那一边(score 高的那一边)
TOOLS = [
    {"tool": "algpred2",    "pos": "Allergen",      "neg": "Non-Allergen"},
    {"tool": "anoxpepred-frs",      "pos": "FRS_active",   "neg": "FRS_inactive"},
    {"tool": "anoxpepred-chelating", "pos": "Chel_active",  "neg": "Chel_inactive"},
    {"tool": "anoxpepred",           "pos": "Antioxidant",  "neg": "Non-antioxidant"},  # legacy 派生前 original 头
    {"tool": "hemopi2",     "pos": "Hemolytic",     "neg": "Non-Hemolytic"},
    {"tool": "plm4cpps",    "pos": "CPP",           "neg": "non-CPP"},
    {"tool": "sodope",      "pos": "Insoluble",     "neg": "Soluble"},
    {"tool": "tipred",      "pos": "non-TIP",       "neg": "TIP"},
    {"tool": "toxinpred3",  "pos": "Toxin",         "neg": "Non-Toxin"},
    {"tool": "amp-esm",     "pos": "AMP",           "neg": "non-AMP"},
    {"tool": "mhcflurry",   "pos": "Strong Binder", "neg": "Non-Binder"},
    {"tool": "mhcflurry_weak", "pos": "Weak Binder", "neg": "Non-Binder"},  # 单独看弱结合
]


def strength_label(d: float) -> str:
    ad = abs(d)
    if ad < 0.2:   return "极弱"
    if ad < 0.5:   return "弱"
    if ad < 0.8:   return "中"
    if ad < 1.2:   return "强"
    if ad < 2.0:   return "很强"
    return "极强"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    with psycopg2.connect(DSN) as conn:
        conn.set_session(readonly=True)
        for spec in TOOLS:
            tool = spec["tool"].replace("_weak", "")
            score, label = fetch_tool_scores(conn, tool)
            pos_label, neg_label = spec["pos"], spec["neg"]
            pos = score[label == pos_label]
            neg = score[label == neg_label]

            # 过滤到干净二分类:非 pos/非 neg 的样本丢掉
            d = cohens_d(pos, neg)
            rows.append({
                "tool": tool,
                "config": f"{pos_label} vs {neg_label}",
                "n_pos": int(len(pos)),
                "n_neg": int(len(neg)),
                "pos_mean": round(float(pos.mean()), 4) if len(pos) else None,
                "neg_mean": round(float(neg.mean()), 4) if len(neg) else None,
                "pos_median": round(float(np.median(pos)), 4) if len(pos) else None,
                "neg_median": round(float(np.median(neg)), 4) if len(neg) else None,
                "pos_std":   round(float(pos.std(ddof=1)), 4) if len(pos) > 1 else None,
                "neg_std":   round(float(neg.std(ddof=1)), 4) if len(neg) > 1 else None,
                "cohen_d":   round(float(d), 4) if not np.isnan(d) else None,
                "abs_d":     round(abs(float(d)), 4) if not np.isnan(d) else None,
                "strength":  strength_label(d) if not np.isnan(d) else "n/a",
                "note": "tipred 阳性=non-TIP(score 低)" if tool == "tipred" else "",
            })
            print(f"{tool:<12} {pos_label:<14} vs {neg_label:<14}  d={d:+.3f}  [{strength_label(d)}]")

    # --- CSV
    csv_path = OUT_DIR / "cohen_d_table.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nwrote {csv_path}")

    # --- 图:横向条形(d 值正负)
    # 按 d 值排序,带颜色编码强度
    sorted_rows = sorted(rows, key=lambda r: (r["abs_d"] if r["abs_d"] is not None else 0), reverse=True)
    names = [f"{r['tool']}\n({r['config'].split(' vs ')[0]})" for r in sorted_rows]
    ds = [r["cohen_d"] for r in sorted_rows]
    colors = ["#E45756" if d < 0 else "#54A24B" for d in ds]

    fig, ax = plt.subplots(figsize=(11, 7))
    ypos = np.arange(len(names))
    bars = ax.barh(ypos, ds, color=colors, edgecolor="white", linewidth=0.5)

    # 强度文字 + 数值
    for i, (b, r) in enumerate(zip(bars, sorted_rows)):
        x = r["cohen_d"]
        ad = r["abs_d"]
        ax.text(x + (0.05 if x >= 0 else -0.05),
                b.get_y() + b.get_height() / 2,
                f"d={x:+.3f}  [{r['strength']}]",
                va="center",
                ha="left" if x >= 0 else "right",
                fontsize=9, family="monospace")

    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=10)
    ax.invert_yaxis()
    ax.axvline(0, color="black", linewidth=0.8)
    ax.axvline(0.2, color="grey", linestyle=":", alpha=0.4)
    ax.axvline(0.5, color="grey", linestyle=":", alpha=0.4)
    ax.axvline(0.8, color="grey", linestyle=":", alpha=0.4)
    ax.axvline(-0.2, color="grey", linestyle=":", alpha=0.4)
    ax.axvline(-0.5, color="grey", linestyle=":", alpha=0.4)
    ax.axvline(-0.8, color="grey", linestyle=":", alpha=0.4)
    ax.set_xlabel("Cohen's d  (positive = pos has higher score)")
    ax.set_xlim(-2.0, max(6.0, max(abs(d) for d in ds if d is not None) + 0.5))
    ax.set_title("Cohen's d per tool — separation strength of binary labels on score", fontsize=12)
    ax.grid(True, axis="x", alpha=0.25, linewidth=0.5)
    ax.text(0.99, 0.02,
            "Cohen's d reference:\n"
            " 0.2  weak\n 0.5  medium\n 0.8  strong\n 1.2+ very strong",
            transform=ax.transAxes, va="bottom", ha="right",
            fontsize=8, family="monospace",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF3CD", alpha=0.85, edgecolor="none"))
    fig.tight_layout()
    fig.savefig(OUT_DIR / "cohens_d_all_tools.png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {OUT_DIR / 'cohens_d_all_tools.png'}")


if __name__ == "__main__":
    main()