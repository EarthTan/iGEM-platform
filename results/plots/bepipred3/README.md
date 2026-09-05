# BepiPred-3.0 20M enrichment 统计结果

> 工具: BepiPred-3.0 v0.0.12.7 (ESM-2 t33 650M + 5-model DenseNet ensemble)
> 数据源: `peptide_enrichment WHERE tool='bepipred3'`
> 生成时间: 2026-08-25
> 跑分: 20,248,885 / 20,248,885 (100%), 0 errors, 7.12h (peak 1147 seq/s, GPU RTX 5880 Ada + bf16)

## 一、Headline numbers

| 指标 | 值 |
|---|---|
| Total rows | **20,248,885** |
| Epitope (score ≥ 0.1512) | **17,842,900 (88.12%)** |
| Non-epitope | 2,405,985 (11.88%) |
| ERROR | 0 |
| Score min / p01 / p25 / median / p75 / p99 / max | 0.0299 / 0.078 / 0.200 / 0.233 / 0.257 / 0.312 / 0.367 |
| Score mean / std | 0.2221 / 0.0525 |

## 二、图清单 (results/plots/bepipred3/)

| 图 | 内容 |
|---|---|
| `bepipred3_score_distribution.png` | 全表 score KDE + 直方图, AMP (legacy_mgy_2022) vs non-AMP |
| `bepipred3_score_by_length.png` | 按 peptide length 看 count / mean / Epitope % |
| `bepipred3_score_by_source.png` | 按 peptides.source 看 count / mean / Epitope % |
| `bepipred3_per_residue_stats.png` | 三种残基级分数的 KDE: avg vs max vs linear |
| `bepipred3_label_box.png` | Epitope / Non-epitope box plot |
| `bepipred3_score_ecdf.png` | 全表 ECDF + 5 个 percentile marker + 阈值线 |

## 三、核心发现

### 1. BepiPred-3.0 不能区分 AMP 与 non-AMP (预期)

`bepipred3_score_distribution.png` 中, AMP 真值集 (`legacy_mgy_2022`, n=946k)
与背景 (其他 source, n=53k) 的 score 分布几乎重合: mean 0.222 vs 0.222–0.242,
Epitope 率 88% vs 76–100%。

> 这是 BepiPred-3.0 的设计意图 —— 它是 **B 细胞线性表位预测工具**,不是
> 抗菌肽分类器。B 细胞表位 = 抗原上被抗体识别的位点,与"是不是抗菌"是
> 不同的生物学问题。

### 2. 短肽几乎全是 Epitope (阈值太松)

`bepipred3_score_by_length.png`: 长度 1–10aa 的肽 96–100% 被判为 Epitope,
30aa 长肽降到 85.5%。原因是 score 全表集中在 0.20–0.26 (单峰, 远高于 0.1512
阈值), **绝大部分都越过 threshold**。

**含义**: 若下游用 `label='Epitope'` 做"是否表位"的硬判定,基本没有信息量。
应直接用连续 `score` 列。

### 3. 三种残基级分数的关系

`bepipred3_per_residue_stats.png`:

- `average_epitope_score`: 全残基平均 → 单峰 ~0.22 (**bp3 推荐用这个**)
- `max_epitope_score`: 单残基最大 → 偏右 ~0.33 (容易假阳)
- `max_linear_epitope_score`: 7-残基 rolling window 最大 → 中间 ~0.27

**建议**: peptide-level 用 `average_epitope_score` (已经写进 `score` 列)。

### 4. PDB 序列 epitope 率最高 (符合预期)

`bepipred3_score_by_source.png`: PDB / legacy_pdb 的 Epitope 率 95–96%,
因为 PDB 序列都是已解析的抗原蛋白片段。`legacy_rfam_2022` (n=36)
是 RNA,序列 100% 判为 Epitope 但样本太少无统计意义。

## 四、Caveats

- **20.25M 全是 AMP 候选集 + UniProt 背景**, 没有独立的 AMP 测试集;因此
  没有画 ROC / PR / Cohen's d 等"分类性能"图。**BepiPred-3.0 在 AMP 任务
  上的真正效用需另算**(见 `cohens_d_all_tools.png` 跨工具对比)。
- **88% Epitope 率是 threshold 0.1512 在短肽上的副作用**;若要严格区分,
  建议重新校准 cutoff。
- **bf16 加速带来的数值误差 < 0.001**, 不影响统计图;但若要做单肽严格
  benchmark,建议用 fp32 重跑那几条。

## 五、复现命令

```bash
# 1. 导出数据(已生成, 1M 采样到 results/plots/bepipred3/bepipred3_full_sample.csv.gz)
/usr/bin/python3 scripts/analysis/export_bepipred3.py

# 2. 画图
/usr/bin/python3 scripts/analysis/plot_bepipred3.py
```