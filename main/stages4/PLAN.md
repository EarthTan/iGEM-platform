# Stages4 Pipeline Plan：层级筛选 + 安全优先

> 版本: v2.0 — 2026-05-18
> 数据源: UniProt (225M) + MGnify (624M)
> 入口候选: ~19.9M (由 stages3 Round 0 预处理完成)
> 目标: 19.9M → Top 100 + Bottom 100

---

## 1. 背景：stages2 的两个致命问题

### 问题 1：加权平均稀释了关键安全属性

Stages2 使用加权平均的方式混合抗氧化性、溶血性、致敏性、MHC 结合等不同维度的属性。

```
AnOxPePred(0.45) + ToxinPred3(0.13) + HemoPI2(0.09) + MHCflurry(0.05) + ...
```

在这个公式中，HemoPI2 的权重仅为 0.05–0.09，这意味着即使一个肽溶血性极差（高溶血风险），只要抗氧化得分足够高，综合分仍然可能排在前列。**安全属性变成了可以被"补偿"的评分项，而不是一票否决的硬条件。**

**正确的做法**：安全属性应该是硬阈值（达标 / 淘汰），不参与加权混合。

### 问题 2：双通道仍被综合分数中的抗氧化主导

虽然双通道的初始分选使用了纯 AnOxPePred（正确），但在后续 Round 3–7 中仍然使用包含 AnOxPePred 权重的综合分进行排名和筛选。这导致：

- Bottom 通道（抗氧化最差的肽）在后续轮次中还是被 AnOxPePred 的残余权重影响
- 安全筛选并没有真正独立于抗氧化信号

**正确的做法**：抗氧化只在双通道分选时使用一次，后续轮次不再参与任何排名。

---

## 2. stages4 核心原则

### 原则 A：层级筛选，绝不跨属性加权平均

每个轮次使用单一筛选标准——或排序取百分比，或硬阈值淘汰。同一轮次绝不对不同属性做加权平均混合。

### 原则 B：安全属性使用硬阈值

毒性、溶血性、致敏性等安全相关属性，只设通过/淘汰阈值，不参与任何评分混合。

### 原则 C：抗氧化只用一次

AnOxPePred 仅在 Round 1 的双通道分选时使用。一旦通道确定，后续所有轮次不再使用抗氧化分数。

### 原则 D：可变权重机制（SD 驱动 + 手动调节）

加权平均仅在 Round 3（深度评分）中使用，采用两层权重机制：

- **基础权重**：SD 驱动，`w_base = σ_i / Σσ_j`，区分度高的属性自动获得高权重
- **手动调节**：在 SD 权重基础上乘以手动系数 `α`，允许领域知识介入

```
w_final_i = w_base_i × α_i         (再归一化使 Σw = 1)
```

默认所有属性 `α = 1.0`（纯 SD 驱动）。手动系数必须在 `weight_config` 表中显式记录，附带调节理由。

**AnOxPePred 的特殊处理**：虽然在 Round 1 中已作为分选标准使用过，但如果希望在 Round 3 中让抗氧化信号参与深度评分，可以通过手动系数 `α > 1.0` 让它在加权中占更高权重（例如 α = 1.3）。这样做的前提是：分选通道已经确定，Round 3 的抗氧化评分是在通道内部对候选做区分，不影响跨通道比较。

| 属性组 | 处理方式 | 位置 |
|--------|---------|------|
| 抗氧化 | Round 1 分选使用；Round 3 可选参与加权（默认参与，α=1.0） | Round 1, Round 3 |
| 安全（毒性/溶血/致敏/免疫） | 硬阈值通过/淘汰 | Round 1–2 |
| 功能（B细胞表位/热稳定/溶解度/细胞穿透） | SD 加权综合分 | Round 3 |
| 3D 结构（SASA/Aggrescan3D/pLDDT） | SD 加权或独立排名 | Round 6–7 |

### 原则 E：从轻到重，逐级收紧

