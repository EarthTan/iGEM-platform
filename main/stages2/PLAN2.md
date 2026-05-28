# Stages2 Pipeline Plan (PLAN2) — v2 修订版

> **目标**: 重新运行 stages2 完整 pipeline，输出到 `output2/`，修复原脚本错误，新增 Bottom-N。
>
> **关键变更 (v2)**: 
> 1. Round 1 **不跑 ToxinPred3**（105 万条太慢，~22h），改为在 Round 2 对 50K 补跑
> 2. Round 2 **按纯抗氧化性分选** top25K + bottom25K，再跑 3 个安全服务
> 3. Round 3 **在 50K 上跑 heavy 服务**（原 10K → 扩展为 50K 双通道）
> 4. 加权综合分公式固定记录，供下游统一使用

---

## 与原脚本的核心差异

| 维度 | 原 stages2 脚本 | PLAN2 修复版 (v2) |
|------|----------------|-------------------|
| **输出目录** | `output/` | **`output2/`** |
| **工具函数** | 每个脚本复制粘贴 8 次 | `common.py` 统一管理 |
| **分选策略** | 按加权综合分排序（混合抗氧化+安全） | **按纯 AnOxPePred 分选**，取 top25K + bottom25K |
| **ToxinPred3** | Round 1 硬跑 105 万条（~22h） | **Round 2 对 50K 补跑**（~48min，可接受） |
| **Round 3 输入** | 10K 条 | **50K 条**（top25K + bottom25K 双通道） |
| **Round 4 评分** | 只有 SoDoPE | 追加 AnOxPePred + BepiPred3 全长评分 |
| **断点续跑** | 无 | checkpoint.json |
| **并发控制** | 所有 `CONCURRENT_CHUNKS=10` | 按服务调整（ToxinPred3=2，其他=10） |
| **异常安全** | 部分缺 `return_exceptions=True` | 统一双层异常隔离 |
| **Docker 编排** | 手动 | 从 stages3 引入 `docker_utils.py` |

---

## 数据流总览

```
                    function_1 (24,694 条)
                    function_2 (1,055,961 条)
                    function_3 (1,117 条)
                           │
                    ┌──────┴──────┐
                    │  Step 0     │  ← 合并、去重、长度过滤 (3-30aa)
                    └──────┬──────┘
                           │ ~105 万条
                           ▼
               ╔══════════════════════════╗
               ║  Round 1: 轻量评分      ║  ← AnOxPePred + AlgPred2（无 ToxinPred3）
               ║  105 万条 → 全量评分     ║     按纯 anoxpepred 排序准备分选
               ╚══════════════════════════╝
                           │
                           ▼
               ╔═══════════════════════════════════╗
               ║  Round 2: 分选 + 安全评分        ║
               ║  ① 按 anoxpepred 取 top25K+bottom25K  ║
               ║  ② 跑 ToxinPred3 + HemoPI2 + MHCflurry ║
               ╚═══════════════════════════════════════╝
                           │ 50K 条（含双通道标签）
                           ▼
               ╔══════════════════════════════╗
               ║  Round 3: 重服务评分        ║
               ║  BepiPred3 + TemStaPro      ║
               ║  50K → top N + bottom M     ║
               ╚══════════════════════════════╝
                          ╱╲
                         ╱  ╲
                        ╱    ╲
                       ▼      ▼
           ╔══════════════╗  ╔══════════════════╗
           ║ Round 4a:   ║  ║ Round 4b:       ║
           ║ Top 枚举    ║  ║ Bottom 枚举     ║
           ╚══════════════╝  ╚══════════════════╝
                       ▼      ▼
               ╔═══════════════════════╗
               ║  Round 5: 3D 结构    ║  ← ESMFold + OmegaFold
               ║  双模型并发           ║     全部 constructs 无差别
               ╚═══════════════════════╝
                           │
                           ▼
               ╔═══════════════════════╗
               ║  Round 6: PDB 评估   ║  ← SASA + Aggrescan3D
               ╚═══════════════════════╝
                           │
                           ▼
               ╔════════════════════════╗
               ║  Round 7: 最终输出    ║  ← Top + Bottom 双排名
               ║  两份排名 + 完整报告   ║
               ╚════════════════════════╝
```

---

## 输出目录结构

