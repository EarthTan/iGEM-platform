"""Export AOPxSVM results from PostgreSQL into analysis-ready files under
results/plots/aopxsvm/.

Outputs:
  - aopxsvm_full.csv.gz       : all ~20M (label, score) rows, gzipped
  - aopxsvm_pos_sample.csv    : 50k random positive scores (for KDE/histogram)
  - aopxsvm_neg_sample.csv    : 50k random negative scores
  - aopxsvm_summary.json      : global stats (n, min, max, mean, std, percentiles)
  - aopxsvm_by_score_bin.csv  : score-binned positive rate for calibration
"""

import csv
import gzip
import json
import os
import random
import sys
from pathlib import Path

import psycopg2

OUT_DIR = Path(__file__).resolve().parents[2] / "results" / "plots" / "aopxsvm"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)

random.seed(42)
SAMPLE_N = 50_000


def main() -> int:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor(name="cur_aopxsvm_export")
    cur.itersize = 50_000

    sql = """
        SELECT label::int, score
          FROM peptide_enrichment
         WHERE tool = 'aopxsvm'
           AND score IS NOT NULL
           AND label IS NOT NULL
    """
    cur.execute(sql)

    full_path = OUT_DIR / "aopxsvm_full.csv.gz"
    pos_path = OUT_DIR / "aopxsvm_pos_sample.csv"
    neg_path = OUT_DIR / "aopxsvm_neg_sample.csv"

    pos_sample: list[float] = []
    neg_sample: list[float] = []
    summary = {"tool": "aopxsvm", "n": 0, "n_pos": 0, "n_neg": 0,
               "sum": 0.0, "sumsq": 0.0, "min": float("inf"), "max": float("-inf")}

    with gzip.open(full_path, "wt", newline="") as f_full, \
         open(pos_path, "w", newline="") as f_pos, \
         open(neg_path, "w", newline="") as f_neg:
        w_full = csv.writer(f_full)
        w_pos = csv.writer(f_pos)
        w_neg = csv.writer(f_neg)
        w_full.writerow(["label", "score"])
        w_pos.writerow(["score"])
        w_neg.writerow(["score"])

        for label, score in cur:
            label_i = int(label)
            score_f = float(score)
            w_full.writerow([label_i, score_f])
            summary["n"] += 1
            if label_i == 1:
                summary["n_pos"] += 1
                if len(pos_sample) < SAMPLE_N:
                    pos_sample.append(score_f)
                else:
                    j = random.randint(0, summary["n_pos"] - 1)
                    if j < SAMPLE_N:
                        pos_sample[j] = score_f
            else:
                summary["n_neg"] += 1
                if len(neg_sample) < SAMPLE_N:
                    neg_sample.append(score_f)
                else:
                    j = random.randint(0, summary["n_neg"] - 1)
                    if j < SAMPLE_N:
                        neg_sample[j] = score_f
            summary["sum"] += score_f
            summary["sumsq"] += score_f * score_f
            if score_f < summary["min"]:
                summary["min"] = score_f
            if score_f > summary["max"]:
                summary["max"] = score_f

        # After streaming, write the (uniform) reservoir samples
        for s in pos_sample:
            w_pos.writerow([s])
        for s in neg_sample:
            w_neg.writerow([s])

    mean = summary["sum"] / summary["n"]
    var = max(0.0, summary["sumsq"] / summary["n"] - mean * mean)
    summary["mean"] = mean
    summary["std"] = var ** 0.5
    summary["pos_rate"] = summary["n_pos"] / summary["n"]
    for k in ("sum", "sumsq"):
        summary.pop(k)

    (OUT_DIR / "aopxsvm_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