```
Round 1 (轻, ~5min/百万) → 排序取前百分之几
Round 2 (轻, 安全阈值)   → 硬淘汰
Round 3 (重, GPU~min/百) → SD 加权 → 取前百分之几
Round 4 (轻, 枚举)       → 排序取前百分之几
Round 5 (最重, GPU~min/条) → 全部通过
Round 6 (轻, CPU秒/条)    → 全部评分
Round 7 (纯本地)          → 最终排名
```

---

## 3. 八轮漏斗设计

### Round 0：数据预处理

**复用 stages3 Round 0 的结果**，不做算法变更。

| 步骤 | 操作 | 数据量 |
|------|------|--------|
| 长度筛选 | 3–30 aa | 849M → ~5% |
| AA 过滤 | 仅 20 种标准氨基酸 | +去重 |
| 写入 DB | DuckDB `candidates` 表 | 19,890,021 |

**预期耗时**：~99 min

---

### Round 1：抗氧化单指标分选

**核心变更**：这是抗氧化分数第一次被使用。如果选择让 AnOxPePred 参与 Round 3 加权，则此处是双通道分选的唯一依据，而非唯一使用位置。

| 步骤 | 操作 | 依据 | 预计通过数 |
|------|------|------|-----------|
| 1a | 对所有 19.9M 候选运行 AnOxPePred | 抗氧化活性预测 | 19.9M 全量评分 |
| 1b | 运行 AlgPred2 | 致敏性硬阈值 ≥0.30 → 淘汰 | 淘汰 ~15% |
| 1c | 通过 AlgPred2 的按 AnOxPePred 排序 | 纯抗氧化分，无混合 | — |
| 1d | 取 Top 10% → Top 通道 | 抗氧化最好的 10% | ~1.7M |
| 1e | 取 Bottom 1% → Bottom 通道 | 抗氧化最差的 1%（阴性对照） | ~170K |

**参与服务**：AnOxPePred (GPU), AlgPred2 (CPU)

**输出表**：`round1_scores`, `round1_channels`

---

### Round 2：安全筛检（纯硬阈值，无加权）

**核心变更**：安全属性不参与任何混合，每个属性独立淘汰。

| 步骤 | 操作 | 淘汰标准 |
|------|------|---------|
| 2a | 运行 ToxinPred3 | ≥0.38 → 淘汰（剧毒风险） |
| 2b | 运行 HemoPI2 | ≥0.55 → 淘汰（溶血风险） |
| 2c | 运行 MHCflurry | ≥0.5 → 淘汰（免疫原性风险） |
| 2d | 合并淘汰列表 | 任一安全项不通过即淘汰 |
| 2e | 幸存者进入 Round 3 | 所有安全项均通过 |

**参与服务**：ToxinPred3 (CPU), HemoPI2 (CPU), MHCflurry (GPU)

**设计理由**：
- 安全属性是"一票否决"的，不是"可补偿"的
- 不需要综合分，不需要排名，只需要通过/淘汰
- 三个服务可并发运行，互不依赖

**输出表**：`round2_scores`, `round2_passed`, `round2_excluded`

---

### Round 3：深度评分 + 可变权重（唯一加权位置）

**核心变更**：全流程唯一使用加权平均的位置。采用 SD 驱动 + 手动调节的双层权重机制。

| 步骤 | 操作 | 详情 |
|------|------|------|
| 3a | 运行 BepiPred3 | B 细胞表位预测 (GPU) |
| 3b | 运行 TemStaPro | 热稳定性预测 (GPU) |
| 3c | 运行 SoDoPE | 溶解度预测 (CPU) |
| 3d | 运行 pLM4CPPs | 细胞穿透预测 (GPU) |
| 3e | 运行 GraphCPP | 细胞穿透 GNN (GPU) |
| 3f | （可选）运行 AnOxPePred | 如果决定让抗氧化参与加权 |
| 3g | 计算每个服务的 winsorized SD | 截尾 1% 极端值 |
| 3h | 基础权重 | w_base_i = σ_i / Σσ_j |
| 3i | 手动调节 | w_final_i = w_base_i × α_i，再归一化 |
| 3j | 计算综合分 | Σ(w_final_i × score_i_normalized) |
| 3k | 排序取 Top N% | 例如 Top 5% |

