#!/usr/bin/env python3
"""
fetch_data.py — 为 AlgPred2 深度分析准备离线数据

输出:
  algpred2_full_scores.npz      全量 score (20.2M float32)
  algpred2_labels.npy           对应 label (字符串)
  algpred2_lengths.npy          对应 sequence_length (int8)
  algpred2_score_by_length.csv  按长度聚合的统计 (n / mean / std / q10/50/90 / allergen_rate)
  algpred2_summary.json         关键数字

来源:
  peptide_enrichment.tool = 'algpred2'  → score / label / details->>'sequence_length'
  peptides.length                       → 仅作为核对(实际用 details 里的 sequence_length)
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("algpred2-fetch")

ROOT = Path("/home/lenovo/Projects/iGEM-platform")
OUT = ROOT / "analysis" / "algpred2"
OUT.mkdir(parents=True, exist_ok=True)

DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"


def fetch_all():
    """一次拉全量 algpred2 score + label + seq_len。"""
    if (OUT / "algpred2_full_scores.npz").exists() and (OUT / "algpred2_labels.npy").exists():
        log.info("loading from cache ...")
        z = np.load(OUT / "algpred2_full_scores.npz")
        s = z["score"].astype(np.float32)
        L = z["seq_len"].astype(np.int16)
        l = np.load(OUT / "algpred2_labels.npy")
        return s, l, L
    log.info("connecting to PG ...")
    conn = psycopg2.connect(DSN)
    conn.set_session(readonly=True)
    cur = conn.cursor(name="algpred2_stream")  # server-side cursor
    cur.itersize = 200_000
    log.info("executing streaming SELECT ...")
    cur.execute(
        """
        SELECT e.score::float8 AS score,
               coalesce(e.label, '') AS label,
               (e.details->>'sequence_length')::int AS seq_len
          FROM peptide_enrichment e
         WHERE e.tool = 'algpred2'
        """
    )

    scores = []
    labels = []
    lens = []
    n = 0
    for row in cur:
        scores.append(float(row[0]) if row[0] is not None else np.nan)
        labels.append(row[1])
        lens.append(int(row[2]))
        n += 1
        if n % 1_000_000 == 0:
            log.info("  streamed %d rows ...", n)
    cur.close()
    conn.close()

    s = np.asarray(scores, dtype=np.float32)
    l = np.asarray(labels, dtype=object)
    L = np.asarray(lens, dtype=np.int16)
    log.info("loaded %d rows  nan=%d  seq_len range=[%d,%d]",
             n, int(np.isnan(s).sum()), int(L.min()), int(L.max()))
    return s, l, L


def by_length_table(s: np.ndarray, l: np.ndarray, L: np.ndarray) -> list[dict]:
    """按 sequence_length 分桶的全量聚合统计。"""
    rows = []
    for Lv in range(int(L.min()), int(L.max()) + 1):
        m = L == Lv
        n = int(m.sum())
        if n == 0:
            continue
        ss = s[m]
        ll = l[m]
        allergen = (ll == "Allergen").sum()
        rows.append({
            "seq_len":      Lv,
            "n":            n,
            "mean_score":   round(float(ss.mean()), 4),
            "std_score":    round(float(ss.std(ddof=0)), 4),
            "q10":          round(float(np.quantile(ss, 0.10)), 4),
            "q50":          round(float(np.quantile(ss, 0.50)), 4),
            "q90":          round(float(np.quantile(ss, 0.90)), 4),
            "n_allergen":   int(allergen),
            "allergen_rate": round(100.0 * allergen / n, 4),
        })
    return rows


def main():
    s, l, L = fetch_all()

    # 全量 score
    np.savez_compressed(
        OUT / "algpred2_full_scores.npz",
        score=s.astype(np.float32),
        seq_len=L.astype(np.int16),
    )
    # labels 单独存:用 0/1 uint8 编码 (0=Non-Allergen, 1=Allergen, 255=other)
    l_enc = np.full(s.shape, 255, dtype=np.uint8)
    l_enc[l == "Allergen"]    = 1
    l_enc[l == "Non-Allergen"]= 0
    np.save(OUT / "algpred2_labels.npy", l_enc, allow_pickle=False)
    log.info("wrote algpred2_full_scores.npz, algpred2_labels.npy (uint8)")

    # 按长度聚合
    rows = by_length_table(s, l, L)
    csv_path = OUT / "algpred2_score_by_length.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %s (%d rows)", csv_path, len(rows))

    # summary.json
    valid = s[~np.isnan(s)]
    n_all = int((l == "Allergen").sum())
    n_non = int((l == "Non-Allergen").sum())
    summary = {
        "tool": "algpred2",
        "library_size": int(s.shape[0]),
        "score_min":    float(valid.min()),
        "score_max":    float(valid.max()),
        "score_mean":   float(valid.mean()),
        "score_median": float(np.median(valid)),
        "score_std":    float(valid.std(ddof=0)),
        "score_q05":    float(np.quantile(valid, 0.05)),
        "score_q95":    float(np.quantile(valid, 0.95)),
        "score_q99":    float(np.quantile(valid, 0.99)),
        "threshold":    0.3,
        "n_allergen":   n_all,
        "n_nonallergen":n_non,
        "allergen_rate_global": round(100.0 * n_all / s.shape[0], 4),
        "frac_above_threshold": round(100.0 * (valid >= 0.3).mean(), 4),
        "frac_above_0.5": round(100.0 * (valid >= 0.5).mean(), 4),
        "seq_len_range": [int(L.min()), int(L.max())],
    }
    with (OUT / "algpred2_summary.json").open("w") as f:
        json.dump(summary, f, indent=2)
    log.info("wrote algpred2_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())