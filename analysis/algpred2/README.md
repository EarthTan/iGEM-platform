# AlgPred2 — 2000 万肽库运行结果统计可视化

## 输入

- **总肽数**: 20,248,885
- **模型**: AlgPred2 SDK `Random Forest (AAC)`,硬编码阈值 = **0.3**
- **全量 score / label / sequence_length**: 已离线导出到
  `analysis/algpred2/algpred2_full_scores.npz` + `algpred2_labels.npy`(uint8 编码)+ `algpred2_score_by_length.csv`
- **关键摘要**: `analysis/algpred2/algpred2_summary.json`

## 输出图表

| 文件 | 内容 |
|---|---|
| `01_score_histogram_overview.png` | 总体 score 直方图 (linear + log-Y 双视图)|
| `02_allergen_vs_non_distribution.png` | Allergen vs Non-Allergen KDE + box |
| `03_score_by_length_box.png` | 按肽长度区间 (5 个桶) 的 score 分布 (box) |
| `04_allergen_rate_by_length.png` | Allergen 占比随长度的变化 + 库长度分布(双轴)|
| `05_score_ecdf.png` | 全库 / Allergen / Non-Allergen 的 ECDF + 阈值标注 |
| `06_threshold_curve.png` | 不同 score 阈值能召回的肽数量 (log + 百分比)|
| `07_cohens_d_comparison.png` | AlgPred2 vs 其他 10 个工具配置的 Cohen's d |
| `08_score_quantile_heatmap.png` | length × score-quantile 热力图(count, log)|
| `09_score_vs_length_density.png` | score 分位带 + 2D hexbin 密度 |

## 关键统计(写入 `algpred2_summary.json`)

```
library_size         : 20,248,885
score    μ / σ / med  : 0.2720 / 0.0832 / 0.2680
score range          : [0.011, 0.924]
score q05 / q95 / q99: 0.144 / 0.412 / 0.492
n_allergen           : 7,114,908   (35.14%)
n_nonallergen        : 13,133,977  (64.86%)
SDK threshold        : 0.3   (label='Allergen' iff score ≥ 0.3)
frac ≥ threshold     : 35.14%
frac ≥ 0.5           : 0.87%
seq_len range         : [1, 30]
Cohen's d (AlgPred2) : 2.606  (Allergen vs Non-Allergen)
```

## 关键发现

1. **全局 score 形态**:近正态(μ=0.272, σ=0.083,range=[0.011, 0.924])。SDK 阈值 0.3 几乎正好落在 mean 处,与全库的 35.14% Allergen 占比一致。
2. **KDE 完美分到阈值两侧**:Non-Allergen (μ=0.224) 与 Allergen (μ=0.360) 的两条密度曲线在 0.30 附近"擦肩而过",Cohen's d = 2.61(极强),这是 SDK 训练良好的体现。
3. **score 与长度强负相关**(`09_score_vs_length_density.png` 的上半子图):
   - tiny (3-7) median = 0.373
   - short (8-13) median = 0.315
   - medium (14-19) median = 0.290
   - long (20-25) median = 0.276
   - xlong (26-30) median = 0.260
4. **Allergen 占比随长度从 ~98% 跌到 ~33%**(`04_allergen_rate_by_length.png`):
   - 长度 ≤ 7 aa 的肽 **82-100%** 被标为 Allergen
   - 长度 20-30 aa(主库 94%)只有 **30-41%** 被标为 Allergen
   - 暗示 AlgPred2 RF (AAC) 模型在短序列上倾向过预测 Allergen(训练集偏倚)
5. **高置信子集**:score ≥ 0.5 仅占 **0.87%** (≈176k 肽),score ≥ 0.7 占 **0.10%** — 用作"高置信 Allergen"很合适。
6. **Cohen's d 在跨工具中**排名第 8(+2.61),低于 pLM4CPPs (+7.19) / AnOxPePred (+4.34) / HemoPI2 (+3.29)等;AlgPred2 在 2000 万肽库上的"二分类清晰度"偏弱,主要是因为短肽样本被全部分到 Allergen,稀释了边界。

## 阈值选择建议

| threshold | 召回数 | % 库 | 备注 |
|---|---|---|---|
| 0.30 | 7,114,908 | 35.14% | SDK 默认,基本 = 全局 Allergen 占比 |
| 0.40 | 1,300,571 |  6.42% | 聚焦高置信 Allergen |
| 0.50 |   176,171 |  0.87% | "极可能 Allergen" |
| 0.60 |    31,876 |  0.16% | "近确定 Allergen" |

(基于 ECDF + 阈值扫描实测)

## 复现

```bash
# 一次性从 DB 拉全量数据 → 离线文件
/tmp/plot_venv/bin/python analysis/algpred2/fetch_data.py

# 画 9 张图(全部基于离线数据,无 DB)
/tmp/plot_venv/bin/python analysis/algpred2/plot_algpred2.py
```

## 历史 / 命名约定

- 所有图表文件名带前缀 `0N_` 以便排序
- `01..09` 与 `analysis/plm4cpps/`、`analysis/tipred/` 的图序对齐,便于跨工具对照
- `cohen_d_table.csv` 用现有 `results/plots/cohen_d_table.csv`,AlgPred2 的 d=+2.61 在跨工具概览中可视化(`07_cohens_d_comparison.png`)