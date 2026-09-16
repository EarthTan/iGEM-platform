# 抗菌方向 · 总览速览版 (antibacterial_v1)

> 一页看完：流程图、关键数字、最重要限制、行动建议。
> 详细文档：`src/pipeline/docs/antibacterial_v1/` （三个分文件）。
> 结果日期：2026-09-12（v1，含 netMHCIIpan 接入）。耗时：31 秒。

---

## 1. 一句话

2,025 万肽 → 4 轮筛选 → **Top 7 + Bottom 58 = 65 条 construct 落库**，全部通过 4 项安全一票否决（**含 netMHCIIpan SB 门控**）；最大限制是 **AMPlify 偏见严重**（Top 250 高分肽中 47% 有毒），最终 Top 仅 7 条通过。

## 2. 流程图

```
2,025 万肽
  │
  │  R1 功能预筛 (amp-esm ≥ P90 = 0.4906)
  ▼
2,024,889 (top 10%)
  │
  │  R2 安全一票否决（4 项，可空缺）
  │      · ToxinPred3 < 0.38（硬，全量）
  │      · HemoPI2    < 0.55（硬，全量）
  │      · MHCflurry  < 0.50（软，仅 5-15 aa）
  │      · netMHCIIpan SB = False（硬，10-30 aa，从独立表 netmhc_score）
  ▼
65 (top 7 + bot 58)         ← Top 143 / Bottom 42 被安全剔除
  │
  │  R3 加权综合分 (4 分量 × Winsorized std 权重 × 4 递送)
  ▼
65 条 construct
  │
  │  R4 落 constructs 表
  ▼
igem_peptides.constructs (Top 7 + Bottom 58, direction='antibacterial')
```

## 3. 关键数字

| 项 | 数值 |
|---|---|
| 库大小 | 20,248,885 肽 |
| 候选池 | 2,024,889 肽（全库 P90+） |
| Top 通道落库 | **7** / 150（4.7%）|
| Bottom 通道落库 | **58** / 100（58%）|
| Top amp_esm 中位数 | 0.861 |
| Bot amp_esm 中位数 | 0.491 |
| composite_topical 范围 | [0.228, 0.572] |
| composite_injection 范围 | [0.228, 0.521] |
| 端到端耗时 | **31 秒**（含 netmhc_score 独立查 5.2s）|
| 综合分权重 (cpp/sens/epitope/agg) | 0.18 / 0.26 / **0.27** / 0.29 |

**`epitope = 0.27` 是因为 BepiPred-3.0（tool 名 `bepipred3`）在 DB 里全 20.25M 覆盖**——与抗氧化方向的 `epitope = 0` 形成对比。

## 4. 工具覆盖：8 个一致 / 更好，1 个长表缺失但独立表覆盖

| 工具 | 提案预期 | 实测 | 影响 |
|---|---|---|---|
| AMPlify (主排序, `amp-esm`) | TSV 复用（覆盖待确认） | ✅ **全 20.25M** | **更好**：覆盖比预期高 |
| ToxinPred3 (硬门控) | full | ✅ full | — |
| HemoPI2 (溶血) | 500K | ✅ **full**（已升为硬门控）| **更好**：覆盖度从 2.5% 升到 100% |
| MHCflurry (MHC-I) | 5–15aa | ✅ 135K | 99.3% 肽未评估 |
| **netMHCIIpan** (MHC-II) | 14.8M | ✅ **14.8M 肽·132 allele（独立表 `netmhc_score` 1.475 亿行）** | **v1 修复**：从独立表接入 |
| pLM4CPPs (穿膜) | full | ✅ full | — |
| AlgPred2 (致敏) | full | ✅ full | — |
| BepiPred-3.0 (`bepipred3`) | full | ✅ full（tool 名拼写差异）| B 细胞表位不再是空 |
| iMFP-LG AMP | （提案 §2 建议） | ✅ **full**（tool 名大写） | 全量交叉确认 |
| **aggrescan_a3v** | 实时算 | ✅ 实时算 | — |

## 5. 最关键的两个限制

### 限制 1：AMPlify 偏见严重（**比提案 §2 描述的更严重**）

- AMPlify Top 250 高分肽中：
  - 47% 有毒（ToxinPred3 ≥ 0.38）
  - 80% 溶血（HemoPI2 ≥ 0.55）
  - 仅 7% 通过安全一票否决
- **原因**：AMPlify 偏袒阳离子两亲性短肽，而这类肽本身就有膜破坏毒性
- **建议**：下一步上 **AMPlify-ESM2 平衡版**（提案 §2 末尾已建议），或考虑 **iMFP-LG AMP 作为主排序**（13.7% 有毒，远低于 AMPlify 的 47%）

