# 抗黑素方向 · 总览速览版 (antimelanin_v1)

> 一页看完：流程图、关键数字、最重要限制、行动建议。
> 详细文档：本目录下的 CSV（top10 / bottom10 / top10_bottom10）。
> 结果日期：2026-09-13（v1，WIP）。耗时：17.8 秒（warm），113 秒（cold，含 bepipred3 索引预热）。

---

## 1. 一句话

**WIP 方向**。2,025 万肽 → 全库 tipred 排序切片 → **Top 83 + Bottom 24 = 107 条 construct 落库**，
全部标 `status='WIP'` + `func_score_meaning='ranking_only_not_probability'`；
最大限制是 **唯一可用的功能信号 TIPred 99.5% 阳性无富集力** + **BLSAM-TIP（提案 §0 称的"本地重建版"）未上线生产 DB**，
本方向产出的不是"已筛出的抗黑素肽"，而是"按 TIPred 弱信号排出的待湿实验验证短名单"。

## 2. 流程图

```
2,025 万肽
  │
  │  R1 功能预筛 (TIPred 全库 P99.48/P0.52 双端切片；不预筛，提案 §1 退化)
  ▼
250 (Top 150 tipred P99+ + Bottom 100 tipred P1-)
  │
  │  R2 安全门控（4 项，可空缺性；Tipred 99.5% 阳性已为安全门控；提案 §1 共识候选池退化）
  │      · ToxinPred3 < 0.38（硬，全量）
  │      · HemoPI2    < 0.55（硬，全量；v1 升为硬门控）
  │      · MHCflurry  < 0.50（软，仅 5-15 aa）
  │      · netMHCIIpan SB = False（硬，10-30 aa，从独立表 netmhc_score）
  ▼
107 (Top 83 + Bot 24)         ← Top 67 / Bottom 76 被安全剔除
  │
  │  R3 一维 AGGRESCAN 实时算 (在 107 条 passed 上)
  ▼
107 条
  │
  │  R4 加权综合分 (4 分量 × Winsorized std 权重 × 4 递送)
  ▼
107 条 construct (Top 按 composite_topical 排名)
  │
  │  R5 落 constructs 表 (status='WIP', scenario='depigmentation')
  ▼
igem_peptides.constructs (Top 83 + Bottom 24, direction='antimelanin', status='WIP')
```

## 3. 关键数字

| 项 | 数值 |
|---|---|
| 库大小 | 20,248,885 肽 |
| 候选池 | **全库**（TIPred 99.5% 阳性无富集力，无法做功能预筛）|
| 切片 | Tipred Top 150 + Bottom 100 = 250 |
| Top 通道落库 | **83** / 150（**55.3%** 通过安全门控） |
| Bottom 通道落库 | **24** / 100（**24%** 通过安全门控）|
| Top tipred 中位数 | 0.968（P99 附近） |
| Bot tipred 中位数 | 0.063（P1 附近）|
| composite_topical 范围 | [0.166, 0.708] |
| composite_injection 范围 | [0.166, 0.708] |
| 端到端耗时 | **17.8s（warm）/ 113s（cold）** |
| 综合分权重 (cpp/sens/epitope/agg) | **0.08 / 0.39 / 0.23 / 0.31** |

**与另两方向对比的关键差异**：
- 抗菌权重：`cpp=0.18 / sens=0.26 / epitope=0.27 / agg=0.29`（beipred3 全量覆盖）
- 抗氧化权重：`cpp=0.31 / sens=0.30 / epitope=**0** / agg=0.39`（bepipred 全 NULL，权重自动归零）
- **抗黑素 cpp=0.08**：因为 Top 通道里 PLM4CPPs 命中数很低（24/107 肽 plm4cpps > 0.1），区分度小

## 4. 工具覆盖：8 个一致 / 更好，2 个关键缺失

