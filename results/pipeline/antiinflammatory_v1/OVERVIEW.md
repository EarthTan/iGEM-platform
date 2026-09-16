# 抗炎方向 · 总览速览版 (antiinflammatory_v1)

> 一页看完：流程图、关键数字、最重要限制、行动建议。
> 详细文档：`src/pipeline/docs/antiinflammatory_v1/` （三个分文件）。
> 结果日期：2026-09-13（v1，**WIP**）。耗时：40.3 秒。

---

## 1. 一句话

**WIP 方向**。2,025 万肽 → imfp_lg_AIP P90+ 候选池 → **Top 87 + Bottom 67 = 154 条 construct 落库**（`status='WIP'`，`func_score_meaning='ranking_only_not_probability'`）；
最大限制是 **AIP 三模型中两个完全不在 DB（aip_v5, aip_esm2）**——唯一可用的抗炎功能模型是 `imfp_lg_AIP`（提案 §0 已标注有训练泄漏 + 碎片盲点），本产出是"按 iMFP-LG AIP 单信号 + 跨功能去混淆启发式排出的待湿实验验证短名单"。

## 2. 流程图

```
2,025 万肽（length ∈ [1, 30]）
  │
  │  R0 长度硬约束 length ∈ [11, 30]（提案 §2；走 peptides_length_idx）
  │  R1 功能预筛（imfp_lg_AIP ≥ P90 = 0.8730，唯一可用 AIP 模型）
  ▼
2,024,889 (top 10%)
  │
  │  R2 L1→L2 级联（提案 §3）—— **退化**：
  │     aip_v5 / aip_esm2 不在 DB（check_score_coverage_aip 核对），仅用 imfp_lg_AIP 单信号
  ▼
  │
  │  R3 Top150 + Bottom100 切片（候选池内 aip_imfp 排序）
  ▼
  │
  │  R4 250 id × 12 tool 主键 enrich（imfp_lg_AIP/ACP/ADP/AHP/AMP + aopxsvm + amp-esm + tox/hemo/mhci/mhcii_pctrank + plm4cpps/algpred2/bepipred3）
  │     + netMHCIIpan 从独立表 netmhc_score 1.475 亿行接入
  ▼
  │
  │  R5 跨功能去混淆（提案 §4.2）：aip_specific = aip_imfp_rank − max(other_function_rank)
  │     实际可用跨功能通道（提案期望 8 → 实际 6）：
  │       - aopxsvm（抗氧化）✅
  │       - amp-esm（抗菌）   ✅
  │       - blsam_tip_lr/gbm（抗黑素）❌ 缺失（与 antimelanin v1 一致）
  │       - imfp_lg_ACP/ADP/AHP/AMP ✅（iMFP-LG 五通道）
  ▼
  │
  │  R6 安全门控（4 项可空缺性 + 长度冗余检查）：
  │      · ToxinPred3 < 0.38（硬，全量）
  │      · HemoPI2    < 0.55（硬，全量；v1 升为硬门控）
  │      · MHCflurry  < 0.50（软，仅 5-15 aa）
  │      · netMHCIIpan SB = False（硬，10-30 aa，从独立表 netmhc_score）
  ▼
154 (top 87 + bot 67)            ← Top 63 / Bottom 33 被安全门控剔除
  │
  │  R7 一维 AGGRESCAN 实时算 (154 条 passed)
  ▼
  │
  │  R8 加权综合分（5 分量 × Winsorized std 权重 × 4 递送）
  │      - aip_specific（主信号，跨功能去混淆后）
  │      - cpp / sens / epitope / agg（可开发性，与另三方向同套）
  ▼
  │
  │  R9 分层 Top-K（提案 §4.1）：每个长度桶各取 K，合并成 Top K
  │     注意：imfp_lg_AIP 训练集实际上限 25 aa，候选池 26-30 桶几乎不存在
  ▼
  │
  │  R10 落 constructs 表（status='WIP', scenario='inflammation'）
  ▼
igem_peptides.constructs (Top 87 + Bottom 67, direction='anti_inflammatory', status='WIP')
```

## 3. 关键数字

