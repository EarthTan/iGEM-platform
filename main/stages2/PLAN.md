# Stages2 设计方案

> 基于第一轮全流程（1843 肽 → 90 construct → ESMFold → OmegaFold → AF3）的复盘教训，完全重构的筛选 Pipeline。
>
> **参考**: `main/PLAN.md`（原始漏斗设计哲学、微服务成本分析、GPU 显存管理）
> **复盘**: `output/REVIEW.md`（第一轮的问题、教训、数据）

---

## 与第一轮的核心差异

| 维度 | 第一轮 | 第二轮 |
|------|--------|--------|
| **数据量** | 1,843 条（仅 function.csv 抗氧化子集） | ~**105 万**条（function_1 + 2 + 3 全量抗氧化肽） |
| **筛选哲学** | 硬过滤（绝对淘汰有毒/致敏/溶血肽） | **评分排名**（所有维度加权评分，安全维度反向计分，无绝对淘汰） |
| **服务编排** | 先过滤 → 后评分（过滤淘汰 94%，剩 107 条评分） | **三轮递进评分**（轻→中→重，逐轮缩小候选池） |
| **CPP 预测** | pLM4CPPs(0.15) + GraphCPP(0.05) 参与评分 | **移除**（抗氧化目标不需要 CPP） |
| **3D 结构** | ESMFold → OmegaFold → AF3 串行递进 | **OmegaFold + ESMFold 同时跑** |
| **长度约束** | 无 | **3 ≤ 肽长 ≤ 30** |
| **安全机制** | 硬过滤，超阈值直接淘汰 | **宽松安全标记**，极端高危肽打标不剔除 |

---

## 数据流总览

```
                    function_1 (24,694 条)
                    function_2 (1,055,961 条)
                    function_3 (1,117 条)
                           │
                    ┌──────┴──────┐
                    │  数据整合层  │  ← 合并、去重、长度过滤 (3-30aa)、标准氨基酸清洗
                    └──────┬──────┘
                           │ ~105 万条抗氧化肽
                           ▼
               ╔═══════════════════════╗
               ║  Round 1: 轻量评分   ║  ← AnOxPePred + ToxinPred3 + AlgPred2
               ║  ~15 min, Top 50K    ║     ~10 min 实际 scoring + 并行开销
               ╚═══════════════════════╝
                           │
                           ▼
               ╔═══════════════════════╗
               ║  Round 2: 中等评分   ║  ← +HemoPI2 + MHCflurry
               ║  ~10 min, Top 5K     ║     重排名，追加 2 个服务
               ╚═══════════════════════╝
                           │
                           ▼
               ╔═══════════════════════╗
               ║  Round 3: 重服务评分 ║  ← +BepiPred-3.0 (+可选 TemStaPro)
               ║  ~5 min, Top 80      ║     最终排名
               ╚═══════════════════════╝
                           │
                           ▼
               ╔══════════════════════════╗
               ║  枚举 + Construct      ║  ← Top N 肽 × 2 Linker × 3 位置
               ║  评分(SoDoPE) +        ║     +SoDoPE → 按(肽,Linker)分组
               ║  分组排序              ║     → Top 组
               ╚══════════════════════════╝
                           │
                           ▼
               ╔══════════════════════════╗
               ║  Construct 级再评分     ║  ← AnOxPePred + BepiPred 在全长上
               ║  游离 vs 融合 活性对比  ║     计算变化比，更新综合分
               ╚══════════════════════════╝
                           │
                           ▼
               ╔══════════════════════════╗
               ║  双模型 3D 预测         ║  ← OmegaFold + ESMFold 同时跑
               ║  ≤100 construct         ║     取置信度高的结果
               ╚══════════════════════════╝
                           │
                           ▼
               ╔══════════════════════════╗
               ║  PDB 评估 + 最终报告    ║  ← SASA + Aggrescan3D → 最终排名
               ╚══════════════════════════╝
```

---

## 评分服务体系

### 权重总表（6+1 服务，总和 = 1.00）

