# TIPred — 2000 万肽库运行结果统计可视化

## 输入
- **总肽数**: 20,248,885 (TIPred 对全库都做了预测)
- **预测为 TIP (label='TIP')**: 20,144,125 (99.5%)
- **预测为 non-TIP (label='non-TIP')**: 104,760 (0.5%)
- **背景随机抽样**: 500,000 (score_samples.npz)
- **全量 non-TIP 分数**: 104,760 (`results/plots/tipred/tipred_nontip_scores.npz`)
- **TIPred 按长度的全量聚合**: `results/plots/tipred/tipred_score_by_length.csv`
- **抽样 + length + label**: 198,478 条 (`results/plots/tipred/tipred_with_length.npz`)

## 关键语义
- TIPred 是 *tumor-homing peptide* 预测器,默认 threshold = 0.5
- **`score < 0.5` → 预测为 TIP (阳性)**
- **`score ≥ 0.5` → 预测为 non-TIP (阴性)**
- 注意: label 字段存的是**预测结果**(与 pLM4CPPs 不同, pLM4CPPs 的 label 是 ground truth)

## 输出图表

| 文件 | 内容 |
|------|------|
| `01_score_histogram_overview.png` | 全库 vs predicted non-TIP 直方图 (线性 + log-Y) |
| `02_score_distribution_predicted.png` | 预测 TIP vs non-TIP: violin + 直方图对比 |
| `03_score_by_length_box.png` | 按长度区间的 score 分布 (线性折带) |
| `04_score_vs_length_density.png` | score × length 散点 + 分位带 + 库分布柱 |
| `05_score_ecdf.png` | 全库 / predicted non-TIP ECDF + 阈值表 |
| `06_threshold_curve.png` | 阈值 / 召回数 / TPR (拒绝率) / FPR 曲线 |
| `07_cohens_d_comparison.png` | TIPred vs 其他工具的 Cohen's d |
| `08_score_quantile_heatmap.png` | length × score quantile 热力图 |
| `09_label_share_by_length.png` | 各长度段预测为 TIP / non-TIP 的占比 |

## 关键统计 (写入 `summary.json`)

```
library_size               : 20,248,885
predicted TIP    (full)    : 20,144,125 (99.5%)
predicted non-TIP (full)   : 104,760 (0.5%)
background score  μ/σ/med  : 0.9180 / 0.0614 / 0.9274
non-TIP score     μ/σ/med  : 0.2987 / 0.1446 / 0.3128
non-TIP 范围                : [0.0189, 0.4999]
Cohen's d (full, predicted): -5.58
Cohen's d (table reference): -15.38  (基于 200k 随机子集)
```

## 关键发现

1. **预测几乎全是 TIP**: 默认阈值 0.5 下, 20.1M (99.5%) 肽被预测为 TIP, 仅 105k (0.5%) 被预测为 non-TIP
2. **Score 分布紧凑**: 全库 score 集中在 [0.85, 0.95] (μ=0.918, σ=0.061); non-TIP 集中在 [0.05, 0.50] (μ=0.299, σ=0.145)
3. **明显的双峰分离**: 0.5 是自然的决策边界, 两侧几乎没有 overlap
4. **小肽倾向于被预测为 TIP**: length ≤ 10 的肽, TIP 占比 100%; length 20+ 时 non-TIP 占比约 0.5%
5. **Cohen's d ≈ -5.58 (基于预测标签的全集)**: 这是真实的强分离
6. **⚠️ Cohen's d 表里的 -15.38 是误导**: 那是在 200k 随机抽样里碰巧抓到 982 个 TIP / 199018 non-TIP 计算的, 实际 TIP 在全库是 2014万

## 阈值 (对比表)

| thr | 全体 ≥ thr (predict TIP 之外) | non-TIP ≥ thr (仍然误叫 TIP) |
|-----|------------|-----------|
| 0.05 | 20,248,885 | 104,748 |
| 0.10 | 20,241,757 | 97,092 |
| 0.30 | 20,199,518 | 55,073 |
| **0.50** (default) | 20,141,889 | **0** |
| 0.70 | 19,895,865 | 0 |
| 0.90 | 19,091,701 | 0 |

## 复现

```bash
/tmp/plot_venv/bin/python analysis/tipred/plot_tipred.py
```

无 DB 调用 — 全部使用 `results/plots/` 下已离线准备好的数据。
脚本首次运行会从 DB 拉全量数据到 npz / csv(若文件不存在),需要时执行 `analysis/tipred/fetch_with_len.py` 可补充更新抽样。