| 项 | 数值 |
|---|---|
| 库大小 | 20,248,885 肽 |
| 候选池（imfp_lg_AIP P90+ 且 length ∈ [11,30]） | **2,024,889**（与 antioxidant/antibacterial 一致） |
| Top 通道落库 | **87** / 150（58.0% 通过安全门控）|
| Bottom 通道落库 | **67** / 100（67% 通过安全门控）|
| Top aip_imfp 中位数 | **0.997**（贴 P99） |
| Bot aip_imfp 中位数 | **0.873**（贴 P90 cutoff）|
| aip_specific Top 中位数 | **-0.322**（中位为负，说明 Top 里多数肽更像"通用功能肽"） |
| aip_specific Bot 中位数 | **-0.358** |
| composite_topical 范围 | [0.180, 0.774] |
| composite_injection 范围 | [0.179, 0.774] |
| 端到端耗时 | **40.3 秒**（含 bepipred3 14.8s + netmhc_score 6.4s）|
| 综合分权重 (aip_specific/cpp/sens/epitope/agg) | **0.247** / 0.102 / 0.233 / 0.226 / 0.191 |
| imfp_confirm (aip_imfp ≥ 0.5) Top 命中 | **87 / 87**（Top 全员命中弱确认） |

**与另三方向对比的关键差异**：

| 方向 | 综合分主信号 | 候选池 cutoff | Top 落库率 | 状态 |
|---|---|---|---|---|
| antioxidant | aopxsvm P90 | 0.99584 | 113/150 (75.3%) | READY |
| antibacterial | amp-esm P90 | 0.4906 | 7/150 (4.7%) | READY |
| antimelanin | tipred P99+ 切片 | 全库（无富集力） | 83/150 (55.3%) | WIP |
| **antiinflammatory** | **imfp_lg_AIP P90** | **0.8730** | **87/150 (58.0%)** | **WIP** |

## 4. 工具覆盖：12 个一致 / 更好，3 个关键缺失（vs 提案 §1 假设）

| 工具 | 提案预期 | 实测 | 影响 |
|---|---|---|---|
| **aip_v5**（AIP-iFeature-LightGBM v5，L1）| 全量 | ❌ **0 行** | **WIP 限制**：L1 完全缺失，无法做 L1→L2 级联 |
| **aip_esm2**（AIP-ESM2 v8，L2 主信号）| 全量 | ❌ **0 行** | **WIP 限制**：唯一有独立 AUC 0.80-0.90 证据的模型完全不在 DB |
| **imfp_lg_AIP**（iMFP-LG AIP 通道）| 全量 | ✅ **全 20.25M（P50=0.18, P90=0.87）** | **唯一可用抗炎功能模型**；但提案 §0 标注有训练泄漏 + 碎片盲点 |
| imfp_lg_ACP/ADP/AHP/AMP | 全量（跨功能）| ✅ 全量 | 跨功能去混淆（提案 §4.2） |
| aopxsvm（抗氧化跨功能）| 全量 | ✅ 全量 | 跨功能去混淆 |
| amp-esm（抗菌跨功能）| 全量 | ✅ 全量 | 跨功能去混淆 |
| **blsam_tip_lr/gbm**（抗黑素跨功能）| 全量 | ❌ **0 行** | **WIP 限制**：抗黑素通道缺失（BLSAM-TIP 重建版未上线生产 DB，与 antimelanin v1 一致）|
| ToxinPred3（硬门控）| full | ✅ full | — |
| HemoPI2（溶血）| 500K | ✅ **full**（升为硬门控）| 更好 |
| MHCflurry（MHC-I）| 5–15aa | ✅ 135K | 99.3% 肽未评估 |
| **netMHCIIpan**（MHC-II）| 14.8M | ✅ **独立表 1.475 亿行 / 132 allele / 14.8M 肽** | 更好（v1 修复，从独立表接入）|
| pLM4CPPs（穿膜）| full | ✅ full | — |
| AlgPred2（致敏）| full | ✅ full | — |
| BepiPred-3.0（`bepipred3`）| full（提案写 bepipred）| ✅ full | 拼写差异 |
| aggrescan_a3v | 实时算 | ✅ 实时算 | — |

**关键偏差**（与 `check_score_coverage_aip.py` 输出对齐）：