**权重公式**：

```
// 基础：SD 驱动
for each service s in participating_services:
    σ_s = winsorized_stddev(scores_s)
    w_base_s = σ_s / Σσ_all

// 手动调节
for each service s:
    w_final_s = w_base_s × α_s       // α >= 0, default 1.0

// 再归一化
Σw_final = Σ(w_final_s)
w_norm_s = w_final_s / Σw_final
```

**手动系数记录格式**：

| 服务 | α | 理由 |
|------|---|------|
| anoxpepred | 1.0 | 默认（不额外抬高） |
| bepipred3 | 1.0 | 默认 |
| temstapro | 1.0 | 默认 |
| ... | 1.0 | 默认 |

如果某天决定"把 AnOxPePred 权重抬高到 1.3 倍"，只需改一个数字，并且所有历史记录可追溯。

**输出表**：`round3_scores`, `round3_ranking`, `score_distribution`, `weight_config`

---

### Round 4：Construct 枚举 + 属性评分

| 步骤 | 操作 |
|------|------|
| 4a | 取 Round 3 Top N 肽（Top 通道）+ Bottom M 肽（Bottom 通道） |
| 4b | 枚举 3 个融合位置（N 端 / C 端 / 两端） |
| 4c | 枚举 2 种 Linker（Flex_GGGGSx1 / Flex_GGGGSx2） |
| 4d | 组装全序列（骨架 ~346aa + Linker + 肽） |
| 4e | 运行 SoDoPE（全长 construct 溶解度） |
| 4f | 运行 TemStaPro（全长 construct 热稳定性） |
| 4g | 取 Top N 进入 Round 5 |

**注意**：此轮次不涉及加权平均。SoDoPE 和 TemStaPro 分别记录。

**输出表**：`constructs`, `construct_scores`

---

### Round 5：3D 结构预测

| 步骤 | 操作 |
|------|------|
| 5a | OmegaFold 推理（所有 construct） |
| 5b | 提取 pLDDT 置信度 |
| 5c | PDB 文件按 construct 命名存储 |

**参与服务**：OmegaFold (GPU, 串行)

**输出表**：`structure_results`, PDB 文件

---

### Round 6：PDB 评估

| 步骤 | 操作 |
|------|------|
| 6a | SASA 计算（肽段表面暴露度，越高越好） |
| 6b | Aggrescan3D 计算（聚集风险，越低越好） |

**参与服务**：SASA (CPU), Aggrescan3D (CPU)

**注意**：不做加权混合。SASA 和 Aggrescan3D 分别记录。

**输出表**：`pdb_eval`

---

### Round 7：最终排名输出

| 步骤 | 操作 |
|------|------|
| 7a | Top 通道独立排名（基于 Round 6 的 3D 指标） |
| 7b | Bottom 通道独立排名 |
| 7c | 生成最终输出包（CSV + 报告） |

**排名方案**：对 SASA, (1-Aggrescan3D), pLDDT 三个 3D 指标进行 SD 加权排名。也可以直接按 SASA 排序（参考 stages2 经验：SASA 区分度最好）。

**输出**：
- `top100.csv` — Top 通道排名
- `bottom100.csv` — Bottom 通道排名
- `score_distribution.json` — 最终分数分布
- 每个 construct 独立文件夹（含 FASTA, PDB, scores）

---

## 4. 文件系统规范

### 命名规则

统一使用 `s4_roundXX_name.py` 格式：