```
output2/
├── STATUS.md                        ← 最新进度指针
│
├── step00_integrate/                ← Step 0: 数据整合（cleaned.csv, 105 万条）
├── round01_lightweight/             ← Round 1: AnOxPePred + AlgPred2（全量评分）
│   └── final/
│       ├── all_scored.csv           ← 全部 105 万条（含单项分）
│       └── top50k.csv              ← 按加权分排序（仅参考）
├── round02_scoring/                 ← Round 2: 分选 + ToxinPred3/HemoPI2/MHCflurry
│   └── final/
│       ├── all_50k.csv             ← 全部 50K（top25K+bottom25K+5 服务分+channel）
│       ├── top25k.csv
│       └── bottom25k.csv
├── round03_heavy/                   ← Round 3: BepiPred3 + TemStaPro 重服务
│   └── final/
│       ├── top80.csv               ← Top 通道选出的 construct 候选
│       └── bottom10.csv            ← Bottom 通道选出的阴性对照
├── round04_enumerate/               ← Round 4: 枚举 + construct 级评分
├── round05_3d/                      ← Round 5: ESMFold + OmegaFold 3D
├── round06_pdb_eval/               ← Round 6: SASA + Aggrescan3D
│
└── round07_final/                   ← Round 7: 最终双通道输出
    ├── README.md                    ← 全流程报告
    ├── top_ranking.csv              ← Top constructs（综合分→SASA 排名）
    ├── top10_summary.csv
    ├── bottom_ranking.csv           ← Bottom constructs
    ├── bottom10_summary.csv
    ├── score_distribution.json
    └── constructs/                  ← 每个 construct 独立文件夹
```

---

## Bottom-N：安全维度正常的抗氧化最差肽

### 动机

主 pipeline 按加权综合分排序，抗氧化活性（AnOxPePred）权重 0.50，主导排名。但 wet-lab 验证时，除了"最好"
的候选，有时也需要"最差但安全的对照"——即在所有安全维度都表现良好、唯独抗氧化活性最低的肽。

这些肽可以作为：
1. **阴性对照**：与 Top 肽对比验证抗氧化活性检测系统的动态范围
2. **安全边界验证**：确认即使抗氧化活性最差的肽，其他性质是否仍然安全
3. **意外发现**：某些肽可能在 AnOxPePred（CNN 模型）上得分低，但实际有其他未被模型捕捉的活性

### 筛选逻辑

在 Round 3 获取全 7 服务评分后，平行执行 Bottom-N 筛选：

```
从 10,000 条已评分肽中:
  1. 安全过滤器（必须全部通过）:
     - ToxinPred3 < 0.60（安全）
     - AlgPred2   < 0.50（安全）
     - HemoPI2    < 0.70（安全）
     - MHCflurry  < 0.50（低免疫原性）
     - BepiPred3  < 0.60（低 B 细胞表位风险）
     - (TemStaPro > 0.30 如果可用，稳定性尚可)
  2. 按 AnOxPePred 升序排列（抗氧化活性从低到高）
  3. 取 Bottom 10 条（抗氧化活性最差但其他维度安全）
```

### 后续流程

Bottom 10 肽与 Top 80 肽走完全相同的后续流程：
- Round 4：枚举（×2 Linker × 3 位置 = 60 constructs）+ SoDoPE + construct 级 re-score
- Round 5：ESMFold + OmegaFold 3D 预测
- Round 6：SASA + Aggrescan3D 评估
- Round 7：单独输出 Bottom 排名，与 Top 排名并列展示

这样在 Round 7 最终报告中，可以清楚看到:
```
─────────────────────────────────────
  Top 10（综合分最高，含 AnOxPePred 主导）
─────────────────────────────────────
  1. pep_000142 | AnOxPePred=0.89 | 综合分=0.72
  2. pep_000831 | AnOxPePred=0.87 | 综合分=0.70
  ...

─────────────────────────────────────
  Bottom 10（安全但 AnOxPePred 最低）  
─────────────────────────────────────
  1. pep_003214 | AnOxPePred=0.12 | ToxinPred3=0.21 | HemoPI2=0.34 ← 抗氧化最低但安全
  2. pep_008912 | AnOxPePred=0.15 | ToxinPred3=0.18 | HemoPI2=0.29
  ...
```

---

## 各轮次详细变化

### Step 0：数据整合（`step00_integrate.py`）

**与原始脚本的差异**：
- 仅修改：输出目录从 `output/` → `output2/`
- 导入 `common.py` 中的工具函数代替本地复制
- 数据逻辑不变

**输入**：`data/function_1.csv`、`data/function_2.csv`、`data/function_3.csv`
**输出**：`output2/step00_integrate/final/cleaned.csv`（~105 万条）
**依赖**：无
**预计耗时**：~30s

---

### Round 1：轻量评分（`round01_lightweight.py`）