1. **AIP 三模型中两个完全不在 DB**——这是抗炎方向**最重要的发现**，意味着提案 §3 的"L1→L2 级联"完全无法实现，必须退化为 imfp_lg_AIP 单信号排序。
2. **`blsam_tip_lr/gbm` 缺失**——跨功能去混淆只能用到 6 个通道（抗氧化 + 抗菌 + iMFP-LG 五通道），缺少抗黑素通道。
3. **imfp_lg_AIP 训练集实际上限 25 aa**——候选池里 26-30 桶几乎不存在（仅 1142 条 / 2M = 0.06%），26-30 桶分层 Top-K 无条可取。

## 5. 最关键的两个限制

### 限制 1：AIP 主排序模型只剩 imfp_lg_AIP（**比提案 §0 描述的更严重**）

- 提案 §0 说 iMFP-LG 是"次级信号"（权重 0.15），主信号是 aip_esm2（独立 AUC 0.80-0.90）
- **实际**：aip_v5 / aip_esm2 完全不在 DB；iMFP-LG AIP 必须升为**唯一**主排序（权重 0.247）
- **后果**：本管线排名完全依赖一个有训练泄漏的模型——`aip_specific Top 中位数 = -0.32`（Top 多数肽"抗炎特有信号"为负）就是直接证据
- **建议**：
  1. **上线 aip_v5 / aip_esm2**（提案 §附 升级路径 4 已建议）——这是把抗炎方向从 WIP 转 READY 的唯一正路
  2. 湿实验阶段用 ELISA / NF-κB 报告基因实验拿小批量标签做 Platt 缩放校准 imfp_lg_AIP 输出

### 限制 2：iMFP-LG AIP 训练集实际上限约 25 aa（候选池在 26-30 桶几乎不存在）

- imfp_lg_AIP P90+ 候选池 2,024,889 条肽的长度分布：
  - L=20-25：约 1,960,341（96.8%）
  - L=26-29：仅 1142 条（0.06%）
  - L=30：0 条
- **后果**：26-30 桶分层 Top-K 无条可取，Top 87 中 21-25 占 53 条、16-20 占 31 条、11-15 仅 3 条
- **解释**：iMFP-LG AIP 训练集最大长度约 25 aa（典型 AIP 数据集如 AIPrepo 的长度分布），模型对 >25 aa 的肽**实际未训练**
- **建议**：长度硬约束实际可收窄为 `[11, 25]`；但提案 §2 已声明 `[11, 30]`（更宽松以保留未来扩展空间），暂不调整

## 6. v1 落地要点（与提案 §1–§7 的差异）

| 项 | 提案 §1–§7 | **v1 落地** |
|---|---|---|
| L1 粗筛（L1→L2 级联） | aip_v5（LightGBM）全库 | **退化**：aip_v5 不在 DB，级联退化为 imfp_lg_AIP 单信号 |
| L2 精排 | aip_esm2（ESM2）全库 | **退化**：aip_esm2 不在 DB；用 imfp_lg_AIP 同时充当 L1+L2 |
| 跨功能去混淆通道数 | 8（抗氧化 + 抗菌 + 抗黑素 LR/GBM + iMFP-LG 五通道）| **6**（抗黑素 LR/GBM 缺失；与 antimelanin v1 一致）|
| 候选池 | imfp_lg_AIP P90+ 且 length ∈ [11,30] | ✅ **完全相同** |
| 长度硬约束 | [11,30] | ✅ **完全相同** |
| 安全门控 | 4 项可空缺（含 netMHCIIpan）| ✅ **完全相同**（netMHCIIpan 从独立表接入）|
| MHC-II | 独立表接入 | ✅ **与 antibacterial 一致**：从 `netmhc_score` 1.475 亿行聚合 |
| 跨功能去混淆公式 | `aip_specific = AIP_rank − max(other_function_rank)` | ✅ **完全相同** |
| 分层 Top-K | `(11-15)(16-20)(21-25)(26-30)` | ✅ 桶定义一致；但 26-30 桶 0 条（数据现实）|
| 落库 status | 全部 WIP | ✅ **全部 WIP** |
| `func_score_meaning` | `ranking_only_not_probability` | ✅ **完全相同** |

## 7. 行动建议（按优先级）

