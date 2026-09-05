#!/usr/bin/env python3
"""
plot_amp_esm.py — amp-esm 预测结果分布分析

输出到 results/plots/amp_esm/:
  - amp_esm_score.png               主 score 直方图 + KDE
  - amp_esm_score_by_label.png      AMP / non-AMP 两组分布叠加
  - amp_esm_submodels.png           5-fold 子模型概率叠加
  - amp_esm_score_by_length.png     按长度分桶的 score KDE 网格
  - amp_esm_score_vs_log.png        score × amplify_log_scaled_score 散点
  - amp_esm_summary.csv             统计摘要
  - amp_esm_samples.npz             抽样 raw(供后续分析)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from scipy.stats import gaussian_kde

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("plot-amp-esm")


DB_DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
SAMPLE_CAP = 500_000
HIST_BINS = 80


def fetch(conn, tool="amp-esm", cap=SAMPLE_CAP):
    """拉 score + label + 关键 details 字段。
    对超大表走 ORDER BY random() LIMIT cap。
    """
    sql = """
        SELECT score, label, details,
               (details->>'length')::int        AS length,
               (details->>'charge')::int        AS charge,
               details->>'mode'                 AS mode,
               details->>'version'              AS version,
               (details->>'amplify_log_scaled_score')::float AS log_scaled,
               details->'sub_model_probabilities'              AS subprobs
          FROM peptide_enrichment
         WHERE tool = %s
           AND score IS NOT NULL
         ORDER BY random()
         LIMIT %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (tool, cap))
        rows = cur.fetchall()
    return rows


def to_arrays(rows):
    n = len(rows)
    score = np.fromiter((r["score"] for r in rows), dtype=np.float32, count=n)
    label = np.array([r["label"] or "" for r in rows])
    length = np.fromiter((r["length"] or 0 for r in rows), dtype=np.int16, count=n)
    charge = np.fromiter((r["charge"] or 0 for r in rows), dtype=np.int8, count=n)
    mode = np.array([r["mode"] or "" for r in rows])
    version = np.array([r["version"] or "" for r in rows])
    log_scaled = np.array(
        [(r["log_scaled"] if r["log_scaled"] is not None else np.nan) for r in rows],
        dtype=np.float32,
    )
    # subprobs 是 list[5]
    subprobs = np.array(
        [list(r["subprobs"]) if r["subprobs"] else [np.nan]*5 for r in rows],
        dtype=np.float32,
    )
    return dict(score=score, label=label, length=length, charge=charge,
                mode=mode, version=version, log_scaled=log_scaled,
                subprobs=subprobs)


