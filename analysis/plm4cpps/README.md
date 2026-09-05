# pLM4CPPs — 2000 万肽库运行结果统计可视化

## 输入
- **总肽数**: 20,248,885
- **已知 CPP (label='CPP')**: 681,222
- **背景采样**: 500,000 (随机抽样)
- **pLM4CPPs 全量 CPP 分数**: 已导出到 `results/plots/plm4cpps/plm4cpps_cpp_scores.npz`
- **pLM4CPPs 按长度的全量聚合统计**: `results/plots/plm4cpps/plm4cpps_score_by_length.csv`

## 输出图表

| 文件 | 内容 |
|------|------|
| `01_score_histogram_overview.png` | 全库 vs CPP 直方图 (线性 + log-Y 双视图) |
| `02_score_distribution_cpp_vs_bg.png` | CPP vs 背景的 violin + box + KDE 对比 |
| `03_score_by_length_box.png` | 按肽长度区间的 score 分布 (log-Y 折带图) |
| `04_score_vs_length_density.png` | score 分位带 vs 长度 + 库长度分布柱 |
| `05_score_ecdf.png` | 全库 / CPP 的 ECDF + 阈值对照表 |
| `06_threshold_curve.png` | 不同 score 阈值的 (召回数 / TPR / Precision) 曲线 |
| `07_cohens_d_comparison.png` | pLM4CPPs vs 其他 9 个工具的 Cohen's d 对比 |
| `08_score_quantile_heatmap.png` | length × score quantile 热力图 (log scale) |
| `09_roc_curve.png` | CPP vs 背景 ROC 曲线 + AUC + 工作点标注 |

## 关键统计 (写入 `summary.json`)

```
library_size          : 20,248,885
background μ/σ/med    : 0.0219 / 0.1021 / 0.0003
CPP     μ/σ/med       : 0.4540 / 0.3170 / 0.2819
CPP 范围              : [0.1500, 1.0000]
Cohen's d (full data)  : 1.84
Cohen's d (reference) : 7.19  (基于更严格的 CPP 子集)
ROC AUC               : 0.9832
```

## 关键发现

1. **强长尾分布**: 全库 score 集中在 [0, 0.01], 但有约 1% 的肽 score > 0.5
2. **CPP 输出下限 ≈ 0.15**: 模型对任何输入输出的 CPP score 不会低于 ~0.15
3. **AUC = 0.983**: 工具在已知 CPP 上的判别能力极强
4. **阈值选择**:
   - 阈值 0.5: 召回 33.9% 的已知 CPP, 过滤出 234k 条肽
   - 阈值 0.7: 召回 27.0% 的已知 CPP, 过滤出 187k 条肽
   - 阈值 0.9: 召回 19.9% 的已知 CPP, 过滤出 139k 条肽

## 复现

```bash
/tmp/plot_venv/bin/python analysis/plm4cpps/plot_plm4cpps.py
```

无 DB 调用 — 全部使用 `results/plots/` 下已离线准备好的数据。