| 优先级 | 行动 | 估算时间 |
|---|---|---|
| **高** | **上线 aip_v5 / aip_esm2**（提案 §附 4 已建议；唯一正路）| 2 周 |
| **高** | **上线 BLSAM-TIP 重建版**（抗黑素跨功能通道补齐）| 2 周 |
| 高 | 湿实验验证 Top 10 候选（NF-κB 抑制 + 溶血 + 致敏 + 免疫）| 4-6 周 |
| 中 | 用湿实验标签做 Platt 缩放 / 等渗回归校准 imfp_lg_AIP 输出 | 4 周（等数据）|
| 中 | 评估 iMFP-LG AIP 通道作为唯一抗炎信号的校准可行性 | 3-5 天 |
| 低 | 收窄长度硬约束到 [11, 25]（与 imfp_lg_AIP 训练集对齐）| 0（参数调整）|
| 低 | VACUUM peptide_enrichment（让 bepipred3 查询从 14.8s 降到 <1s）| 0（运维）|

## 8. 查看结果

```bash
# 总数与通道分布
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -c "SELECT channel, status, count(*) FROM constructs
        WHERE direction='anti_inflammatory' GROUP BY channel, status"

# 完整自检 (10 段)
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -f src/setup/sql/17_antiinflammatory_self_check.sql

# Top 5 速览（含 aip_specific + netMHCIIpan 数据）
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT c.rank, c.peptide_id, p.length,
       (c.scores->>'aip_imfp')::float8                    AS aip_imfp,
       (c.scores->>'aip_specific')::float8                AS aip_specific,
       (c.scores->>'other_max')::float8                   AS other_max,
       (c.scores->>'netmhcipan_min_rank_pct')::float8     AS mhcii_min_rank,
       (c.scores->>'netmhcipan_has_sb')::bool             AS mhcii_has_sb,
       (c.delivery_scores->>'topical')::float8            AS d_topical,
       (c.delivery_scores->>'injection')::float8          AS d_injection
FROM constructs c JOIN peptides p ON p.id = c.peptide_id
WHERE c.direction='anti_inflammatory' AND c.channel='top'
ORDER BY c.rank NULLS LAST LIMIT 5"

# 工具覆盖率核对（含 aip_v5/aip_esm2 缺失警告 + netmhc_score 表状态）
python3 scripts/pipeline/check_score_coverage_aip.py
```

## 9. 详细文档

| 文件 | 受众 |
|---|---|
| `src/pipeline/docs/antiinflammatory_v1/01_各工具实际情况.md` | 工具核对（15 个工具逐一对照 + aip_v5/aip_esm2 缺失发现 + netmhc_score 独立表） |
| `src/pipeline/docs/antiinflammatory_v1/02_工程实现方法与结果.md` | 工程实现细节、SQL 重写、跨功能去混淆策略、可重现命令 |
| `src/pipeline/docs/antiinflammatory_v1/03_筛选流程总体说明.md` | 生物学汇报版（无代码） |
| `top10.csv` | Top 10（composite_topical 最高，含 18 项分数 + 4 种递送综合分 + func_score_meaning）|
| `bottom10.csv` | Bottom 10（composite_topical 最低，作为对照）|
| `top10_bottom10.csv` | Top 10 + Bottom 10 同一张表，方便对比 |

## 10. 重跑命令

```bash
# 工具核对（含 aip_v5/aip_esm2 缺失警告 + netmhc_score 表状态）
python3 scripts/pipeline/check_score_coverage_aip.py

# 建表（与 antioxidant/antibacterial 共用；direction 是 free varchar 32 不需扩展；
# status 已有 'WIP'；不需要新 DDL）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/12_create_constructs.sql

# 一键跑（40 秒）
python3 scripts/pipeline/run_antiinflammatory.py

# 自检（10 段）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/17_antiinflammatory_self_check.sql

# 导出 Top10 + Bottom10 CSV
python3 scripts/pipeline/export_antiinflammatory_top10.py
```

幂等：重跑只更新 scores / delivery_scores / rank，不会改变行数。

---

## 附：本方向的"可用性边界"

抗炎方向是 iGEM 平台四个方向中**与 antimelanin 并列 WIP**的方向。
- **READY**（antioxidant / antibacterial）：功能分可信 + 安全门控完整 → 可直接进入湿实验阶段
- **WIP**（antimelanin / antiinflammatory）：功能分有缺陷 → **不可直接进入湿实验阶段**

界面与预计算库均标 WIP，**不与两个 READY 方向并列展示**。
湿实验启动条件：**aip_v5 / aip_esm2 上线 + imfp_lg_AIP 用湿实验标签做 Platt 缩放校准** 后才能转 WIP → READY（提案 §8 升级路径 1-3）。