**与原脚本的差异**：
- 输出到 `output2/`
- 使用 `common.py`
- **不跑 ToxinPred3** — 105 万条用 sklearn ExtraTreesClassifier 需 ~22 小时，改为在 Round 2 对 50K 补跑
- 按服务调整并发：AnOxPePred=10, AlgPred2=10
- 统一 `asyncio.gather` 异常安全
- 输出 `all_scored.csv`（全量评分）供 Round 2 分选

**服务**：AnOxPePred(0.50) + AlgPred2(0.10)
**输入**：`output2/step00_integrate/final/cleaned.csv`（~105 万条）
**输出**：
- `final/all_scored.csv` — 全部 105 万条评分明细
- `final/top50k.csv` — 按加权综合分排序 Top 50K（仅作参考，Round 2 不用此排序）
**依赖**：Step 0
**预计耗时**：~15 min

---

### Round 2：分选 + 安全评分（`round02_scoring.py`）

**v2 变更（与原 PLAN2 完全不同！）**：
- **按纯抗氧化性分选**：读 `all_scored.csv`，按 `anoxpepred` 原始分降序排列
  - Top 25K：抗氧化活性最好的 25,000 条
  - Bottom 25K：抗氧化活性最差的 25,000 条（阴性对照）
  - 丢弃 Round 1 的加权综合分排序
- **补跑 ToxinPred3**：因 Round 1 跳过全量，在此对 50K 单独跑（并发 2）
- **追加 HemoPI2 + MHCflurry**（并发 10 各）
- **保留 AlgPred2 分数**（来自 Round 1）
- **计算并记录综合分**：5 服务权重固定记录在脚本中
```
weighted_score = Σ(normalized_i × weight_i) / Σ(weight_i)
  正向: anoxpepred (0.50)
  反向: toxinpred3 (0.15) + algpred2 (0.10) + hemopi2 (0.10) + mhcflurry (0.05)
```
- **输出含 channel 标签**（top/bottom），贯穿后续所有 round

**输入**：`output2/round01_lightweight/final/all_scored.csv`（105 万条全量评分）
**输出**：
- `final/all_50k.csv` — 全部 50K（top25K + bottom25K + 5 服务分 + 综合分 + 安全标记 + channel 标签）
- `final/top25k.csv` — Top 25K
- `final/bottom25k.csv` — Bottom 25K
**依赖**：Round 1
**预计耗时**：~50 min（ToxinPred3 是瓶颈）

---

### Round 3：重服务评分（`round03_heavy.py`）

**v2 变更**：
- **输入从 10K 扩展为 50K**（top25K + bottom25K 双通道）
- **按 channel 分别选择 top N 和 bottom M**：
  - Top 通道：在 top25K 中按综合分取前 N 条
  - Bottom 通道：在 bottom25K 中按安全过滤 → 取抗氧化最差 M 条
- 追加 BepiPred-3.0(0.07) + TemStaPro(0.05)
- `asyncio.wait_for` 不能中断 C 扩展 → 改用 socket 级别超时

**服务**：追加 BepiPred-3.0(0.07) + TemStaPro(0.05)
**输入**：`output2/round02_scoring/final/all_50k.csv`
**输出**：
- `output2/round03_heavy/final/top80.csv` — Top 通道按综合分 Top 80
- `output2/round03_heavy/final/bottom10.csv` — Bottom 通道安全过滤后取抗氧化最差 10 条
- `output2/round03_heavy/final/all_scored.csv` — 全部 50K 评分明细
**依赖**：Round 2
**预计耗时**：取决于服务并发性能

---

### Round 4：枚举 + Construct 评分（`round04_enumerate.py`）

**与原始脚本的差异**：
- 输出到 `output2/`
- 使用 `common.py`
- **新增：construct 级 re-score** — 枚举后对全长 construct 运行 AnOxPePred + BepiPred3，
  计算活性变化比（construct_score / peptide_score），更新综合分
- **Top + Bottom 双通道枚举** — 从 Top 80 取前 40 条 + Bottom 10 全部，各自枚举
- 分组排序逻辑与原脚本相同（peptide × linker 分组，组内取最高分）

**Construct 级评分公式**（来自原 PLAN.md 但未实现）：
```
综合分(R2) = 肽综合分(Round 3) × 0.40
           + SoDoPE × 0.25
           + construct_AnOxPePred × 0.20
           + construct_BepiPred × 0.10
           + TemStaPro × 0.05 (if available)
```

