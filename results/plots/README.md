# results/plots/ — 2000 万肽库统计学图表

2020 万肽 enrichment 完成后,所有统计学图表 / 抽样数据落盘到这里。
**目录结构按"工具 + 全局"分类**,所以你打开一个工具时只需要看对应子文件夹。

## 顶层(全局 / 跨工具)

| 文件 / 类型 | 内容 |
|---|---|
| `score_distributions_grid.png` | 9 个工具 3x3 grid 主分布图 |
| `cohens_d_all_tools.png`       | 跨工具 Cohen's d 横向条形 |
| `cohen_d_table.csv`            | 跨工具 Cohen's d 表(每个工具一行) |
| `score_summary.csv`            | 各工具 score 统计摘要(n / mean / median / std / p1 / p99 / min / max) |
| `score_samples.npz`            | 各工具 500k 随机抽样 score(numpy .npz, key=tool 名) |

## `library/`
肽库本身(不依赖任何工具)的元信息。

| 文件 | 内容 |
|---|---|
| `length_distribution.csv`      | 3..30 aa 长度 × 行数 × 累计% |
| `length_distribution.png`      | bar + 累计比例 双轴 |
| `length_distribution_log.png`  | 同上,y 轴 log |

## `<tool>/`(每个工具一个)
每个工具的**深度分析 + 单工具单分布图**。
子目录命名 = 工具名;同一工具的所有输出(包括 `plot_score_distributions.py` 生成的 `score_<tool>.png`)都集中在这里。

| 子目录 | 主要内容 |
|---|---|
| `algpred2/`     | `score_algpred2.png` (其他深度分析见 README, 如有) |
| `anoxpepred/`   | `score_anoxpepred.png` |
| `hemopi2/`      | `score_hemopi2.png` |
| `sodope/`       | `score_sodope.png` |
| `toxinpred3/`   | `score_toxinpred3.png` |
| `amp_esm/`      | `score_amp_esm.png` + 深度分析(子模型 / logit / transforms_kde / samples.npz / summary) |
| `mhcflurry/`    | 单 allele 的 5 张图 + summary + allele_audit.txt |
| `mhcflurry_multi/` | 多 allele 聚合分析(`best / n_bound / consensus` 等口径) |
| `netmhciipa/`   | NetMHCIIpa 深度分析(1.475 亿 binder 行,131 个 HLA-II 等位基因,6 张图 + summary) |
| `plm4cpps/`     | `score_plm4cpps.png` + 全量 CPP 分数 + 按长度聚合 |
| `temstapro/`    | `score_temstapro.png` + 6 温度 raw 全表统计 + score 定义 md |
| `tipred/`       | `score_tipred.png` + 全量 non-TIP 分数 + 按长度聚合 + 抽样 |
| `aopxsvm/`      | AOPxSVM 深度分析(pos/neg 抽样 + 全表 gz + calibration) |
| `bepipred3/`    | BepiPred3.0 深度分析(per-residue / by source / by length) |

## 复现

每个工具的图表都可以由 `scripts/analysis/` 或 `analysis/<tool>/` 下的脚本重新生成:

```bash
# 全局(每个工具一张 score_*.png 写到 results/plots/<tool>/,跨工具 grid 写在根)
/tmp/plot_venv/bin/python scripts/analysis/plot_score_distributions.py

# 全局(cohens_d_all_tools.png + cohen_d_table.csv 写在根)
/tmp/plot_venv/bin/python scripts/analysis/cohens_d_all_tools.py

# 库长度分布 (写到 results/plots/library/)
/tmp/plot_venv/bin/python scripts/analysis/plot_length_distribution.py

# 单工具深度(各自默认 --out-dir 已指向 results/plots/<tool>/)
/tmp/plot_venv/bin/python scripts/analysis/plot_amp_esm.py
/tmp/plot_venv/bin/python scripts/analysis/plot_mhcflurry.py
/tmp/plot_venv/bin/python scripts/analysis/plot_mhcflurry_multi.py
/tmp/plot_venv/bin/python scripts/analysis/extract_temstapro_full.py

# NetMHCIIpa(MHC II 打分器,数据源: netmhc_score 表,~1.475 亿行,131 个 HLA-II allele)
/usr/bin/python3 scripts/analysis/plot_netmhciipa.py

# pLM4CPPs / TIPred 的 9 张统计图(写到 analysis/<tool>/)
/tmp/plot_venv/bin/python analysis/plm4cpps/plot_plm4cpps.py
/tmp/plot_venv/bin/python analysis/tipred/plot_tipred.py
```

## 历史 / 命名约定

- 所有文件名都带**工具名前缀**(如 `amp_esm_score.png`),便于在统一目录下排序查找
- 单分布图(`score_<tool>.png`)保留是因为它是"该工具能跑出来的最朴素直方图"
- 深度分析的命名遵循 `<tool>_<figure_topic>.png`