### 限制 2：MHC-I 受长度限制（与提案一致）

- MHCflurry 仅覆盖 5-15 aa 的肽（0.7%）
- Top 7 条肽长度 27-29 aa，全部不在 MHCflurry 范围 → 注射下 MHC-I 收紧对所有候选不生效
- **但 v1 修复了 MHC-II**（10-30 aa），所以免疫评估**对长肽不全是盲区**了
- **建议**：所有注射候选在湿实验阶段**必须**做 T 细胞增殖实验（MHC-II 体外）

## 6. v1 升级要点（与 v0 的关键差异）

| 项 | v0 | **v1** |
|---|---|---|
| netMHCIIpan | 软信号、全 NULL | **从独立表 `netmhc_score` 接入（14.8M 肽·132 allele）** |
| `assessed_mhcii` | 0 / 76 | **65 / 65** |
| 安全门控 | 3 项 | **4 项**（+ netMHCIIpan SB）|
| Bottom 通道淘汰 | 31% | **42%**（净MHCIIpan SB 补剔 11 条）|
| 落库总数 | 76 | **65** |
| 端到端耗时 | 86s | **31s**（候选池从 63s 降到 8.8s）|

## 7. 行动建议（按优先级）

| 优先级 | 行动 | 估算时间 |
|---|---|---|
| **高** | 上 AMPlify-ESM2 平衡版 | 1 周 |
| 中 | 评估 iMFP-LG AMP 作为主排序 | 3-5 天 |
| 中 | 湿实验验证 Top 7 候选（溶血 + MIC + 免疫） | 4-6 周 |
| 低 | 前端 Library 页面过滤 length < 5 | 0.5 天 |
| 低 | VACUUM peptide_enrichment（让 bepipred3 查询从 14.7s 降到 <1s） | 0（运维） |

## 8. 查看结果

```bash
# 总数与通道分布
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -c "SELECT channel, status, count(*) FROM constructs
        WHERE direction='antibacterial' GROUP BY channel, status"

# 完整自检 (6 段)
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -f src/setup/sql/14_antibacterial_self_check.sql

# Top 5 速览（含 netMHCIIpan 数据）
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT c.rank, c.peptide_id, p.length,
       (c.scores->>'amp_esm')::float8                              AS amp_esm,
       (c.scores->>'netmhcipan_min_rank_pct')::float8              AS min_rank_pct,
       (c.scores->>'netmhcipan_has_sb')::bool                      AS has_sb,
       (c.scores->>'netmhcipan_n_alleles')::int                    AS n_alleles,
       (c.delivery_scores->>'topical')::float8                     AS d_topical,
       (c.delivery_scores->>'injection')::float8                   AS d_injection
FROM constructs c JOIN peptides p ON p.id = c.peptide_id
WHERE c.direction='antibacterial' AND c.channel='top'
ORDER BY c.rank NULLS LAST LIMIT 5"

# 工具覆盖率核对（含 netmhc_score 表）
python3 scripts/pipeline/check_score_coverage_amp.py
```

## 9. 详细文档

| 文件 | 受众 |
|---|---|
| `src/pipeline/docs/antibacterial_v1/01_各工具实际情况.md` | 工具核对（10 个工具逐一对照 + netmhc_score 独立表） |
| `src/pipeline/docs/antibacterial_v1/02_工程实现方法与结果.md` | 工程实现细节、SQL 重写、netmhc_score 接入策略、可重现命令 |
| `src/pipeline/docs/antibacterial_v1/03_筛选流程总体说明.md` | 生物学汇报版（无代码） |
| `top10.csv` | Top 7（功能分最高，含 11 项分数 + 4 种递送综合分 + 4 项 netMHCIIpan 数据）|
| `bottom10.csv` | Bottom 10（功能分最低，作为对照）|
| `top10_bottom10.csv` | Top 7 + Bottom 10 同一张表，方便对比 |

## 10. 重跑命令

```bash
# 工具核对（验证 DB 状态；含 netmhc_score 表的预估行数与抽样）
python3 scripts/pipeline/check_score_coverage_amp.py

# 建表（与 antioxidant 共用）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/12_create_constructs.sql

# 一键跑（31 秒）
python3 scripts/pipeline/run_antibacterial.py

# 自检（6 段）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/14_antibacterial_self_check.sql

# 导出 Top10 + Bottom10 CSV
python3 scripts/pipeline/export_antibacterial_top10.py
```

幂等：重跑只更新分数、不重复插入。