| 工具 | 提案预期 | 实测 | 影响 |
|---|---|---|---|
| **BLSAM-TIP (LR)** | 全量（主排序） | ❌ **0 行** | **WIP 限制**：提案 §0 称"本地重建版"未上线生产 DB |
| **BLSAM-TIP (GBM)** | 全量（辅排序） | ❌ **0 行** | 同上 |
| **TIPred** | 全量（已排除） | ✅ **全 20.25M，99.48% 阳性** | **唯一可用信号**，但无富集力 |
| ToxinPred3 (硬门控) | full | ✅ full | — |
| HemoPI2 (溶血) | 500K | ✅ **full**（已升为硬门控）| **更好** |
| MHCflurry (MHC-I) | 5–15aa | ✅ 135K | 49.5% 肽未评估（与提案一致） |
| **netMHCIIpan** (MHC-II) | 14.8M | ✅ **独立表 1.475 亿行 / 132 allele / 14.8M 肽** | **更好**：从独立表接入 |
| pLM4CPPs (穿膜) | full | ✅ full | — |
| AlgPred2 (致敏) | full | ✅ full | — |
| BepiPred-3.0 (`bepipred3`) | full | ✅ full | — |
| aggrescan_a3v | 实时算 | ✅ 实时算 | — |

**关键偏差**（与 `check_score_coverage_melanin.py` 输出对齐）：
- BLSAM-TIP 双模型**完全不在 DB**——这是抗黑素方向**最重要的发现**，意味着本管线只能退化用 TIPred
- TIPred 99.48% 阳性（20,144,125 / 20,248,885），median 0.927，p95 0.943——**无富集力**

## 5. 最关键的两个限制

### 限制 1：BLSAM-TIP 双模型缺失（**比提案 §0 描述的更严重**）

- 提案 §0 称 BLSAM-TIP 是"本地重建版"——预期**应在生产 DB 里**作为主/辅排序器
- **实际**：DB 里 0 行（`check_score_coverage_melanin.py` 已确认）
- **后果**：本管线**只能退化**用 TIPred 单信号排序，而 TIPred 99.5% 阳性、median 0.927
  → Top150 实际上是从全库 99%+ 阳性的肽中按 P99+ 切片，**没有富集力**
- **建议**：
  1. 上线 BLSAM-TIP 重建版（提案 §"已知限制与升级路径" 1–2 项已建议）
  2. 在湿实验阶段用 ELISA / 体外酪氨酸酶抑制实验拿小批量标签做 Platt 缩放校准

### 限制 2：TIPred 无富集力导致落库构造数偏低（**通过率差异显著**）

- Top 通道：通过安全门控 83/150（55.3%）—— 实际**只有 67 条**被门控剔除
- Bottom 通道：通过安全门控 24/100（24%）—— **76 条**被门控剔除（76%）
- **解释**：Bottom 通道肽 tipred 0.03–0.07（最低端），**这些肽序列组成多为短片段/异常肽**，毒性/溶血评估概率天然偏高
- **后果**：Top 和 Bottom 数量差异大（83 vs 24），**不是抗黑素功能差异**，而是**安全门控对不同长度/组成的差异**
- **建议**：本管线不应解读为"Top 比 Bottom 更可能抗黑素"——这是 WIP 方向的核心限制

## 6. v1 升级要点（与提案 §0–§5 的差异）

| 项 | 提案 §0–§5 | **v1 落地** |
|---|---|---|
| 候选池 | 双模型共识 `blsam_tip_lr≥0.5 AND blsam_tip_gbm≥0.5` | **退化**：候选池=全库（TIPred 唯一弱信号）|
| 切 Top/Bottom | 在共识候选池内 | **退化**：直接 `ORDER BY tipred LIMIT 150/100`（SQL 端切片，2 秒） |
| 评估方向 | 共识优先 + GBM-only 单独留档 | **退化**：仅 TIPred 排序，无 dual-model 区分 |
| 综合分 | 与另两方向同套 | ✅ **完全相同**（plm4cpps / algpred2 / bepipred3 / aggrescan_a3v，Winsorized std 权重） |
| 落库 status | 全部 WIP | ✅ 全部 WIP |
| 安全门控 | 4 项可空缺 | ✅ **4 项可空缺**（含 netMHCIIpan SB 硬门控）|
| MHC-II | 独立表接入 | ✅ **与抗菌一致**：从 `netmhc_score` 1.475 亿行聚合 |
| 端到端耗时 | 未给预期 | **17.8s（warm）/ 113s（cold）** |

