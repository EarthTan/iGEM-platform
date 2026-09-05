#!/usr/bin/env python3
"""
plot_amp_esm_transformed.py — amp-esm score 多种变换对比展示(纯可视化,不改进模型)

用途:展示不同数学变换如何"重塑"分布外观。仅供视觉对比,不应用于下游分析。

变换:
  - identity   (原 score)
  - log1p      log(1+x),保 [0,1] → [0, ln 2]
  - sqrt
  - asinh      arcsinh(x/σ),对称、稳定、log-like
  - logit      log(x/(1-x)),[0,1]→(-∞,+∞),标准概率到实数映射
                注意:score∈{0,1}会映射到 ±∞,需要先 clip
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde


DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
SAMPLE_CAP = 200_000   # 变换展示不需要 50 万,20 万够了,跑得快


def fetch(conn, cap=SAMPLE_CAP):
    sql = """
        SELECT score, label
          FROM peptide_enrichment
         WHERE tool = 'amp-esm' AND score IS NOT NULL
         ORDER BY random() LIMIT %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (cap,))
        rows = cur.fetchall()
    s = np.fromiter((r["score"] for r in rows), dtype=np.float32, count=len(rows))
    lbl = np.array([r["label"] or "" for r in rows])
    return s, lbl


def transform(x: np.ndarray, kind: str) -> np.ndarray:
    if kind == "identity":
        return x.astype(np.float64)
    if kind == "log1p":
        return np.log1p(x)
    if kind == "sqrt":
        return np.sqrt(x)
    if kind == "asinh":
        return np.arcsinh(x)
    if kind == "logit":
        # clip 避免 ±∞
        x_c = np.clip(x, 1e-5, 1 - 1e-5)
        return np.log(x_c / (1.0 - x_c))
    raise ValueError(kind)