**活性变化比**：
```
AnOxPePred_变化比 = construct_AnOxPePred / peptide_AnOxPePred（来自 Round 3）
BepiPred_变化比  = construct_BepiPred  / peptide_BepiPred（来自 Round 3）
变化比 > 1.0 → 融合后活性增强
变化比 ≈ 1.0 → 不受 scaffold 影响
变化比 < 0.8 → 显著下降，标记警告
```

**枚举参数**：
- Top 通道：40 肽 × 2 Linker × 3 位置 = 240 constructs → 分组排序 → Top 组
- Bottom 通道：10 肽 × 2 Linker × 3 位置 = 60 constructs → 分组排序 → Bottom 组
- construct 上限：≤100（两个通道合计不超过此数，比例约 80% top + 20% bottom）

**输入**：
- `output2/round03_heavy/final/top80.csv`
- `output2/round03_heavy/final/bottom10.csv`
- `data/silk.fasta`
- `data/linker.fasta`

**输出**：
- `output2/round04_enumerate/final/constructs_top.csv` — Top constructs
- `output2/round04_enumerate/final/constructs_bottom.csv` — Bottom constructs
- `output2/round04_enumerate/final/all_constructs.csv` — 全部
- `output2/round04_enumerate/final/all_constructs.fasta` — 全部进入 3D
- `output2/round04_enumerate/final/context_effect.csv` — 游离 vs 融合活性变化

**依赖**：Round 3
**预计耗时**：~5 min

---

### Round 5：3D 结构预测（`round05_3d.py`）

**与原始脚本的差异**：
- 输出到 `output2/`
- 使用 `common.py`
- **引入 `docker_utils.py`**（从 stages3 适配）替代手动服务管理
- 保留 OmegaFold Docker 桥接 IP 检测（`_fix_omegafold_docker_network()`，原脚本验证有效的模式）
- 修复原脚本杀死结构服务自身的 bug
- 所有 construct（top + bottom）统一处理，不做区分

**服务**：ESMFold(port 8203) + OmegaFold(port 8204)
**输入**：`output2/round04_enumerate/final/all_constructs.fasta`
**输出**：`output2/round05_3d/constructs/con_XXXX/`（每个 construct 独立文件夹）
**依赖**：Round 4
**预计耗时**：~2h（90 constructs，并发 2+2）

---

### Round 6：PDB 评估（`round06_pdb_eval.py`）

**与原始脚本的差异**：
- 输出到 `output2/`
- 使用 `common.py`
- 修复输出文件名：写出 `sasa_ranking.csv`（与 round07 期望一致）
- 使用 `docker_utils.py` 确保 SASA + Aggrescan3D 就绪

**服务**：SASA(port 8101) + Aggrescan3D(port 8102)
**输入**：`output2/round05_3d/constructs/con_XXXX/`
**输出**：`output2/round06_pdb_eval/final/sasa_ranking.csv`
**依赖**：Round 5
**预计耗时**：~3 min

---

### Round 7：最终输出（`round07_final.py`）

**与原始脚本的差异**：
- 输出到 `output2/`
- 使用 `common.py`
- 修复原脚本读 `final_ranked_sasa.csv`（不存在）→ 读 `sasa_ranking.csv`（已修复）
- 修复 README 中的轮次标签（原脚本显示的是 stages1 的标签，全错）
- **新增 Bottom 排名** — 与 Top 排名并列展示，各自独立 ranking

**排名标准**：
- Top 排名：按 SASA 暴露度从高到低（同原设计）
- Bottom 排名：按 SASA 暴露度从高到低（与 Top 相同标准，独立排序）

**输入**：`output2/round06_pdb_eval/final/sasa_ranking.csv`
**输出**：
```
output2/round07_final/
├── README.md                     ← 全流程报告（含 Top + Bottom 对比表）
├── top_ranking.csv               ← Top constructs 排名
├── top10_summary.csv             ← Top 10 精简表
├── bottom_ranking.csv            ← Bottom constructs 排名
├── bottom10_summary.csv          ← Bottom 10 精简表
├── score_distribution.json       ← 各分数维度分布
└── constructs/                   ← 每个 construct 独立文件夹
```
**依赖**：Round 6
**预计耗时**：~1s

---

## 脚本清单与编写顺序

以下为 PLAN2 涉及的全部脚本，按编写顺序排列（先基础后应用）：