```
main/stages4/
├── PLAN.md                        # ← 本文档
├── __init__.py
│
├── s4_round00_preprocess.py       # 数据预处理（可跳过）
├── s4_round01_antioxidant_split.py # 抗氧化单指标分选
├── s4_round02_safety_screen.py     # 安全筛检（硬阈值）
├── s4_round03_deep_scoring.py      # 深度评分 + 可变权重
├── s4_round04_enumerate.py         # Construct 枚举
├── s4_round05_3d.py                # 3D 结构预测
├── s4_round06_pdb_eval.py          # PDB 评估
├── s4_round07_final.py             # 最终排名
│
├── s4_db.py                        # DuckDB 数据库接口
├── s4_docker_utils.py              # Docker 按需启动
├── s4_service_map.py               # 服务依赖映射
├── s4_analytics.py                 # 方差感知权重引擎（含手动调节）
│
├── plan/                           # 规划文档存储
│
└── __pycache__/
```

### 与 stages2 命名对比

| 含义 | stages2 | stages4 |
|------|---------|---------|
| 数据预处理 | `step00_integrate.py` | `s4_round00_preprocess.py` |
| 轻量评分 | `round01_lightweight.py` | `s4_round01_antioxidant_split.py` |
| 安全筛选 | （混在 round02–03） | `s4_round02_safety_screen.py` |
| 深度评分 | `round03_heavy.py` | `s4_round03_deep_scoring.py` |
| Construct | `round04_enumerate.py` | `s4_round04_enumerate.py` |
| 3D 结构 | `round05_3d.py` | `s4_round05_3d.py` |
| PDB 评估 | `round06_pdb_eval.py` | `s4_round06_pdb_eval.py` |
| 最终排名 | `round07_final.py` | `s4_round07_final.py` |

### 输出目录结构

```
output4/
├── STATUS.md
├── pipeline.db                    # DuckDB 数据库
├── reports/                       # 各轮次报告
│   ├── round0_report.md
│   ├── round1_report.md
│   └── ...
├── pdb/                           # 3D 结构文件
│   └── con_XXXX/
│       ├── construct.fasta
│       ├── omegafold.pdb
│       └── scores.json
├── logs/                          # 运行日志
└── final/                         # 最终输出
    ├── top100.csv
    ├── bottom100.csv
    ├── weight_config.json          # 权重配置 + 手动调节记录
    ├── score_distribution.json
    └── constructs/                # 每个 construct 独立文件夹
```

---

## 5. 数据库设计

### DuckDB 说明

Stages4 使用 DuckDB 管理状态。虽然数据量（千万级）远小于 stages3（十亿级），使用数据库的优势仍成立：
- 统一的 checkpoint/断点续跑机制
- 查询灵活（SQL 比 CSV 接力的文件操作更可靠）
- stages3 的 `db.py` 可以大幅复用

### 表清单

| 表 | 用途 | 写入轮次 | 预计行数 |
|----|------|---------|---------|
| `candidates` | 候选肽段 | Round 0 | 19.9M |
| `round1_scores` | AnOxPePred + AlgPred2 | Round 1 | 19.9M |
| `round1_channels` | 双通道归属（Top/Bottom） | Round 1 | ~1.87M |
| `round2_scores` | 安全服务评分 | Round 2 | ~1.87M |
| `round2_passed` | 安全阈值通过者 | Round 2 | ~1.5M |
| `round3_scores` | 深度服务评分 | Round 3 | ~1.5M |
| `round3_ranking` | SD 加权排名 | Round 3 | ~1.5M |
| `constructs` | 构造枚举 | Round 4 | ~10K–50K |
| `construct_scores` | 构造级评分 | Round 4 | ~10K–50K |
| `structure_results` | 3D 结构结果 | Round 5 | ~500–5K |
| `pdb_eval` | PDB 评估结果 | Round 6 | ~500–5K |
| `final_ranking` | 最终排名 | Round 7 | ~200 |
| `score_distribution` | 分数分布统计 | Round 3, 7 | ~20 |
| `weight_config` | 权重配置（含 SD 权重 + 手动系数 α） | Round 3, 7 | ~10 |
| `checkpoint` | 检查点 | All | ~20 |

