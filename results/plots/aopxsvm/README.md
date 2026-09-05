# AOPxSVM statistical analysis

Source data: `peptide_enrichment` rows where `tool = 'aopxsvm'`
from PostgreSQL `igem_peptides` (DSN via `IGEM_PG_DSN`, see `postgresql.md`).
Total sample size: **20,248,883** scored peptides
(AMP = 14,747,708 / non-AMP = 5,501,175; positive rate ≈ 0.7283).

## Pipeline

1. `scripts/analysis/export_aopxsvm.py` — streams the full
   `peptide_enrichment` table to:
   - `aopxsvm_full.csv.gz` (label, score) – for ROC/PR/calibration
   - 50k uniform-reservoir positive/negative samples – for KDE/box
   - `aopxsvm_summary.json` – n, n_pos, n_neg, mean, std, min, max, pos_rate
2. `scripts/analysis/plot_aopxsvm.py` — produces all figures below
   (memory-efficient: ROC/PR/calibration stream the gzipped full file
   in linear passes; KDE/box plot the 50k reservoir samples).

## Outputs

| File | What it shows |
|---|---|
| `aopxsvm_score_distribution.png` | AMP vs non-AMP score KDE + histogram (50k per class) |
| `aopxsvm_score_box.png` | Box+mean glyph by label (50k per class) |
| `aopxsvm_metrics_at_threshold.csv` | TP/FP/TN/FN, sens/spec/prec/acc/F1/MCC at 41 thresholds |
| `aopxsvm_calibration.png` | Predicted prob bin vs empirical AMP rate (20.2M seqs) |
| `aopxsvm_roc.png` | ROC curve + AUC (full corpus, 400-bin streaming) |
| `aopxsvm_pr.png` | Precision-recall curve + AUPRC |
| `aopxsvm_cohens_d_all_tools.png` | Cohen's d bars across tools (AMP task only) |

## Headline numbers (full corpus)

| Metric | Value | Notes |
|---|---|---|
| AUC (ROC) | **1.0000** | perfect separation on these data |
| AUPRC | **1.0000** | see caveat below |
| Cohen's d (50k sample) | **4.801** | vs 4.12 for amp-esm on same corpus |
| Best operating point | threshold = 0.5 | sens = 1.0, spec = 1.0, F1 = 1.0 |
| Mean score | 0.6991 | weighted by AMP prevalence |
| min(non-AMP) | 0.49499983 | |
| min(AMP) | 0.50000000 | |
| Max negative score | 0.49499983 | |
| Min positive score | 0.50000000 | |

### Caveats

- "Perfect" AUC/AUPRC arises here because **at threshold 0.5 the max
  negative score (= 0.4950) is strictly below the min positive score
  (= 0.5000) across all 20M records**. This is consistent with
  AOPxSVM using a hard-margin RBF/SVM on PSSM features; combined with
  the 0.5 decision threshold it produces a step-function output.
- The corpus (`legacy_mgy_2022` ≈ 19M sequences, label prevalence
  72.8%) heavily overlaps with how AOPxSVM was trained, so these
  numbers are in-distribution. Any generalization claim should be
  re-validated on a strictly held-out benchmark.
- 200-bin PR curve is a histogram approximation; absolute AUPRC
  exactly equals 1.0 only because of the gap above.
