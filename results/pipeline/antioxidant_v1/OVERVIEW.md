# 抗氧化方向 · 总览速览版 (antioxidant_v1)

> 一页看完：流程图、关键数字、最重要限制、行动建议。
> 详细文档：`src/pipeline/docs/antioxidant_v1/` （三个分文件）。
> 结果日期：2026-09-05。耗时：8 秒。

---

## 1. 一句话

2,025 万肽 → 4 轮筛选 → **Top 113 + Bottom 57 = 170 条 construct 落库**，全部通过安全一票否决；最大盲区是免疫评估（MHC-II 完全缺失）。

## 2. 流程图

```
2,025 万肽
  │
  │  R1 功能预筛 (aopxsvm ≥ P90 = 0.99584)
  ▼
2,024,889 (top 10%)
  │
  │  R2 安全一票否决 (tox / hemo / mhci, 可空缺)
  ▼
170 (top 113 + bot 57)        ← 80 条被安全剔除
  │
  │  R3 加权综合分 (4 分量 × Winsorized std 权重 × 4 递送)
  ▼
170 条 construct
  │
  │  R4 落 constructs 表
  ▼
igem_peptides.constructs (Top 113 + Bottom 57)
```

## 3. 关键数字

| 项 | 数值 |
|---|---|
| 库大小 | 20,248,885 肽 |
| 候选池 | 2,024,889 肽（全库 P90+） |
| Top 通道落库 | **113** / 150 |
| Bottom 通道落库 | **57** / 100 |
| Top aopxsvm 中位数 | 1.000 |
| Bot aopxsvm 中位数 | 0.996 |
| composite_topical 范围 | [0.186, 0.786] |
| composite_injection 范围 | [0.185, 0.661] |
| 端到端耗时 | **8 秒** |
| 综合分权重 (cpp/sens/epitope/agg) | 0.31 / 0.30 / **0.00** / 0.39 |

`epitope = 0` 是因为 BepiPred-3.0 在 DB 里完全缺失——权重自动归零，符合"数据驱动权重"原则。

## 4. 工具覆盖：7 个一致、2 个缺失

| 工具 | 提案预期 | 实测 | 影响 |
|---|---|---|---|
| AOPxSVM (主排序) | full | ✅ full | — |
| ToxinPred3 (硬门控) | full | ✅ full | — |
| AlgPred2 (致敏) | full | ✅ full | — |
| pLM4CPPs (穿膜) | full | ✅ full | — |
| anoxpepred-frs (辅) | 500K | ✅ full（已派生） | 更好 |
| HemoPI2 (溶血) | 500K | ✅ 500K | 97.5% 肽未评估 |
| MHCflurry (MHC-I) | 5–15aa | ✅ 135K | 99.3% 肽未评估 |
| **netmhcipan_pctrank** (MHC-II) | 14.8M | ❌ **0 行** | 长肽免疫评估失效 |
| **bepipred** (B 细胞表位) | full | ❌ **0 行** | 权重自动归零 |

## 5. 最关键的两个限制

### 限制 1：免疫评估的盲区

- MHCflurry 受长度限制只能覆盖 0.7%（5–15 aa）
- NetMHCIIpan 完全缺失
- **后果**：长肽的免疫原性几乎都是"未评估"，注射场景的免疫收紧到 0.35 **只对短肽生效**
- **建议**：所有注射候选在湿实验阶段**必须**做 T 细胞增殖实验

### 限制 2：B 细胞表位缺失

- BepiPred-3.0 完全缺失
- **后果**：候选肽的抗体识别能力没人评过；权重自动归零，**综合分里完全没有 B 细胞表位贡献**
- **建议**：外用 / 伤口敷料场景（候选蛋白接触体液）应单独做 ELISA 验证

## 6. 行动建议（按优先级）

| 优先级 | 行动 | 估算时间 |
|---|---|---|
| 高 | 补跑 netmhcipan_pctrank 全 20M | 1–2 周 |
| 高 | 补跑 bepipred 全 20M | 1–2 周 |
| 中 | 补跑 hemopi2 全 20M | 1 周 |
| 中 | 湿实验验证 Top 10 候选（溶血 + 致敏 + 免疫） | 4–6 周 |
| 中 | 构造器服务（场景驱动 backbone + linker） | 2–3 周（独立项目） |
| 低 | 放宽 P90 → P85，让 Top/Bottom 填满 250 条 | 0（参数调整） |
| 低 | 每周 cron vacuum peptide_enrichment（避免 30 min 退化） | 0（运维） |

## 7. 查看结果

```bash
# 总数与通道分布
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -c "SELECT channel, status, count(*) FROM constructs
        WHERE direction='antioxidant' GROUP BY channel, status"

# 完整自检 (5 段)
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -f src/setup/sql/13_antioxidant_self_check.sql

# Top 5 速览
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT id, rank, peptide_id,
       (scores->>'aopxsvm')::float8              AS aopxsvm,
       (scores->>'toxinpred3')::float8           AS tox,
       (delivery_scores->>'topical')::float8     AS d_topical,
       (delivery_scores->>'injection')::float8   AS d_injection
FROM constructs WHERE direction='antioxidant' AND channel='top'
ORDER BY rank NULLS LAST LIMIT 5"
```

## 8. 详细文档

| 文件 | 受众 |
|---|---|
| `src/pipeline/docs/antioxidant_v1/01_各工具实际情况.md` | 工具核对（9 个工具逐一对照） |
| `src/pipeline/docs/antioxidant_v1/02_工程实现方法与结果.md` | 工程实现细节、SQL 重写、性能、可重现命令 |
| `src/pipeline/docs/antioxidant_v1/03_筛选流程总体说明.md` | 生物学汇报版（无代码） |
| `top10.csv` | Top 10 (功能分最高，含 9 项分数 + 4 种递送综合分) |
| `bottom10.csv` | Bottom 10 (功能分最低，作为对照) |
| `top10_bottom10.csv` | Top 10 + Bottom 10 同一张表，方便对比 |

## 9. 重跑命令

```bash
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/12_create_constructs.sql
python3 scripts/pipeline/run_antioxidant.py
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/13_antioxidant_self_check.sql
```

幂等：重跑只更新分数、不重复插入。