| 服务 | 权重 | 方向 | 分类 | 吞吐 | 出现轮次 | 测量内容 |
|------|------|------|------|------|----------|----------|
| **AnOxPePred** | **0.50** | 正向 | 核心功能 | ~200 条/秒 | Round 1 | 抗氧化活性（CNN） |
| **ToxinPred3** | 0.15 | **反向** | 安全维度 | ~2000 条/秒 | Round 1 | 毒性预测 |
| **AlgPred2** | 0.10 | **反向** | 安全维度 | ~2000 条/秒 | Round 1 | 致敏性预测 |
| **HemoPI2** | 0.10 | **反向** | 安全维度 | ~100 条/秒 | Round 2 | 溶血性预测 |
| **MHCflurry** | 0.05 | **反向** | 免疫原性 | ~500 条/秒 | Round 2 | MHC-I 结合亲和力 |
| **BepiPred-3.0** | 0.10 | 正向 | 免疫表位 | ~50 条/秒 | Round 3 | B 细胞表位潜力 |
| **TemStaPro** | *0.05* | 正向 | 稳定性 | ~20 条/秒 | Round 3(可选) | 热稳定性 |

**移除的服务**（与第一轮对比）：
- ~~pLM4CPPs~~（CPP，与抗氧化目标无关）
- ~~GraphCPP~~（CPP，且是吞吐瓶颈 30 条/秒）

**安全维度均为反向计分**：得分越高 → 归一化后 `1.0 - score` → 加权越低 → 排名越靠后。极端高危肽自然沉底，不设硬阈值。

### 三轮递进的吞吐设计逻辑

| 轮次 | 服务 | 候选量 | 瓶颈服务 | 耗时 | 累计 |
|------|------|--------|----------|------|------|
| R1 | AnOxPePred(200/s) + ToxinPred3(2000/s) + AlgPred2(2000/s) | 1,057,020 | AnOxPePred: 1M/200≈**83 min** | ~15 min* | ~15 min |
| R2 | + HemoPI2(100/s) + MHCflurry(500/s) | 50,000 | HemoPI2: 50K/100≈**8 min** | ~10 min | ~25 min |
| R3 | + BepiPred-3.0(50/s) | 5,000 | BepiPred: 5K/50≈**2 min** | ~5 min | ~30 min |

*\* Round 1 的实际耗时远小于 83 min，因为 client.py 的 `predict_batch` 并发调用多个服务（不是串行等 AnOxPePred 跑完再跑 ToxinPred3）。三个服务同时发 + 按最慢的等 ≈ AnOxPePred 时间。且 100 万条会按 1000 条/批拆分，httpx 并发减少网络开销。*

**核心原则**：BepiPred-3.0（最重，50 条/秒）只运行在 Round 2 输出的 5,000 条上，而不是全部 100 万条。这遵循了原 PLAN.md 的结论——"重的只留给少数"。

---

## 安全标记系统

不设硬过滤阈值，但设**三级安全标记**：

| 级别 | 标记 | ToxinPred3 | AlgPred2 | HemoPI2 | 行为 |
|------|------|-----------|----------|---------|------|
| 🟢 正常 | `safe` | < 0.60 | < 0.50 | < 0.70 | 无标记 |
| 🟡 注意 | `caution` | 0.60-0.80 | 0.50-0.70 | 0.70-0.85 | 在排名表中标记 |
| 🔴 高危 | `danger` | ≥ 0.80 | ≥ 0.70 | ≥ 0.85 | 标记 + 在 README 中单独列出 |

**这些标记不影响排名计算**，安全服务通过"反向计分"自然拉低排名。标记仅在报告中提供参考——如果某肽 AnOxPePred 极高但 ToxinPred3 高危，用户能看见。

极端情况才打🔴，避免第一轮 AlgPred2 在 0.30 阈值误杀 58% 的问题。

---

## 各阶段详细设计

### Round 1：轻量评分（`round01_lightweight.py`）

**前提条件**：数据已整合（function_1 + 2 + 3 合并、去重、长度过滤、标准 AA 清洗）

**参与服务**：
- AnOxPePred（抗氧化核心，权重 0.50）
- ToxinPred3（毒性反向，权重 0.15）
- AlgPred2（致敏反向，权重 0.10）

**执行流程**：
1. 加载 105 万条候选肽，统一分配 `peptide_id`
2. 同时向 3 个服务提交 `predict_batch`（自动按 1000 条/批拆分）
3. 收集结果，计算加权综合分
4. 安全标记：检查各安全服务得分，打 `safe/caution/danger` 标记
5. 按综合分降序排列，取 **Top 50,000**

