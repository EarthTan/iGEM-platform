# TemStaPro `score` 列语义定义

> 由 `extract_temstapro_full.py` 自动生成于 2026-08-24,
> 反推自 `peptide_enrichment.details::jsonb` 全表 20,248,885 行。

## 定义

`peptide_enrichment.score` 列(TemStaPro 工具下)**等价于**:

```
score ≡ mean(details.thresholds['40','45','50','55','60','65'].raw)
```

即 6 个温度阈值(40/45/50/55/60/65 °C)的 raw 稳定概率的算术平均。

**复核证据**(全表 Pearson 相关):

| 量 | Pearson r vs `score` | n |
|---|---|---|
| `mean(t40..t65 raw)` | **0.9999999835** | 20,248,885 |

与 1.0 的偏差是 float32 量化的尾数噪声,与1.0 等价。

## Caveat:语义定义 ≠ 域内有效

> **重要**:本文件定义的是 `score` 列在数据库里的**计算语义**(它是怎么算出来的),
> 不代表它在 3–30 aa 短肽上的**预测有效性**。

TemStaPro 上游服务在全长蛋白训练,从未在短肽上校准。
本文件不回答以下问题,这些问题由评估报告 §3 处理:

- 该工具在 3–30 aa 短肽上是否能给出有意义的稳定概率?
- `thermophilicity` 标签在短肽上的校准度如何?
- `score` 列能否作为 peptide 库的下游筛选信号?

如果短肽域外,`score` 列即使在 [0,1] 区间分布"正常",
也可能仅反映模型的随机猜测输出。是否使用、如何使用,
请参见评估报告 §3 与本目录下其他 CSV 的全表证据。

## 全表结构证据(供下游判断)

全表 n = 20,248,885 行,其中:

| `thermophilicity` | n | T=40 mean | T=65 mean |
|---|---|---|---|
| thermophilic | 12,283,340 | 0.7020 | 0.4424 |
| mesophilic   | 7,965,545 | 0.3961 | 0.2235 |
| Δ (thermo − meso) | | **+0.3059** | **+0.2189** |

thermophilic 与 mesophilic 两组的 raw 概率在每个温度上都可分,且 Δ 在 6 个温度上
均 ≥ 0.2。但**这仅说明信号有结构,不说明结构正确**——训练域与短肽域的偏移问题见评估报告 §3。

## 物理解释

| 维度 | 内容 |
|---|---|
| npz 文件 | `results/plots/temstapro_full_samples.npz` (500,000 行 × 11 维 float32) |
| CSV 证据 | `temstapro_full_summary.csv` / `temstapro_temperature_gradient.csv` |
| 上游字段位置 | `peptide_enrichment.details.thresholds[*].raw` |
| 上游字段数 | 6 温度 × (raw + binary + seeds[5]) = 18 维;本文件聚焦 raw(11 维) |
| `clash` / `thermophilicity` | 另存于 `details.clash` / `details.thermophilicity` 字符串标签 |

## 版本控制

- 本文档由 `scripts/analysis/extract_temstapro_full.py` 生成,不可手动编辑。
- 修改上游 service 协议时,请同步更新该脚本中的 `TEMP_COLS` 与 jsonb 路径。