def hist_kde(ax, data, label, color, bins=HIST_BINS, kde=True):
    s = data[~np.isnan(data)]
    if len(s) == 0:
        ax.text(0.5, 0.5, "no data", transform=ax.transAxes, ha="center")
        return
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-12:
        hi = lo + 1e-12
    ax.hist(s, bins=np.linspace(lo, hi, bins+1), density=True,
            alpha=0.45, color=color, edgecolor="white", linewidth=0.3,
            label=f"{label} hist (n={len(s):,})")
    if kde and len(s) >= 100:
        sub = s if len(s) <= 50_000 else np.random.default_rng(0).choice(s, 50_000, replace=False)
        try:
            xs = np.linspace(lo, hi, 400)
            ys = gaussian_kde(sub, bw_method="scott")(xs)
            ax.plot(xs, ys, color=color, linewidth=1.8, label=f"{label} KDE")
        except Exception as e:
            log.warning("KDE fail for %s: %s", label, e)
    ax.set_xlim(lo, hi)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="results/plots/amp_esm")
    p.add_argument("--dsn", default=DB_DSN)
    p.add_argument("--cap", type=int, default=SAMPLE_CAP)
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(args.dsn) as conn:
        conn.set_session(readonly=True)
        rows = fetch(conn, cap=args.cap)
    log.info("sampled %d rows", len(rows))

    a = to_arrays(rows)
    np.savez_compressed(out_dir / "amp_esm_samples.npz", **a)

    # ============ 图 1: 主 score 分布
    fig, ax = plt.subplots(figsize=(8, 5))
    hist_kde(ax, a["score"], "score", "#4C78A8")
    mu, med, std = float(a["score"].mean()), float(np.median(a["score"])), float(a["score"].std())
    ax.axvline(0.5, color="grey", linestyle="--", alpha=0.5, label="threshold 0.5")
    ax.text(0.98, 0.95,
            f"μ = {mu:.4f}\nmedian = {med:.4f}\nσ = {std:.4f}\nn = {len(a['score']):,}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9, family="monospace",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85, edgecolor="none"))
    ax.set_xlabel("amp-esm score")
    ax.set_ylabel("density")
    ax.set_title("amp-esm primary score distribution (sampled)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "amp_esm_score.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # ============ 图 2: AMP / non-AMP 分组
    fig, ax = plt.subplots(figsize=(8, 5))
    for lbl, col in [("AMP", "#E45756"), ("non-AMP", "#54A24B"), ("ERROR", "#888888")]:
        mask = a["label"] == lbl
        if mask.sum() == 0:
            continue
        hist_kde(ax, a["score"][mask], lbl, col)
    ax.axvline(0.5, color="grey", linestyle="--", alpha=0.5)
    ax.set_xlabel("amp-esm score")
    ax.set_ylabel("density")
    ax.set_title(f"amp-esm score by label  (sampled, n={len(a['score']):,})")
    ax.legend(loc="upper center")
    ax.grid(True, alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "amp_esm_score_by_label.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # ============ 图 3: 5-fold 子模型
    fig, ax = plt.subplots(figsize=(8.5, 5))
    sub_colors = ["#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B"]
    for i in range(5):
        col = a["subprobs"][:, i]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            continue
        hist_kde(ax, valid, f"fold-{i+1}", sub_colors[i])
    ax.set_xlabel("sub_model_probability")
    ax.set_ylabel("density")
    ax.set_title("amp-esm 5-fold sub-model probability distributions")
    ax.legend(loc="upper center", ncol=3, fontsize=9)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "amp_esm_submodels.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # ============ 图 4: 按长度分桶的 score KDE
    bins = [(1, 10), (11, 15), (16, 20), (21, 25), (26, 30)]
    titles = ["3-10aa", "11-15aa", "16-20aa", "21-25aa", "26-30aa"]
    fig, axes = plt.subplots(1, 5, figsize=(20, 4.2), sharey=True)
    for ax, (lo, hi), title in zip(axes, bins, titles):
        mask = (a["length"] >= lo) & (a["length"] <= hi)
        n = mask.sum()
        if n == 0:
            ax.set_title(f"{title}\n(n=0)")
            ax.text(0.5, 0.5, "no data", transform=ax.transAxes, ha="center")
            continue
        s = a["score"][mask]
        # 强制 0..1 范围
        s = s[~np.isnan(s)]
        # 直方图(密度)
        ax.hist(s, bins=HIST_BINS, density=True, color="#4C78A8", alpha=0.55,
                edgecolor="white", linewidth=0.2)
        if len(s) >= 100:
            try:
                sub = s if len(s) <= 50_000 else np.random.default_rng(0).choice(s, 50_000, replace=False)
                xs = np.linspace(0, 1, 400)
                ys = gaussian_kde(sub, bw_method="scott")(xs)
                ax.plot(xs, ys, color="#E45756", linewidth=1.6)
            except Exception:
                pass
        # AMP rate
        if "AMP" in a["label"]:
            amp_rate = float((a["label"][mask] == "AMP").mean())
        else:
            amp_rate = 0.0
        ax.set_xlim(0, 1)
        ax.set_title(f"{title}\nn={n:,}  AMP={amp_rate*100:.2f}%")
        ax.set_xlabel("score")
        ax.grid(True, alpha=0.25, linewidth=0.5)
    axes[0].set_ylabel("density")
    fig.suptitle("amp-esm score by peptide length bucket", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "amp_esm_score_by_length.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # ============ 图 5: score vs amplify_log_scaled_score 散点
    fig, ax = plt.subplots(figsize=(8, 6.5))
    valid = (~np.isnan(a["score"])) & (~np.isnan(a["log_scaled"]))
    sv, lv = a["score"][valid], a["log_scaled"][valid]
    # 用 hexbin(避免 50w 点过度密集)
    hb = ax.hexbin(sv, lv, gridsize=120, cmap="viridis", mincnt=1, norm=LogNorm())
    cb = fig.colorbar(hb, ax=ax, label="count (log)")
    ax.axvline(0.5, color="white", linestyle="--", alpha=0.5)
    ax.set_xlabel("amp-esm score")
    ax.set_ylabel("amplify_log_scaled_score (from details)")
    ax.set_title(f"primary score vs log-scaled score  (n={int(valid.sum()):,})")
    ax.grid(True, alpha=0.2, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "amp_esm_score_vs_log.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # ============ CSV 摘要
    n_total = len(a["score"])
    n_amp = int((a["label"] == "AMP").sum())
    n_non = int((a["label"] == "non-AMP").sum())
    n_err = int((a["label"] == "ERROR").sum())
    summary = {
        "sampled": n_total,
        "amp_label_count": n_amp,
        "amp_label_pct": round(100 * n_amp / n_total, 4),
        "nonamp_label_count": n_non,
        "nonamp_label_pct": round(100 * n_non / n_total, 4),
        "error_label_count": n_err,
        "score_mean": round(float(a["score"].mean()), 6),
        "score_median": round(float(np.median(a["score"])), 6),
        "score_std": round(float(a["score"].std()), 6),
        "score_min": round(float(a["score"].min()), 6),
        "score_max": round(float(a["score"].max()), 6),
        "log_scaled_mean": round(float(np.nanmean(a["log_scaled"])), 4),
        "log_scaled_median": round(float(np.nanmedian(a["log_scaled"])), 4),
        "submodel_means": [round(float(np.nanmean(a["subprobs"][:, i])), 4) for i in range(5)],
        "modes": {m: int((a["mode"] == m).sum()) for m in np.unique(a["mode"])},
        "versions": {v: int((a["version"] == v).sum()) for v in np.unique(a["version"])},
        "length_buckets": {
            f"{lo}-{hi}": {
                "n": int(((a["length"] >= lo) & (a["length"] <= hi)).sum()),
                "score_mean": round(float(a["score"][(a["length"] >= lo) & (a["length"] <= hi)].mean()), 6),
                "amp_rate": round(float((a["label"][(a["length"] >= lo) & (a["length"] <= hi)] == "AMP").mean()), 4),
            }
            for (lo, hi) in bins
        },
    }
    (out_dir / "amp_esm_summary.json").write_text(json.dumps(summary, indent=2))

    # CSV(一键看)
    csv_path = out_dir / "amp_esm_summary.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for k, v in summary.items():
            if isinstance(v, (dict, list)):
                w.writerow([k, json.dumps(v, default=str)])
            else:
                w.writerow([k, v])

    print("\n=== amp-esm summary ===")
    print(json.dumps(summary, indent=2, default=str))
    print("\nwrote:")
    for f in ["amp_esm_score.png", "amp_esm_score_by_label.png",
              "amp_esm_submodels.png", "amp_esm_score_by_length.png",
              "amp_esm_score_vs_log.png", "amp_esm_summary.csv",
              "amp_esm_summary.json", "amp_esm_samples.npz"]:
        print(" ", out_dir / f)


if __name__ == "__main__":
    sys.exit(main())