**输出**：
```
output/round01_lightweight/
├── README.md              ← 得分分布、Top 20 展示、安全标记统计
├── run.log
├── scores/                ← 各服务原始返回
├── final/passed.csv       ← Top 50,000（含综合分+安全标记）
└── final/danger_list.csv  ← 🔴 高危肽单独清单
```

**预期耗时**: ~15 分钟（并发调用，实时网络往返）
**预期安全标记分布**: ~1-5% 🔴（约 1-5 万条）

---

### Round 2：中等评分（`round02_medium.py`）

**前提条件**：Round 1 完成，有 Top 50,000 的评分数据

**新增服务**：
- HemoPI2（溶血反向，权重 0.10）
- MHCflurry（MHC-I 反向，权重 0.05）

**执行流程**：
1. 读取 Round 1 的 Top 50,000
2. 追加调用 HemoPI2 + MHCflurry（仅在 50K 上跑）
3. 合并 Round 1 的 3 个分数，用 5 服务权重重算综合分
4. 更新安全标记（HemoPI2 加入）
5. 按综合分降序排列，取 **Top 5,000**

**输出**：
```
output/round02_medium/
├── README.md              ← 排名变化、新服务得分分布
├── run.log
├── scores/                ← HemoPI2 + MHCflurry 原始返回
├── final/passed.csv       ← Top 5,000（含全部 5 服务分数）
└── final/rank_changes.csv ← 相比 Round 1 的排名变动
```

**预期耗时**: ~10 分钟

---

### Round 3：重服务评分（`round03_heavy.py`）

**前提条件**：Round 2 完成，有 Top 5,000 的评分数据

**新增服务**：
- BepiPred-3.0（B 细胞表位，权重 0.10）
- TemStaPro（热稳定性，权重 *0.05*，可选——如果就绪则跑，否则跳过，权重归零并重新归一化）

**执行流程**：
1. 读取 Round 2 的 Top 5,000
2. 追加调用 BepiPred-3.0（+ TemStaPro 如果可用）
3. 用全部 6（或 7）个服务重算最终综合分
4. 按综合分取 **Top 80**
5. 生成完整评分报告（含所有服务分数、安全标记、排名变动轨迹）

**输出**：
```
output/round03_heavy/
├── README.md              ← 最终排名报告、Top 20 展示
├── run.log
├── scores/                ← BepiPred 等原始返回
├── final/
│   ├── top80.csv          ← 最终 Top 80 肽
│   ├── all_scored.csv     ← 全部 5,000 条评分明细
│   ├── trajectory.csv     ← 跨轮排名变动（R1→R2→R3 的排名变化）
│   └── danger_list.csv    ← 🔴 高危肽最终清单
```

**预期耗时**: ~5 分钟（不含 TemStaPro），~10 分钟（含 TemStaPro）

---

### Stage 4：枚举 + Construct 评分 + 复评（`stage04_enumerate.py`）

**前提条件**：Round 3 完成，有 Top 80 肽

**枚举参数**：
- **优选肽数量**: 取 Top 20（可配置，但上限不超过 Top 80）
- **Linker**: 从 linker.fasta 选取 2-4 种（柔性 GGGGSx1/x2 + 其他精选）
- **位置方案**: N 端 / C 端 / 两端（3 种）
- **输出上限**: ≤100 construct 进入 3D 预测

**Construct 序列构建**：
```
N 端:   [功能肽] + [Linker] + [丝素核心] + [His6]
C 端:   [丝素核心] + [Linker] + [功能肽] + [Linker] + [His6]
两端:   [功能肽] + [Linker] + [丝素核心] + [Linker] + [功能肽] + [Linker] + [His6]
```

**SoDoPE 评分**：对每个 construct 做溶解度预测（<1 ms/条，极快）

**分组排序逻辑**（沿用第一轮验证有效的策略）：
1. 按 `(peptide_id, linker_id)` 分组，每组 3 个 position
2. 组内取最高综合分代表该组
3. 按组排序，选 Top K 组
4. 每组 3 个 position 全部输出 → 进入 3D 预测

**组综合分公式**：
```
G-FASTA 综合分 = 肽综合分(Round 3) × 0.60 + SoDoPE × 0.30 + TemStaPro × 0.10 (if available)
```

**自适应控制**：调整 K 使进入 3D 的 construct 数量 ≤ 100。
- K=33 → 99 construct（20 肽 × 2 Linker 可能不够？实际只有 40 组，可全进）