| # | 文件名 | 说明 | 新写/修改 |
|---|--------|------|-----------|
| 1 | `main/stages2/common.py` | **共享工具模块**：log、describe、make_dir、write_json、calc_safety_flag、checkpoint 读写 | **新写** |
| 2 | `main/stages2/step00_integrate.py` | 数据整合（复用原有逻辑，改用 common.py，改输出到 output2/） | **重写** |
| 3 | `main/stages2/round01_lightweight.py` | Round 1 重写（检查点、按服务并发、修复输出名） | **重写** |
| 4 | `main/stages2/round02_scoring.py` | Round 2 重写（不移 ToxinPred3、输入名修复、检查点） | **重写** |
| 5 | `main/stages2/round03_heavy.py` | Round 3 重写（检查点、Bottom-N 新增、修复超时处理） | **重写** |
| 6 | `main/stages2/round04_enumerate.py` | Round 4 重写（construct re-score、Top+Bottom 双通道） | **重写** |
| 7 | `main/stages2/round05_3d.py` | Round 5 重写（docker_utils、修复杀死自身 bug） | **重写** |
| 8 | `main/stages2/round06_pdb_eval.py` | Round 6 重写（修复输出文件名、docker_utils） | **重写** |
| 9 | `main/stages2/round07_final.py` | Round 7 重写（修复标签、Bottom 排名、修复文件名） | **重写** |

**外部依赖**（从 stages3 复用，不改动）：
- `main/stages3/docker_utils.py` — 按需启动 Docker 微服务
- `main/stages3/service_map.py` — 服务依赖地图（需确认是否可直接复用，或为 stages2 创建简化版）

---

## 执行计划

### 阶段 1：编写全部脚本 ✅

全部脚本已编写完成（step00 → round01 → round07）。

### 阶段 2：逐步执行（进行中）

每步执行后检查输出，确认无误后再进入下一步：

```bash
# Step 0：数据整合（~30 秒）✅ 已完成
uv run python -m main.stages2.step00_integrate
# → 1,055,116 条清洗完成

# Round 1：轻量评分（~15 分钟）✅ 已完成
uv run python -m main.stages2.round01_lightweight
# → AnOxPePred + AlgPred2 全量评分完成
# → all_scored.csv + top50k.csv 已输出

# Round 2：分选 + 安全评分（~50 分钟）🔄 进行中
uv run python -m main.stages2.round02_scoring
# → HemoPI2 ✅ 50K/380s
# → MHCflurry ❌ 模型未下载（需 fix Dockerfile --release 参数）
# → ToxinPred3 ⏳ 运行中（并发 2，预计 ~48 分钟）
# → 输出: all_50k.csv（含 channel 标签 + 5 服务分 + 综合分 + 安全标记）

# Round 3：重服务评分
uv run python -m main.stages2.round03_heavy
# → 在 50K 上跑 BepiPred3 + TemStaPro
# → 输出 top80 + bottom10

# Round 4：枚举 + Construct 评分
uv run python -m main.stages2.round04_enumerate
# → 双通道枚举 + construct 级 re-score

# Round 5：3D 结构预测（~2 小时）
uv run python -m main.stages2.round05_3d
# → ESMFold + OmegaFold

# Round 6：PDB 评估（~3 分钟）
uv run python -m main.stages2.round06_pdb_eval
# → SASA + Aggrescan3D

# Round 7：最终输出（~1 秒）
uv run python -m main.stages2.round07_final
# → Top + Bottom 双排名
```

### 阶段 3：验证

- 对比 top/bottom 的 AnOxPePred 分布差异
- 检查 bottom 中各安全维度分数是否均在阈值以下
- 确认所有 PDB 文件可正常加载

---

## 注意事项

1. **与 stages3 隔离** — stages3 正在 `output3/` 上运行 Step 1。本 pipeline 使用 `output2/`
   和 `main/stages2/`，不触碰 `output3/` 或 `main/stages3/`。Docker 服务可能被 stages3
   占用（如 anoxpepred、algpred2），启动前先做 health check。

2. **Docker 服务竞争** — 如果 stages3 的 Step 1 正在使用 anoxpepred 和 algpred2，stages2 的
   Round 1 会与之冲突。建议等 stages3 Step 1 完成后再启动 stages2，或者确保服务实例独立。

3. **GPU 显存** — 如果 stages3 启用了 GPU 服务（如 bepipred3、temstapro），stages2 的 Rounds 3/5
   可能遇到显存不足。执行前用 `nvidia-smi` 检查显存使用情况。

4. **Bottom-N 不是"坏肽"** — Bottom-N 肽在所有安全维度上都通过了阈值，只是 AnOxPePred 得分
   最低。它们是安全的对照，不是"失败"的肽。

5. **construct 级 re-score 增加时间** — Round 4 新增的 AnOxPePred + BepiPred3 全长评分
   会增加约 2-5 分钟，但 ≤100 个 construct 的量级可接受。
