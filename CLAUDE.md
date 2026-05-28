# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

iGEM-platform 是一个**丝素蛋白融合功能肽设计平台**，通过枚举法在丝素蛋白骨架上融合功能肽，筛选最优嵌合体用于护肤/抗氧化/抗黑色素等功效。

核心流程：
1. 从 ~100 万条功能肽候选开始
2. 多轮评分（轻→中→重，逐轮缩小候选池）
3. 与丝素蛋白骨架 + Linker 穷举组合
4. 3D 结构预测（ESMFold / OmegaFold / AlphaFold3）
5. PDB 评估（SASA 暴露度 / Aggrescan3D 聚集风险）
6. 生成最终排名报告

## 常用命令

### 运行 Pipeline 阶段

```bash
# Round 1：轻量评分（AnOxPePred + AlgPred2，105万条 → Top 50K）
uv run python -m main.stages2.round01_lightweight

# Round 2：中等评分（+ HemoPI2 + MHCflurry，50K → Top 5K）
uv run python -m main.stages2.round02_scoring

# Round 3：重服务评分（+ BepiPred-3.0，5K → Top 80）
uv run python -m main.stages2.round03_heavy

# Stage 4：枚举 + Construct 评分
uv run python -m main.stages2.round04_enumerate

# Stage 5：3D 结构预测（ESMFold + OmegaFold）
uv run python -m main.stages2.round05_3d

# Stage 6：PDB 评估 + 最终报告
uv run python -m main.stages2.round06_pdb_eval

# Stage 7：最终整合
uv run python -m main.stages2.round07_final
```

### 微服务管理

所有微服务独立运行，默认监听 `127.0.0.1` 不同端口（见 `main/config.py`）：

| 服务 | 端口 | 用途 |
|------|------|------|
| anoxpepred | 8001 | 抗氧化评分 |
| toxinpred3 | 8003 | 毒性过滤 |
| hemopi2 | 8004 | 溶血过滤 |
| bepipred3 | 8002 | B细胞表位评分 |
| alphafold3 | 8201 | 3D结构预测 |
| esmfold | 8203 | 3D结构预测 |
| sasa | 8101 | PDB评估（溶剂可及表面积）|
| aggrescan3d | 8102 | PDB评估（聚集风险）|

### 输出目录

Pipeline 输出在 `output2/` 下，按阶段组织：
```
output2/
├── round01_lightweight/   # Top 50K + 安全标记
├── round02_*/              # Top 5K
├── round03_*/              # Top 80
├── stage04_enumerate/      # construct 枚举结果
├── stage05_3d/             # PDB 文件
└── stage06_pdb_eval/       # 最终排名
```

每个阶段目录包含：`run.log`、各服务的原始分数 JSON、最终输出 CSV。

## 架构设计

### 三套 Pipeline（历史演进）

| 目录 | 状态 | 说明 |
|------|------|------|
| `main/stages/` | 已废弃 | 早期方案，参考价值低 |
| `main/stages2/` | **当前生产** | 推荐使用，完整 7 轮流程 |
| `main/stages4/` | 开发中 | 最新迭代版本 |

当前开发应基于 `stages2/` 的设计。

### 漏斗设计原则

**倒金字塔轻重分层**（PLAN.md 核心原则）：
- 吞吐量高的轻量服务（2000条/秒）放前面，过滤掉 70-80% 候选
- 重量服务（50条/秒的 BepiPred）只跑在剩余 5K 条上
- 3D 结构预测是瓶颈，用缓存避免重复计算

**Round 1 → 50K → Round 2 → 5K → Round 3 → 80 → 枚举 → 100 construct → 3D**

### 评分权重体系

```
综合分 = AnOxPePred×0.50 + ToxinPred3×0.15(反向) + AlgPred2×0.10(反向)
       + HemoPI2×0.10(反向) + MHCflurry×0.05(反向) + BepiPred×0.10
```

安全维度（ToxinPred3/AlgPred2/HemoPI2/MHCflurry）**反向计分**：分数越高，归一化后越低，排名越靠后。

### Construct 枚举结构

融合蛋白固定结构：
```
[功能肽] + [Linker] + [丝素核心] + [His6]        # N端
[丝素核心] + [Linker] + [功能肽] + [Linker] + [His6]  # C端
[功能肽] + [Linker] + [丝素核心] + [Linker] + [功能肽] + [Linker] + [His6]  # 两端
```

枚举策略：Top 20 肽 × 2 Linker × 3 位置 = 120 construct，按 (肽,Linker) 分组取最优。

### 显存管理

单 GPU 48GB 分时复用策略：
- 阶段1-3：轻量服务并行（~7 GB）
- 阶段5（ESMFold）：~8 GB × 3并发 = 24 GB，需关停其他 GPU 服务
- AlphaFold3 需独占，不与任何 GPU 服务共存

## 关键文件

| 文件 | 作用 |
|------|------|
| `main/config.py` | 微服务地址/端口配置，唯一的配置控制面板 |
| `main/client.py` | ServiceClient 类，统一 HTTP 调用微服务 |
| `main/data_loader.py` | 数据加载（FASTA/CSV），路径 `data/` |
| `main/stages2/common.py` | 共享工具函数（日志/统计/checkpoint） |
| `PROGRAM 0.md` | 项目背景、方向探讨、开放问题 |
| `IDEA.md` | Pipeline 设计原则、漏斗策略、架构细节 |
| `main/stages2/PLAN.md` | 完整漏斗设计文档 |
| `main/stages2/PLAN2.md` | 第二轮复盘重构方案 |

## 数据格式

### FASTA 格式

```
>species|protein|domain
AAAGGGRWRWRW...
```

### CSV 格式（function.csv）

关键列：`sequence`, `is_antioxidant`, `is_antimicrobial`, `is_antiglycation`, `source_name`, `database_id`

### 微服务返回

统一 JSON 格式：`{"results": [{"id": "...", "score": float, ...}, ...]}`

## 开发注意事项

1. **不要直接运行 `python -m main`**：`__main__.py` 抛出 `NotImplementedError`，流水线需分阶段运行

2. **重试与断点续跑**：每阶段有 `checkpoint.json`，崩溃后可从上次位置恢复

3. **安全标记系统**：不设硬过滤阈值，用 `safe/caution/danger` 三级标记，通过反向计分自然淘汰高危肽

4. **缓存策略**：AlphaFold/ESMFold 输出的 PDB 用序列 SHA256 哈希值缓存，相同序列不重复计算

5. **Docker 服务**：Aggrescan3D 和 AlphaFold3 通过 Docker 运行，需确保 Docker daemon 可用