**Construct 级再评分子流程**（接 SoDoPE 之后）：

1. 取出全部候选 construct（≤100 条），同时提交 **AnOxPePred + BepiPred-3.0**
2. 对每个 construct 计算 **活性变化比**：

```
变化比 = construct_服务分 / peptide_服务分（来自 Round 3）
```

3. 用复评分更新组综合分，输出 **context_effect.csv**（游离 vs 融合对比表）

**输出**：
```
output/stage04_enumerate/
├── README.md
├── run.log
├── scores/
│   ├── all_ranked.csv        ← 全部 construct 评分明细
│   ├── context_effect.csv    ← 游离 vs 融合活性变化对比
│   └── per_service/          ← 各服务原始返回
├── final/
│   ├── constructs.csv        ← 全部 construct 评分明细
│   ├── topN.fasta            ← N 个 construct FASTA → 3D 预测
│   ├── topN.csv              ← 评分明细
│   └── stage5_input.json     ← 阶段五配置
```

**预期耗时**: <1 秒（枚举）+ <1 秒（SoDoPE）+ ~2-5 分钟（construct 级 AnOxPePred + BepiPred）

---

### Stage 5：双模型 3D 结构预测（`stage05_fold.py`）

**前提条件**：Stage 4 完成，有 ≤100 个 construct 的 FASTA

**核心变化**：同时向 OmegaFold 和 ESMFold 提交所有 construct，取置信度高的结果。

**并发策略**：

| 服务 | 并发 | 单条耗时 | 显存 | 总耗时（100 条） |
|------|------|----------|------|-----------------|
| ESMFold | 3 | ~2 min | ~8 GB × 3 = 24 GB | ~67 min |
| OmegaFold | 3 | ~1 min | ~8 GB × 3 = 24 GB | ~34 min |

**两个服务同时运行**意味着 GPU 显存冲突——ESMFold 和 OmegaFold 各自需要约 24 GB（并发 3）。48 GB 显存 **可以同时运行两者**，但每个服务只能并发 2 而不是 3，总耗时约为：
- ESMFold (并发 2): 100 × 2 min / 2 = 100 min
- OmegaFold (并发 2): 100 × 1 min / 2 = 50 min
- 两者同时跑 ≈ **100 min**（取最慢的，即 ESMFold）

如果显存不足，可串行：先跑完 OmegaFold（~34 min），再跑 ESMFold（~67 min），总计 ~101 min。实际上两者异步并发调度的总时间差不多。

**输出策略**：
- 对每个 construct，同时等待 ESMFold 和 OmegaFold 的结果
- 取 pLDDT 较高的 PDB 作为"最佳结构"
- 两者都失败的 construct 用 AlphaFold3 单独补救（限 Top 3，~80 min/条）

**检查点机制**（沿用第一轮经验）：每完成 5 个 construct 保存 checkpoint，崩溃后可恢复。

**输出**：
```
output/stage05_fold/
├── README.md
├── run.log
├── pdb_esmfold/           ← ESMFold 生成的 PDB
├── pdb_omegafold/         ← OmegaFold 生成的 PDB
├── pdb_best/              ← 每个 construct 的最佳 PDB（两者取高）
├── scores/
│   ├── esmfold_plddt.csv
│   ├── omegafold_plddt.csv
│   └── comparison.csv     ← 双模型 pLDDT 对比
├── checkpoint.json
├── final/
│   ├── best_structures.csv
│   └── stage6_input.json
```

**pLDDT 质量评估**：
| pLDDT 区间 | ESMFold 评估 | OmegaFold 评估 (0-100) | 行动 |
|-----------|-------------|----------------------|------|
| ≥ 0.70 / ≥ 70 | 高置信度 | 高置信度 | 直接使用 |
| 0.50-0.70 / 50-70 | 可信 | 可信 | 使用，标记"中等置信度" |
| < 0.50 / < 50 | 低置信度 | 低置信度 | 标记，考虑 AF3 验证 |

**预期耗时**: ~100 分钟（100 construct，并发 2+2）

---

### Stage 6：PDB 评估 + 最终报告（`stage06_pdb_eval.py`）

**前提条件**：Stage 5 完成，有最佳 PDB 文件

**评估服务**：
- **SASA**（溶剂可及表面积，CPU，1-5 秒/条，并发 10）：评估功能肽暴露度
- **Aggrescan3D**（聚集倾向，CPU Docker，1-15 分钟/条，并发 2）：评估聚集风险

