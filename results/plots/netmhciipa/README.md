# NetMHCIIpa — 2000 万肽库运行结果统计可视化

NetMHCIIpan 4.x (本仓库以 `netmhciipa` 表示) 在 20M 肽库上对 131 个 HLA-II 等位基因做的 MHC-II 结合预测结果,落到 `netmhc_score` 表,共 **1.475 亿条 binder 行**。本目录是该数据集的统计学可视化。

## 输入

- **数据源**: `igem_peptides.netmhc_score` 表
- **总 binder 行数**: 147,540,791 (1.475 × 10⁸)
- **不同 peptide 数**: ~14,804,406
- **覆盖等位基因**: 131 个,跨 4 个 HLA-II 家族
  - DRB1 × 49
  - DRB3/4/5 × 8
  - HLA-DPA1-DPB1 × 49
  - HLA-DQA1-DQB1 × 25
- **bind_level 分布**:
  - Strong Binder (SB, rank ≤ 2%): 8,139,352 (5.52%)
  - Weak Binder (WB, rank ≤ 10%): 139,401,439 (94.48%)
- **肽长度范围**: 10–30 aa(主峰 20–30 aa)
- **binding core 长度**: 全部 9 aa(MHC II canonical)

## 输出图表

| 文件 | 内容 |
|------|------|
| `netmhciipa_label_distribution.png` | 全局 SB/WB 饼图 + meta 文本块 + **4 家族分面** per-allele SB% 横向条形(Top-25 + 其它摘要),含全局 SB% 虚线参考 |
| `netmhciipa_rank_by_allele.png` | 131 个 allele 的 `rank_pct` violin,**4 家族分面**,按家族内中位数排序;含 SB=2% / WB=10% 阈值参考线 |
| `netmhciipa_rank_ecdf.png` | 全局 + 各家族 `%Rank` ECDF(200 桶服务器端直方图累加);带 2% / 10% 经典阈值线 |
| `netmhciipa_score_vs_rank.png` | `score` vs `%Rank` hexbin(200k 随机抽样,log-count 着色);标注 Pearson / Spearman 相关系数 |
| `netmhciipa_length_vs_rank.png` | 上:peptide 长度 × median/mean %Rank;下:SB/WB 堆叠条 + 每长度行数标注 |
| `netmhciipa_overview.png` | 2×3 dashboard 缩略图,末格放数据规模文本摘要 |

## 关键统计 (写入 `netmhciipa_summary.json` / `.csv`)

```
n_total          : 147,540,791
n_sb             :   8,139,352   ( 5.52%)
n_wb             : 139,401,439   (94.48%)
n_alleles        : 131
SB cutoff        : %Rank ≤ 2%
WB cutoff        : %Rank ≤ 10%
score vs %Rank   : Pearson r = -0.79, Spearman ρ = -0.77   (200k 随机抽样)
```

`netmhciipa_summary.csv` 包含每个 allele 完整一行:`allele, family, n, n_sb, n_wb, sb_pct, mean_rank, median_rank, mean_score` — 共 131 行,可直接导入 Excel / pandas 做下游分析。

## 关键发现

1. **强全局一致性**: `score` 与 `%Rank` 单调反相关(Pearson -0.79,Spearman -0.77),符合 NetMHCIIpan 的 log-likelihood 分数应与 rank 严格反向的设计;hexbin 中可见两个分支,对应不同 peptide 长度的拟合曲线。
2. **肽长度最优区间 14–18 aa**: 该区间 `rank_pct` 中位数最低(≈ 5.1),结合预测最强;短肽(10–12 aa)受 core 9-aa 模板外侧残基少而预测弱,长肽(20+ aa)预测趋于平稳但稍弱。
3. **家族差异显著**:
   - **DRB3/4/5** 的 ECDF 始终高于其它家族(同 %Rank 下累计比例更大,意味着更多命中较弱结合者),SB 比例最高;
   - **HLA-DP** 和 **HLA-DQ** 的 ECDF 略低于全局(同 %Rank 下更少命中),SB 比例最低 — 与 DP/DQ 在人群中更严格的肽结合口袋特征一致;
   - **DRB1** 紧贴全局曲线,因样本量最大(88.8M)。
4. **131 个 allele 中 SB% 跨度**: 全局 5.52%,top-allele 接近 8–9%,最低 1–2%(见 `label_distribution` 各家族面板)。
5. **core 9-mer 模式稳定**: `LENGTH(core)` 在 1.47 亿行中恒为 9 — 没有异常短 / 长 binding core,数据质量自洽。

## 复现

```bash
/usr/bin/python3 scripts/analysis/plot_netmhciipa.py
```

**数据流**: 脚本直接连 `igem_peptides.netmhc_score` 表,所有 1.47 亿行聚合都在 SQL 端做(每 allele 一行 + 全局 / 家族直方图共 5 次查询),仅下拉 ~855k 抽样行做 violin / hexbin。

**运行环境**:
- 依赖:`matplotlib`, `numpy`, `psycopg2`(系统 `python3` 已自带,无需 venv)
- DB 连接:`host=127.0.0.1 dbname=igem_peptides user=igem`(写在脚本顶部 `DB_DSN`)
- 输出目录:`results/plots/netmhciipa/`
- 单次运行耗时:~5 分钟(主要是 5 次全表聚合 + 655k 行抽样)

**注意**: 131 个 allele 的 violin 是按"每个 allele 抽 5k 行"算的,如需更精细可改脚本中的 `n_per_allele`(默认 5000)。