## 7. 行动建议（按优先级）

| 优先级 | 行动 | 估算时间 |
|---|---|---|
| **高** | 上线 BLSAM-TIP 重建版（提案 §附 1–2 项已建议） | 2 周 |
| **高** | 用湿实验标签做 Platt 缩放 / 等渗回归校准 BLSAM-TIP 输出 | 4 周（等数据） |
| **中** | 湿实验验证 Top 10 候选（酪氨酸酶抑制 + 黑色素合成抑制 + 溶血） | 6–8 周 |
| **中** | 评估 iMFP-LG 其它通道（ACP/ADP/AHP/AIP）作为抗黑素代理 | 3–5 天 |
| **低** | HemoPI2 已在全量（提案 §0 误估 500K；v1 实测全量） | ✅ 已达标 |
| **低** | 前端 Library 页面过滤 length < 5（如 YGGFL 太短难以构 backbone） | 0.5 天 |

## 8. 查看结果

```bash
# 总数与通道分布
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT channel, status, count(*) FROM constructs
WHERE direction='antimelanin' GROUP BY channel, status"

# 完整自检 (9 段)
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -f src/setup/sql/16_antimelanin_self_check.sql

# Top 5 速览（含 func_score_meaning）
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT c.rank, c.peptide_id, p.length,
       (c.scores->>'tipred')::float8                       AS tipred,
       (c.scores->>'func_score_meaning')                   AS func_meaning,
       (c.scores->>'netmhcipan_min_rank_pct')::float8      AS min_rank_pct,
       (c.scores->>'netmhcipan_has_sb')::bool              AS has_sb,
       (c.delivery_scores->>'topical')::float8             AS d_topical,
       (c.delivery_scores->>'injection')::float8           AS d_injection
FROM constructs c JOIN peptides p ON p.id = c.peptide_id
WHERE c.direction='antimelanin' AND c.channel='top'
ORDER BY c.rank NULLS LAST LIMIT 5"

# 工具覆盖率核对（含 BLSAM-TIP 缺失警告）
python3 scripts/pipeline/check_score_coverage_melanin.py
```

## 9. 详细文档

| 文件 | 受众 |
|---|---|
| `top10.csv` | Top 10（composite_topical 最高，含 11 项分数 + 4 种递送综合分 + func_score_meaning）|
| `bottom10.csv` | Bottom 10（composite_topical 最低，作为对照）|
| `top10_bottom10.csv` | Top 10 + Bottom 10 同一张表，方便对比 |
| `src/setup/sql/15_extend_constructs_status_wip.sql` | 把 constructs.status 的 CHECK 扩展以容纳 'WIP' |
| `src/setup/sql/16_antimelanin_self_check.sql` | 9 段自检（WIP-specific） |

## 10. 重跑命令

```bash
# 工具核对（含 BLSAM-TIP 缺失警告、netmhc_score 表状态）
python3 scripts/pipeline/check_score_coverage_melanin.py

# 扩展 constructs.status CHECK（首次运行需执行；幂等）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/15_extend_constructs_status_wip.sql

# 一键跑（warm 18 秒）
python3 scripts/pipeline/run_antimelanin.py

# 自检（9 段）
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/16_antimelanin_self_check.sql

# 导出 Top10 + Bottom10 CSV
python3 scripts/pipeline/export_antimelanin_top10.py
```

幂等：重跑只更新分数、不重复插入（`ON CONFLICT (direction, backbone_id, linker_id, peptide_id) DO UPDATE`）。

---

## 附：本方向的"可用性边界"

抗黑素方向是 iGEM 平台四个方向中**唯一未达 READY 状态**的方向。
- **READY**（抗氧化 / 抗菌）：功能分可信 + 安全门控完整 → 可直接进入湿实验阶段
- **WIP**（抗黑素）：功能分 TIPred 无富集力 + BLSAM-TIP 缺失 → **不可直接进入湿实验阶段**

界面与预计算库均标 WIP，**不与其他三个 READY 方向并列展示**。
湿实验启动条件：**BLSAM-TIP 重建并校准** 后才能转 WIP → READY（提案 §附 5）。