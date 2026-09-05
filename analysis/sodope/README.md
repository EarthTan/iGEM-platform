# SoDoPe 在 2000 万肽库上的运行结果统计可视化

**生成日期**: 2026-08-28
**总肽库规模**: 20,248,885 (3..30 aa)
**Insoluble**: 9,956,436 (49.2%)
**Soluble**:   10,292,449 (50.8%)

## 数据来源

所有图表均从**本地 .npz / .csv** 离线生成, 不需要 DB 连接:

| 文件 | 来源 | 内容 |
|------|------|------|
| `results/plots/score_samples.npz`                | 现有   | 50 万随机抽样 score (全库) |
| `results/plots/sodope/sodope_labels_full.npz`    | 新拉   | 9.96M Insoluble + 10.29M Soluble 全量 score |
| `results/plots/sodope/sodope_label_summary.csv`  | 新拉   | Insoluble / Soluble 全表 server-side 分位 |
| `results/plots/sodope/sodope_score_by_length.csv`| 新拉   | 长度 × score 全表 server-side 分位 |
| `results/plots/score_summary.csv`                | 现有   | 各工具总体统计 |
| `results/plots/cohen_d_table.csv`                | 现有   | 跨工具 Cohen's d |
| `results/plots/library/length_distribution.csv`  | 现有   | 肽库长度分布 |

## 图表 (9 张)

| 文件 | 内容 |
|------|------|
| `01_score_overview.png`              | 总体 score 分布: 线性 + log-Y 双面板 (展示 U 型) |
| `02_insoluble_vs_soluble.png`        | Insoluble vs Soluble 分布: violin/box + log-Y KDE (Cohen's d = -3.91) |
| `03_score_by_length_box.png`         | 按长度 score 聚合: 全表 mean ± p10..p90 + σ (server-side) |
| `04_score_vs_length_density.png`     | score vs length 2D 直方图 (长度均匀抽样) + 每长度 mean |
| `05_score_ecdf.png`                  | ECDF: 全库 / Insoluble / Soluble (t=0.5 处完美分界) |
| `06_threshold_curve.png`             | Recall / FPR / 绝对数量 vs threshold 双面板 |
| `07_cohens_d_comparison.png`         | SoDoPe vs 其他 9 个工具 Cohen's d (金色突出) |
| `08_score_quantile_heatmap.png`      | length × {p10, p25, p50, p75, p90, p95} 热力图 |
| `09_roc_curve.png`                   | ROC 曲线 + AUC (AUC = 1.0, 完美分类) |
| `summary.json`                       | 关键数字汇总 (means / median / σ / operating point) |

## 关键发现

1. **完美双峰分离**: Insoluble score ∈ [0, 0.5], Soluble score ∈ [0.5, 1], **零重叠**。
2. **AUC = 1.0000** (理论上完美分类): 在 t = 0.5 处 TPR = 100%, FPR = 0%。
3. **Cohen's d ≈ -3.91** (全量 Insoluble vs Soluble): 极大分离强度, 在跨工具中排名靠前
   (仅次于 mhcflurry = 36.97, tipred = 15.38, plm4cpps = 7.19 等)。

## 复现

```bash
# 数据准备 (一次性, 已落盘):
#   results/plots/sodope/sodope_labels_full.npz
#   results/plots/sodope/sodope_label_summary.csv
#   results/plots/sodope/sodope_score_by_length.csv
# (拉数脚本在 plot_sodope.py 的 docstring 里有说明)

# 一次性重新生成所有图表:
/tmp/plot_venv/bin/python analysis/sodope/plot_sodope.py
```