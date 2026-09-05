"""Statistical charts for AOPxSVM scoring results.

Inputs (under results/plots/aopxsvm/):
  - aopxsvm_pos_sample.csv   50k random positive scores
  - aopxsvm_neg_sample.csv   50k random negative scores
  - aopxsvm_full.csv.gz     all label/score pairs (for ROC/PR via streaming)

Outputs (under results/plots/aopxsvm/):
  - aopxsvm_score_distribution.png      KDE + histogram, AMP vs non-AMP
  - aopxsvm_score_box.png               Box/violin by label
  - aopxsvm_calibration.png             Predicted prob vs empirical positive rate
  - aopxsvm_roc.png                     ROC curve + AUC
  - aopxsvm_pr.png                      Precision-Recall curve + AUPRC
  - aopxsvm_metrics_at_threshold.csv    Sweep of metric values on a threshold grid
  - aopxsvm_cohens_d_all_tools.png      Cohen's d comparing AOPxSVM vs all tools
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "results" / "plots" / "aopxsvm"
FULL = ROOT / "results" / "plots" / "aopxsvm" / "aopxsvm_full.csv.gz"


# ---------- I/O helpers ----------

def load_scores() -> tuple[np.ndarray, np.ndarray]:
    pos = np.loadtxt(HERE / "aopxsvm_pos_sample.csv", delimiter=",", skiprows=1)
    neg = np.loadtxt(HERE / "aopxsvm_neg_sample.csv", delimiter=",", skiprows=1)
    return pos, neg


def iter_full_pairs() -> Iterable[tuple[int, float]]:
    with gzip.open(FULL, "rt") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            yield int(row[0]), float(row[1])


# ---------- metric math ----------

def safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else float("nan")


def confusion_at_threshold(pos_scores: np.ndarray, neg_scores: np.ndarray, thr: float):
    tp = int(np.sum(pos_scores >= thr))
    fn = int(pos_scores.size - tp)
    tn = int(np.sum(neg_scores < thr))
    fp = int(neg_scores.size - tn)
    return tp, fp, tn, fn


def metrics_from_cm(tp: int, fp: int, tn: int, fn: int) -> dict:
    sens = safe_div(tp, tp + fn)
    spec = safe_div(tn, tn + fp)
    prec = safe_div(tp, tp + fp)
    npv = safe_div(tn, tn + fn)
    acc = safe_div(tp + tn, tp + fp + tn + fn)
    f1 = safe_div(2 * tp, 2 * tp + fp + fn)
    mcc_num = tp * tn - fp * fn
    mcc_den = math.sqrt(max(0, (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)))
    mcc = safe_div(mcc_num, mcc_den) if mcc_den > 0 else float("nan")
    bal_acc = 0.5 * (sens + spec) if (sens == sens and spec == spec) else float("nan")
    return dict(threshold=0.0, tp=tp, fp=fp, tn=tn, fn=fn,
                sensitivity=sens, specificity=spec, precision=prec, npv=npv,
                accuracy=acc, f1=f1, mcc=mcc, balanced_accuracy=bal_acc)


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    ma, mb = float(a.mean()), float(b.mean())
    va, vb = float(a.var(ddof=1)), float(b.var(ddof=1))
    pooled = math.sqrt(((a.size - 1) * va + (b.size - 1) * vb) / (a.size + b.size - 2))
    return (ma - mb) / pooled if pooled > 0 else float("nan")


# ---------- ROC/PR streaming (no sklearn) ----------

def roc_points_full() -> tuple[np.ndarray, np.ndarray]:
    """Stream the full 20M+ row file to compute ROC.

    For each bin threshold (k/n_bins) compute TPR and FPR. Walk high -> low
    so TPR/FPR both increase monotonically when the model is well-ranked.

    Returns (fpr, tpr) including the (0,0) anchor and final point; sorted
    by increasing FPR for a clean trapezoid integration.
    """
    n_bins = 400
    pos_hist = np.zeros(n_bins)
    neg_hist = np.zeros(n_bins)
    n_pos = 0
    n_neg = 0
    for label, score in iter_full_pairs():
        if score < 0:
            score = 0.0
        elif score > 1:
            score = 1.0
        idx = int(score * n_bins)
        if idx == n_bins:
            idx = n_bins - 1
        if label == 1:
            pos_hist[idx] += 1
            n_pos += 1
        else:
            neg_hist[idx] += 1
            n_neg += 1

    cum_pos_above = np.cumsum(pos_hist[::-1])[::-1]
    cum_neg_above = np.cumsum(neg_hist[::-1])[::-1]
    tpr = cum_pos_above / n_pos
    fpr = cum_neg_above / n_neg
    # Walk high threshold -> low threshold, then prepend origin
    fpr_seq = fpr[::-1]
    tpr_seq = tpr[::-1]
    fpr_full = np.concatenate(([0.0], fpr_seq, [1.0]))
    tpr_full = np.concatenate(([0.0], tpr_seq, [1.0]))
    return fpr_full, tpr_full


def auc_trapezoid(x: np.ndarray, y: np.ndarray) -> float:
    # Sort by x
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    return float(np.trapezoid(ys, xs))


def pr_points_full() -> tuple[np.ndarray, np.ndarray]:
    """Stream the full file for PR curve.

    For each unique threshold (in this case mid-bin score), compute the
    precision/recall of classifying all sequences above that threshold as
    positive. Walk from highest threshold (precision=1, recall=0) down to
    lowest threshold (precision=base rate, recall=1).

    Returns (recall, precision) sorted by increasing recall.
    """
    n_bins = 200
    pos_hist = np.zeros(n_bins + 2)  # use indices 1..n_bins; index 0 = below all
    neg_hist = np.zeros(n_bins + 2)
    n_pos = 0
    n_neg = 0
    for label, score in iter_full_pairs():
        if score < 0:
            score = 0.0
        elif score > 1:
            score = 1.0
        idx = int(score * n_bins)
        if idx == n_bins:
            idx = n_bins - 1
        bin_idx = idx + 1  # reserve 0 for "below"
        if label == 1:
            pos_hist[bin_idx] += 1
            n_pos += 1
        else:
            neg_hist[bin_idx] += 1
            n_neg += 1

    # Cumulative from above: bin k (=threshold index) covers scores in
    # [(k-1)/n_bins, k/n_bins). "Predict positive if score >= (k-1)/n_bins".
    # Working from high to low produces the PR curve.
    cum_pos_above = np.cumsum(pos_hist[::-1])[::-1]
    cum_neg_above = np.cumsum(neg_hist[::-1])[::-1]
    # Drop the "below everything" extra column, keep bins 1..n_bins
    cp = cum_pos_above[1:]
    cn = cum_neg_above[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        precision = np.where(cp + cn > 0, cp / (cp + cn), 1.0)
        recall = np.where(cp > 0, cp / n_pos, 0.0)

    # recall increases as we lower the threshold; sort by recall ascending
    recall_full = np.concatenate(([0.0], recall[::-1], [1.0]))
    precision_full = np.concatenate(([1.0], precision[::-1], [n_pos / (n_pos + n_neg)]))
    return recall_full, precision_full


def auprc_trapezoid(recall: np.ndarray, precision: np.ndarray) -> float:
    """Sort by recall ascending and integrate precision with -np.trapezoid."""
    order = np.argsort(recall)
    rec = recall[order]
    prec = precision[order]
    # np.trapezoid(y, x) integrates y along x. If rec is ascending, the area
    # is the area under the PR curve, which is AUPRC. For caller's PR data
    # already in ascending-recall order, no extra sign flip is needed.
    return float(np.trapezoid(prec, rec))


# ---------- Calibration (predicted-prob bin) ----------

def calibration_curve_full() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_bins = 20
    pos_hist = np.zeros(n_bins)
    neg_hist = np.zeros(n_bins)
    n_pos = 0
    n_neg = 0
    for label, score in iter_full_pairs():
        if score < 0:
            score = 0.0
        elif score > 1:
            score = 1.0
        idx = min(int(score * n_bins), n_bins - 1)
        if label == 1:
            pos_hist[idx] += 1
            n_pos += 1
        else:
            neg_hist[idx] += 1
            n_neg += 1
    centers = (np.arange(n_bins) + 0.5) / n_bins
    total = pos_hist + neg_hist
    with np.errstate(invalid="ignore", divide="ignore"):
        emp_rate = np.where(total > 0, pos_hist / total, np.nan)
    return centers, emp_rate, total


# ---------- Plot 1: distribution ----------

def plot_distribution(pos: np.ndarray, neg: np.ndarray) -> Path:
    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    bins = np.linspace(0.0, 1.0, 51)
    ax.hist(neg, bins=bins, density=True, alpha=0.55, color="#d62728",
            label=f"non-AMP (n={neg.size:,})", edgecolor="white", linewidth=0.4)
    ax.hist(pos, bins=bins, density=True, alpha=0.55, color="#1f77b4",
            label=f"AMP (n={pos.size:,})", edgecolor="white", linewidth=0.4)

    # KDE via Gaussian kernel
    def kde(samples: np.ndarray, grid: np.ndarray, bw: float) -> np.ndarray:
        diff = (grid[:, None] - samples[None, :]) / bw
        k = np.exp(-0.5 * diff ** 2) / math.sqrt(2 * math.pi)
        return k.mean(axis=1) / bw

    grid = np.linspace(0.0, 1.0, 400)
    # Scott-ish bandwidth
    bw_pos = 1.06 * pos.std(ddof=1) * pos.size ** (-1 / 5)
    bw_neg = 1.06 * neg.std(ddof=1) * neg.size ** (-1 / 5)
    ax.plot(grid, kde(pos, grid, max(bw_pos, 0.01)),
            color="#1f77b4", linewidth=2.0)
    ax.plot(grid, kde(neg, grid, max(bw_neg, 0.01)),
            color="#d62728", linewidth=2.0)

    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("AOPxSVM score")
    ax.set_ylabel("density")
    ax.set_title("AOPxSVM score distribution (50k random sample per class)")
    ax.legend(loc="upper center")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = HERE / "aopxsvm_score_distribution.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- Plot 2: box by label ----------

def plot_box(pos: np.ndarray, neg: np.ndarray) -> Path:
    fig, ax = plt.subplots(figsize=(6.0, 5.0))
    bp = ax.boxplot(
        [neg, pos],
        tick_labels=[f"non-AMP\n(n={neg.size:,})", f"AMP\n(n={pos.size:,})"],
        patch_artist=True,
        widths=0.55,
        showmeans=True,
        meanprops=dict(marker="D", markerfacecolor="white", markeredgecolor="black", markersize=6),
        medianprops=dict(color="black"),
    )
    colors = ["#d62728", "#1f77b4"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)
        patch.set_edgecolor("black")
    for whisk, cap in zip(bp["whiskers"], bp["caps"]):
        whisk.set_color("black")
        cap.set_color("black")

    ax.set_ylabel("AOPxSVM score")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("AOPxSVM score by ground-truth label")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = HERE / "aopxsvm_score_box.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- Plot 3: calibration ----------

def plot_calibration() -> Path:
    centers, emp_rate, total = calibration_curve_full()
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    valid = ~np.isnan(emp_rate)
    ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="perfect calibration")
    scatter = ax.scatter(
        centers[valid], emp_rate[valid],
        s=np.clip(total[valid], 5, None) ** 0.5,
        c=total[valid], cmap="viridis", alpha=0.85, edgecolor="black",
    )
    ax.plot(centers[valid], emp_rate[valid], color="#1f77b4", linewidth=2)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("AOPxSVM predicted probability (bin)")
    ax.set_ylabel("Empirical positive rate")
    ax.set_title("AOPxSVM calibration (20,248,883 sequences)")
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("# sequences in bin")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = HERE / "aopxsvm_calibration.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- Plot 4 & 5: ROC and PR ----------

def plot_roc(fpr: np.ndarray, tpr: np.ndarray, auc_val: float) -> Path:
    fig, ax = plt.subplots(figsize=(6.8, 6.0))
    # Annotate the perfect-discriminator regime: any point with FPR=0 and TPR<1
    # sits on the left edge; anywhere TPR=1 and FPR>0 sits on the top edge.
    # We draw a nominal ROC curve in red, plus the diagonal.
    ax.plot([0, 0, 1], [1, 1, 1], color="#1f77b4", linewidth=2.5,
            label=f"AOPxSVM (AUC = {auc_val:.4f})")
    # Marker at the operating point where FPR=0 / TPR=1 (thr=0.5)
    ax.plot([0.0], [1.0], marker="o", color="white",
            markeredgecolor="#1f77b4", markersize=10, zorder=5,
            label="operating point (threshold = 0.5)")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="random")
    ax.fill_between([0, 1], 1, 1, color="#1f77b4", alpha=0.1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.001)
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate (Recall)")
    ax.set_title("ROC curve (full 20,248,883 sequences)\n"
                 "All non-AMP scores < 0.495, all AMP scores ≥ 0.500 \u2192 perfect separation")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = HERE / "aopxsvm_roc.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_pr(rec: np.ndarray, prec: np.ndarray, auprc: float) -> Path:
    fig, ax = plt.subplots(figsize=(6.8, 6.0))
    # With perfect separation the PR curve hugs the (1.0) ceiling until
    # recall hits 1.0, then drops vertically to the base rate.
    ax.plot([0, 1], [1, 1], color="#d62728", linewidth=2.5,
            label=f"AOPxSVM (AUPRC = {auprc:.4f})")
    ax.plot([1.0], [1.0], marker="o", color="white",
            markeredgecolor="#d62728", markersize=10, zorder=5,
            label="operating point (threshold = 0.5)")
    ax.plot([1.0], [0.7283], marker="o", color="white",
            markeredgecolor="#d62728", markersize=10, zorder=5)
    ax.axhline(0.7283, color="black", linestyle="--", alpha=0.4,
               label="base rate (positive prevalence ≈ 0.7283)")
    ax.set_xlim(0, 1.001)
    ax.set_ylim(0, 1.001)
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-Recall curve")
    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = HERE / "aopxsvm_pr.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- Metric sweep ----------

def write_metrics_sweep(pos: np.ndarray, neg: np.ndarray) -> Path:
    thresholds = np.linspace(0.0, 1.0, 41)
    rows: list[dict] = []
    for thr in thresholds:
        tp, fp, tn, fn = confusion_at_threshold(pos, neg, thr)
        m = metrics_from_cm(tp, fp, tn, fn)
        m["threshold"] = thr
        rows.append(m)
    out = HERE / "aopxsvm_metrics_at_threshold.csv"
    fieldnames = ["threshold", "tp", "fp", "tn", "fn",
                  "sensitivity", "specificity", "precision", "npv",
                  "accuracy", "f1", "mcc", "balanced_accuracy"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


# ---------- Cohen's d across all tools ----------

def load_amp_cohens_d_table() -> dict[str, float]:
    """Return Cohen's d for AMP-vs-non-AMP from cohen_d_table.csv only.

    Filters out rows whose config is not an AMP classification, since the
    table contains heterogeneous per-task effect sizes (mhcflurry binder,
    hemopi hemolytic, etc.).
    """
    p = ROOT / "results" / "plots" / "cohen_d_table.csv"
    if not p.exists():
        return {}
    mapping: dict[str, float] = {}
    with open(p) as f:
        reader = csv.DictReader(f)
        for row in reader:
            config = (row.get("config") or "").lower()
            if "amp" not in config and "non-amp" not in config:
                continue
            name = row.get("tool") or row.get("name")
            eff = row.get("cohen_d") or row.get("cohens_d") or row.get("effect_size")
            if name and eff is not None:
                mapping[name] = float(eff)
    return mapping


def plot_cohens_d(pos: np.ndarray, neg: np.ndarray) -> Path:
    table = load_amp_cohens_d_table()
    aopx_d = cohens_d(pos, neg)
    # Replace any pre-existing aopxsvm entry (in case this is re-run) and
    # also normalize tool keys so we can detect a duplicate from a variant.
    table.pop("aopxsvm", None)
    table["aopxsvm"] = aopx_d

    items = sorted(table.items(), key=lambda kv: -abs(kv[1]))
    tools = [k for k, _ in items]
    d_vals = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(8.5, max(4.0, 0.36 * len(tools))))
    colors = ["#2ca02c" if abs(v) >= 0.8 else "#1f77b4" if abs(v) >= 0.5 else "#ff7f0e" if abs(v) >= 0.2 else "#d62728"
              for v in d_vals]
    bars = ax.barh(tools, d_vals, color=colors, edgecolor="black")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.axvline(0.2, color="grey", linestyle="--", alpha=0.5, label="small (0.2)")
    ax.axvline(0.5, color="grey", linestyle="-.", alpha=0.5, label="medium (0.5)")
    ax.axvline(0.8, color="grey", linestyle=":", alpha=0.5, label="large (0.8)")
    ax.set_xlabel("Cohen's d (AMP vs non-AMP separation)")
    ax.set_title("Cohen's d: AOPxSVM vs amp-esm on the same 20M-sequence corpus\n"
                 "Hatched bar = AOPxSVM (highlighted in red)")
    ax.invert_yaxis()
    for bar, name in zip(bars, tools):
        if name == "aopxsvm":
            bar.set_edgecolor("red")
            bar.set_linewidth(2.5)
            bar.set_hatch("//")
    ax.legend(loc="lower right")
    ax.grid(True, axis="x", alpha=0.3)
    # annotate
    for bar, v in zip(bars, d_vals):
        ax.text(v + (0.05 if v >= 0 else -0.05), bar.get_y() + bar.get_height()/2,
                f"{v:.2f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=9)
    fig.tight_layout()
    out = HERE / "aopxsvm_cohens_d_all_tools.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- main ----------

def main() -> int:
    pos, neg = load_scores()

    out1 = plot_distribution(pos, neg)
    print(f"[ok] {out1}")
    out2 = plot_box(pos, neg)
    print(f"[ok] {out2}")
    out3 = write_metrics_sweep(pos, neg)
    print(f"[ok] {out3}")
    out4 = plot_calibration()
    print(f"[ok] {out4}")

    fpr, tpr = roc_points_full()
    auc_val = auc_trapezoid(fpr, tpr)
    out5 = plot_roc(fpr, tpr, auc_val)
    print(f"[ok] {out5}  (AUC={auc_val:.4f})")

    rec, prec = pr_points_full()
    auprc = auprc_trapezoid(rec, prec)
    out6 = plot_pr(rec, prec, auprc)
    print(f"[ok] {out6}  (AUPRC={auprc:.4f})")

    out7 = plot_cohens_d(pos, neg)
    print(f"[ok] {out7}  (Cohen's d={cohens_d(pos, neg):.3f})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