def kde_safe(data: np.ndarray, n_sub: int = 50_000):
    if len(data) >= n_sub:
        data = np.random.default_rng(0).choice(data, n_sub, replace=False)
    try:
        kde = gaussian_kde(data, bw_method="scott")
        xs = np.linspace(data.min(), data.max(), 400)
        return xs, kde(xs)
    except Exception:
        return None, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots/amp_esm")
    p.add_argument("--dsn", default=DSN)
    p.add_argument("--cap", type=int, default=SAMPLE_CAP)
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(args.dsn) as conn:
        conn.set_session(readonly=True)
        score, label = fetch(conn, args.cap)
    print(f"sampled {len(score):,} rows")
    print(f"  AMP     : {(label == 'AMP').sum():,} ({(label == 'AMP').mean()*100:.2f}%)")
    print(f"  non-AMP : {(label == 'non-AMP').sum():,} ({(label == 'non-AMP').mean()*100:.2f}%)")

    transforms = ["identity", "log1p", "sqrt", "asinh", "logit"]
    pretty = {
        "identity": "identity  (raw score)",
        "log1p":    "log(1 + score)",
        "sqrt":     "sqrt(score)",
        "asinh":    "asinh(score)",
        "logit":    "logit(score) = log(p / (1-p))",
    }

    # ---- 图 A: 5 种变换 KDE 大图(全样本 + 分组)
    fig, axes = plt.subplots(2, 3, figsize=(18, 9))
    axes_flat = axes.flatten()
    for ax, kind in zip(axes_flat, transforms):
        s_all = transform(score, kind)
        for lbl, color in [("AMP", "#E45756"), ("non-AMP", "#54A24B")]:
            mask = label == lbl
            if mask.sum() < 100:
                continue
            data = s_all[mask]
            xs, ys = kde_safe(data)
            if xs is not None:
                ax.plot(xs, ys, color=color, linewidth=1.8, label=f"{lbl} (n={mask.sum():,})")
            ax.hist(data, bins=60, density=True, alpha=0.30, color=color, edgecolor="none")
        ax.set_title(pretty[kind], fontsize=11)
        ax.grid(True, alpha=0.25, linewidth=0.5)
        ax.set_xlabel("transformed value")
        ax.set_ylabel("density")
        if kind == "identity":
            ax.axvline(0.5, color="grey", linestyle="--", alpha=0.6, label="threshold 0.5")
        if kind == "logit":
            ax.axvline(0, color="grey", linestyle="--", alpha=0.6, label="logit(0.5) = 0")
        ax.legend(loc="upper right", fontsize=8)
    # 第 6 格空着,放总结文字
    axes_flat[5].axis("off")
    txt = (
        "FOR DISPLAY ONLY\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "变换不改 separation,\n"
        "只改视觉形态。\n\n"
        "• logit 是概率→实数的标准映射\n"
        "• score=0.5 → logit=0\n"
        "• 不能拿变换后值当 threshold\n"
        "• AUC / PR / 跟其他工具对比\n"
        "  必须用原 score\n"
    )
    axes_flat[5].text(0.5, 0.5, txt, ha="center", va="center",
                      family="monospace", fontsize=11,
                      bbox=dict(boxstyle="round,pad=0.5", facecolor="#FFF3CD",
                                edgecolor="#E0C200"))
    fig.suptitle(f"amp-esm score — visual transformation comparison  (n={len(score):,})",
                 fontsize=14, y=1.00)
    fig.tight_layout()
    out_a = out_dir / "amp_esm_transforms_kde.png"
    fig.savefig(out_a, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out_a)

    # ---- 图 B: 单一变换 (logit) 详细版,带分位数信息
    s_logit = transform(score, "logit")
    amp_logit = s_logit[label == "AMP"]
    non_logit = s_logit[label == "non-AMP"]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for data, color, name in [(amp_logit, "#E45756", "AMP"),
                              (non_logit, "#54A24B", "non-AMP")]:
        xs, ys = kde_safe(data)
        if xs is not None:
            ax.plot(xs, ys, color=color, linewidth=2.2, label=name)
        ax.hist(data, bins=80, density=True, alpha=0.30, color=color, edgecolor="none")

    ax.axvline(0, color="grey", linestyle="--", alpha=0.7, label="logit(0.5) = 0")
    # 加分位线
    for q in [0.25, 0.5, 0.75]:
        for data, color in [(amp_logit, "#E45756"), (non_logit, "#54A24B")]:
            v = np.quantile(data, q)
            ax.axvline(v, color=color, linestyle=":", alpha=0.4, linewidth=0.8)

    ax.set_xlim(-10, 6)
    ax.set_xlabel("logit(score)")
    ax.set_ylabel("density")
    ax.set_title("amp-esm score after logit transform — visual only (n=%d)" % len(score),
                 fontsize=12)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.legend(loc="upper right")

    # 文字框:叠在一起的描述
    txt2 = (
        "⚠ logit 变换后:\n"
        f"  non-AMP median  = {np.median(non_logit):.2f}\n"
        f"  AMP     median  = {np.median(amp_logit):.2f}\n"
        f"  Cohen's d (两组均值差/合并 sd) ≈ "
        f"{(amp_logit.mean()-non_logit.mean())/np.sqrt((amp_logit.std()**2+non_logit.std()**2)/2):.3f}\n"
        "\n"
        "d < 0.2: 极弱分离(基本不可分)\n"
        "d 0.2-0.5: 弱\n"
        "d 0.5-0.8: 中\n"
        "d > 0.8: 强"
    )
    ax.text(0.02, 0.97, txt2, transform=ax.transAxes,
            va="top", ha="left", family="monospace", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFF3CD",
                      edgecolor="#E0C200", alpha=0.92))
    fig.tight_layout()
    out_b = out_dir / "amp_esm_logit_detail.png"
    fig.savefig(out_b, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out_b)

    # ---- 顺带算 Cohen's d 给个数字(每个变换都算)
    print("\n=== Cohen's d (AMP vs non-AMP) on each transform ===")
    print(f"{'transform':<12}{'d':>8}{'note'}")
    for kind in transforms:
        s = transform(score, kind)
        a, n = s[label == "AMP"], s[label == "non-AMP"]
        if len(a) < 2 or len(n) < 2:
            continue
        pooled = np.sqrt((a.var(ddof=1) * (len(a)-1) + n.var(ddof=1) * (len(n)-1)) / (len(a)+len(n)-2))
        d = (a.mean() - n.mean()) / pooled if pooled > 0 else 0.0
        note = []
        if kind == "identity" and abs(d) < 0.5:
            note.append("→ 弱分离:确认模型区分度差,非变换问题")
        if abs(d) < 0.2:
            note.append("极弱")
        elif abs(d) < 0.5:
            note.append("弱")
        elif abs(d) < 0.8:
            note.append("中")
        else:
            note.append("强")
        print(f"{kind:<12}{d:>8.3f}  {' / '.join(note)}")


if __name__ == "__main__":
    sys.exit(main())