### 与 stages2 的差异

| 维度 | stages2 | stages4 |
|------|---------|---------|
| 状态管理 | CSV 文件接力 | **DuckDB 统一管理** |
| Round 1 输出 | `all_scored.csv`（全量，无通道标签） | `round1_channels`（含双通道归属） |
| Round 2 安全处理 | 低权重混入综合分 | **独立硬阈值淘汰，单独表** |
| 加权位置 | Round 2 + Round 3 + Round 6 | **仅 Round 3** |
| 权重来源 | 人工固定 | **SD 驱动 + 手动系数 α** |
| 抗氧化使用次数 | 多个轮次隐含使用 | **Round 1 分选；Round 3 可选参与** |

---

## 6. 数据量估算

### 筛选漏斗

```
19,890,021  candidates
    │ Round 1: AnOxPePred 排序取 Top 10%
    ▼
~1,990,000  Top 通道 (+ ~200,000 Bottom 通道)
    │ Round 2: 安全硬阈值（Toxin/Hemo/MHC）
    ▼
~1,500,000  安全通过
    │ Round 3: 深度评分 SD 加权 → Top 5%
    ▼
~75,000     深度评分通过
    │ Round 4: 枚举 × 6 (3 pos × 2 linker) → SoDoPE/TemStaPro → Top 5%
    ▼
~22,500     constructs
    │ Round 5–6: OmegaFold + SASA + Aggrescan3D（全部通过）
    ▼
~22,500     评分 construct
    │ Round 7: 最终排名
    ▼
  Top 100 + Bottom 100
```

### 瓶颈估算

| 轮次 | 最耗时服务 | 单条耗时 | 总耗时（估） | 并发加速后 |
|------|-----------|---------|-------------|-----------|
| Round 1 | AnOxPePred | ~0.1s | ~23d | ~2d (batch 1K, concurrency 10) |
| Round 2 | ToxinPred3 | ~0.5s | ~11d | ~1d (batch 10, concurrency 5) |
| Round 3 | BepiPred3 (GPU) | ~2.5s | ~43d | ~2d (concurrency 5, Semaphore) |
| Round 5 | OmegaFold (GPU) | ~120s | ~31d | ~31d (串行，不可并发) |

---

## 7. 可变权重机制详解

### 两层权重结构

```
┌─────────────────────────────────────────────────────────┐
│                     weight_config 表                       │
│                                                         │
│  {                                                       │
│    "round": 3,                                           │
│    "formula": "winsorized_stddev_normalized",            │
│    "sd_weights": {                                       │
│      "bepipred3_score":  0.31,    ← 数据驱动的基础权重  │
│      "temstapro_score":  0.18,                           │
│      "sodope_score":     0.22,                           │
│      "plm4cpps_score":   0.19,                           │
│      "graphcpp_score":   0.10                            │
│    },                                                    │
│    "manual_coefficients": {                              │
│      "bepipred3_score":  1.0,     ← 手动调节系数       │
│      "temstapro_score":  1.0,                            │
│      "sodope_score":     1.0,                            │
│      "plm4cpps_score":   1.0,                            │
│      "graphcpp_score":   1.0                             │
│    },                                                    │
│    "final_weights": {                                    │
│      "bepipred3_score":  0.31,    ← 归一化后的最终权重  │
│      ...                                                 │
│    },                                                    │
│    "adjustment_reason": "默认配置，无手动调节"            │
│  }                                                       │
└─────────────────────────────────────────────────────────┘
```

### 调节原则

1. 默认所有 `α = 1.0`（纯 SD 驱动，无需人工干预）
2. 修改 α 必须填写 `adjustment_reason`（如"AnOxPePred 是核心功能指标，轻微上抬"）
3. 可以单次运行中尝试多组 α 值做敏感性分析
4. 所有历史调节记录保存在 `weight_config` 表，可完全追溯

### 使用场景示例