**综合评分**：
```
final_score = composite_score(Stage 4) × 0.60 + SASA × 0.40
Aggrescan3D 作为参考，不参与加权
```

**输出**：最终排名报告，含所有维度的评分（肽评分 × 5-6 服务 + SoDoPE + ESMFold pLDDT + OmegaFold pLDDT + SASA + Aggrescan3D）。

**预期耗时**: SASA ~2 min + Aggrescan3D ~25-60 min

---

## 三轮排名轨迹

每个肽跨三轮的排名变化是重要的质量信号：

```
肽 XXX:
  Round 1 排名: 12/1,057,020  (仅 3 服务)
  Round 2 排名: 8/50,000      (+HemoPI2 + MHCflurry)
  Round 3 排名: 3/5,000       (+BepiPred-3.0)
  结论: 排名稳定上升，各维度一致看好 ✅

肽 YYY:
  Round 1 排名: 15/1,057,020
  Round 2 排名: 4,218/50,000  (HemoPI2 溶血分极高，反向计分后暴跌)
  Round 3 排名: 淘汰
  结论: 抗氧化好但溶血风险高，自然淘汰 ✅
```

这种轨迹分析在最终报告中自动生成。

---

## 每轮统计报告规范

每轮输出 README.md 必须包含以下统计内容，确保数据分布透明可追溯。

### 核心统计量

**综合分分布表**（对所有候选肽计算）：

```
综合分分布 (n=50000):
  均值:    0.3724
  中位数:  0.3681
  标准差: 0.0823
  最小值:  0.1124
  最大值:  0.6831
  P5:      0.2341  |  P25: 0.3012  |  P75: 0.4421  |  P95: 0.5214
```

### 分布直方图

```
  0.0-0.1: ██░░░░░░░░░░  (450 条,  0.9%)
  0.1-0.2: ██████░░░░░░  (2,891 条, 5.8%)
  0.2-0.3: ████████████  (12,450 条, 24.9%)
  0.3-0.4: ████████████  (19,234 条, 38.5%)  ← 均值
  0.4-0.5: ████████░░░░  (11,023 条, 22.0%)
  0.5-0.6: ████░░░░░░░░  (4,512 条,  9.0%)
  0.6-0.7: ██░░░░░░░░░░  (1,234 条,  2.5%)
  0.7+:    ░░░░░░░░░░░░  (206 条,   0.4%)
```

直方图的槽位自适应数据范围，确保展示 6-10 个区间。条形用字符块 `█`（实心）和 `░`（空白）按比例填充，总宽固定 14 字符。

### 每项服务的单独分布

对每个评分服务也输出单独分布，便于排查异常服务：

```
AnOxPePred 分布 (n=50000):
  均值: 0.3412, 标准差: 0.1124, 中位数: 0.3289
  P5: 0.1421  P25: 0.2612  P75: 0.4223  P95: 0.5342
  [直方图]

ToxinPred3 分布 (n=50000):
  均值: 0.2841, 标准差: 0.1532, ...
  [直方图]
```

### Top / Bottom 展示

- **Top 10** 肽：排名、ID、综合分、各服务分数、安全标记
- **Bottom 10** 肽：同上
- **前后对比**：如果是从上一轮继承，展示排名变化最大的 ±10 条

---

## Construct 级再评分（Stage 4 内）

### 动机

肽在游离状态下的评分（AnOxPePred 等）衡量的是其**固有活性**。但融合到丝素蛋白 scaffold 上后，构象环境可能改变其功能表现。如果某肽游离分高但 construct 分显著下降，说明 scaffold 上下文抑制了其活性——这是重要的排序信号。

### 适用范围

不是所有服务都适合跑全长 construct。按模型训练数据判断：

| 服务 | 全长适用性 | 理由 |
|------|-----------|------|
| **AnOxPePred** ✅ | **适用** | CNN 基于序列特征（氨基酸组成 + 位置），全长上虽含义不同，但能反映上下文活性 |
| **BepiPred-3.0** ✅ | **适用** | ESM-2 650M 在全蛋白上预训练，长序列天然支持 |
| **TemStaPro** ✅ | **适用** | ProtT5-XL 3B 全蛋白模型，已用于数百氨基酸的蛋白质 |
| SoDoPE | ✅ 已做 | 二肽查表，已在枚举后运行 |
| MHCflurry ❌ | **不适用** | 训练于 9-11 aa MHC 基序 |
| ToxinPred3 ❌ | **不适用** | 训练于短毒素肽 |
| AlgPred2 ❌ | **不适用** | 同上 |
| HemoPI2 ❌ | **不适用** | 同上 |

### 执行流程

Stage 4 枚举 construct 后，在 SoDoPE 评分之后追加：

1. 取全部候选 construct（≤100 条），提交 **AnOxPePred** 和 **BepiPred-3.0**
2. 对每个 construct，计算 **活性变化比**：

```
AnOxPePred_变化比 = construct_AnOxPePred / peptide_AnOxPePred（来自 Round 3）
BepiPred_变化比  = construct_BepiPred  / peptide_BepiPred（来自 Round 3）
```

- 变化比 > 1.0 → 融合后活性增强
- 变化比 ≈ 1.0 → 不受 scaffold 影响
- 变化比 < 0.8 → **显著下降**，标记警告

3. 更新组综合分（加入 construct 级复评分）：

```
G-FASTA 综合分(R2) = 肽综合分(Round 3) × 0.40
                   + SoDoPE × 0.25
                   + construct_AnOxPePred × 0.20
                   + construct_BepiPred × 0.10
                   + TemStaPro × 0.05 (if available)
```

SoDoPE 权重从 0.30 降至 0.25，挪出权重给 construct 级 AnOxPePred(0.20) 和 BepiPred(0.10)。肽综合分从 0.60 降至 0.40。

4. 输出 **context_effect.csv**：每条肽的游离分 vs construct 分对比，变化比排序。一眼看出哪些肽在 scaffold 上表现稳定，哪些活性被抑制。

### 预期

- 大多数肽的 construct 级分略低于肽级分（scaffold 有稀释效应）
- 如果某肽变化比 < 0.7，考虑在报告中标记"scaffold 活性抑制"
- 如果所有肽变化比都接近（例如都在 0.8-1.2），说明 scaffold 对功能肽的影响是均匀的，不需要用此指标排序

---

## 输出目录结构

```
output/
├── STATUS.md                 ← 最新进度指针
├── status/                   ← 时间戳状态快照
├── REVIEW.md                 ← 全流程复盘（第一轮已存在，第二轮追加）
│
├── round01_lightweight/      ← Round 1 输出
├── round02_medium/           ← Round 2 输出
├── round03_heavy/            ← Round 3 输出
├── stage04_enumerate/        ← Stage 4 输出
├── stage05_fold/             ← Stage 5 输出（双模型）
├── stage06_pdb_eval/         ← Stage 6 输出
│
└── final_output/             ← 最终打包（Top N construct 独立文件夹）
```

**每个阶段目录包含**：`README.md`（报告）、`run.log`（日志）、`final/`（输出数据）、`scores/`（原始分数）

---

## 上下文管理

沿用原 PLAN.md 第 9 节的上下文管理策略：

- **STATUS.md** — 最新状态锚点，每次阶段完成更新
- **status/status_YYYY-MM-DD_HHMM.md** — 带时间戳的快照
- **每个阶段的 README.md** — 做了什么、为什么、结果是什么
- **文件即记忆** — Claude 不记数据只记路径

独立的脚本设计：每个脚本可独立运行，`python -m main.stages2.round01_lightweight` 等，不依赖编排器。

---

## 实施顺序（走一步看一步）

| 步骤 | 内容 | 依赖 | 预计耗时 |
|------|------|------|----------|
| 1 | 数据整合层：合并 function_1/2/3，去重，长度过滤，标准 AA 清洗 | 无 | 一次性 |
| 2 | Round 1 轻量评分 + 安全标记脚本 | 步骤 1 | 1-2 次迭代 |
| 3 | Round 2 中等评分脚本 | 步骤 2 | 1-2 次迭代 |
| 4 | Round 3 重服务评分脚本 | 步骤 3 | 1-2 次迭代 |
| 5 | Stage 4 枚举 + Construct 评分 | 步骤 4 | 一次性 |
| 6 | Stage 5 双模型 3D 预测 | 步骤 5 | 2-3 次迭代 |
| 7 | Stage 6 PDB 评估 + 最终报告 | 步骤 6 | 1 次 |

每步产出可用中间结果，调整后再继续下一步。