| 场景 | 操作 | 效果 |
|------|------|------|
| 默认 | α = 1.0 全部 | 纯数据驱动 |
| 轻微强调抗氧化 | anoxpepred α = 1.3 | SD 权重 × 1.3 后再归一化 |
| 排除无区分度属性 | temstapro α = 0 | 完全从加权中排除 |
| 强调 B 细胞表位 | bepipred3 α = 1.5 | B 细胞表位权重上调 50% |

---

## 8. 与 stages2 的对比

| 维度 | stages2（问题） | stages4（修复） |
|------|----------------|-----------------|
| 安全属性 | 低权重混入加权平均（0.05–0.13），可被抗氧化"补偿" | **独立硬阈值淘汰**，一票否决 |
| 抗氧化使用 | 多轮次隐含使用，污染双通道 | **Round 1 分选专用；Round 3 可选参与** |
| 双通道隔离度 | 后续排名仍受抗氧化影响 | **通道独立排名，无交叉污染** |
| 加权方式 | 人工固定权重（凭经验赋值） | **SD 驱动 + 手动系数（数据驱动 + 领域知识）** |
| 加权位置 | Round 2 + Round 3 + Round 6 | **仅 Round 3** |
| 权重可审计性 | CSV 中手动记录 | **weight_config 表，SD权重+手动系数+理由** |
| 文件命名 | `roundXX_name.py` + `step00` 混用 | **统一 `s4_roundXX_name.py`** |
| 状态管理 | CSV 文件接力 | **DuckDB 统一管理** |
| 无区分度属性 | 仍分配固定权重 | **自动降至零权重，或手动设为 α=0** |
| 断点续跑 | 手动检查输出文件 | **checkpoint 表自动恢复** |

---

## 9. 技术架构

### 基础设施来源

| 组件 | 来源 | 修改 |
|------|------|------|
| `s4_db.py` | 基于 stages3 `db.py` 重构 | 表名改为 round_*，新增通道/安全筛选操作 |
| `s4_docker_utils.py` | 从 stages3 直接复制 | 无修改 |
| `s4_service_map.py` | 重写 | 按 8 轮重新映射服务依赖 |
| `s4_analytics.py` | 基于 stages3 `analytics.py` 重构 | 新增手动系数 α 支持 |
| `ServiceClient` | `main/client.py` | 直接复用 |

### 并发策略

| 轮次 | 策略 | 说明 |
|------|------|------|
| Round 1 | Semaphore(10–20), batch 1K/请求 | AnOxPePred 可高并发 |
| Round 2 | Semaphore(2–5), ToxinPred3 串行化 | ToxinPred3 sklearn 线程不安全 |
| Round 3 | Semaphore(3–5) per GPU service | GPU 服务控制并发防 OOM |
| Round 5 | Semaphore(1) 串行 | OmegaFold 阻塞事件循环 |
| Round 6 | Semaphore(10) | CPU 服务可高并发 |

### 检查点与断点续跑

复用 stages3 的 checkpoint 机制：`set_checkpoint()` / `get_checkpoint()` / `get_last_processed_id()`。

---

## 10. 待决策事项

| 问题 | 选项 | 建议 |
|------|------|------|
| Round 1 Top 取多少 %？ | 5% / 10% / 20% | 10%（~1.99M） |
| Round 1 Bottom 取多少 %？ | 0.5% / 1% / 2% | 1%（~199K） |
| Round 3 取多少 %？ | 1% / 5% / 10% | 5%（~75K） |
| Round 4 取多少 %？ | 1% / 5% / 10% | 5%（~22.5K constructs） |
| MHCflurry 硬阈值？ | ≥0.5 / ≥0.7 / 不设 | 需查文献确认 |
| AnOxPePred 是否参与 Round 3 加权？ | 参与 / 不参与 | 建议参与（α=1.0 默认） |
| Round 7 排名依据 | 纯 SASA / SD加权(3D) | 建议先看分布再决定 |
| 是否复用 stages3 的 DB？ | 复用 / 重跑 | 如果 schema 兼容则复用 |
