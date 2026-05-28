# Pipeline 工程原则

<!-- 本文档阐述 main/ 目录下管线代码的设计原则、工程美学和最佳实践。 -->

---

## (a) 微服务调用的方法

### 1. 三大服务模板

所有微服务遵循三种接口模板，`ServiceClient` 对应提供三种调用方式：

| 模板 | 输入 | 输出 | 调用方法 |
|------|------|------|----------|
| FASTA 服务 | `sequence` (str) | `score` (float) | `predict_single()` / `predict_batch()` |
| Structure 服务 | `sequence` (str) | `pdb_content` (str) | `predict_structure_async()` (job 轮询) |
| PDB 评分服务 | `pdb_content` (str) + `sequence` (str) | `score` (float) | `predict_pdb_single()` / `predict_pdb_batch()` |

每个服务只属于一个模板。不要在业务逻辑中猜测服务接口——查 `services.json` 的 `type` 字段。

### 2. 批量调用优先

- 默认使用 `predict_batch()`，除非服务强制单条发送
- 例外：ToxinPred3（sklearn `ExtraTreesClassifier` 线程不安全，只能单条）
- batch 大小是**性能关键参数**，需要在吞吐和超时之间平衡

### 3. Semaphore 控制并发

每个服务的并发数在代码中显式声明为 `asyncio.Semaphore(N)`。N 的取值取决于：

- **服务类型**：GPU 服务需考虑显存限制（TemStaPro → 低并发；pLM4CPPs → 可高并发）
- **线程安全**：CPU 服务中 sklearn/skrebayes 的 C 扩展可能线程不安全 → Semaphore(1-2)
- **实例数**：多容器部署时每个容器独立 Semaphore（跨进程安全）

Semaphore 的默认值服务于**吞吐 × 稳定性的平衡点**，通过实际压测确定。

### 4. 避免 127.0.0.1（Docker bridge IP）

访问 Docker 容器时**绝对不要用 `127.0.0.1:PORT`**。docker-proxy 转发会导致 httpx 间歇性 keep-alive 挂起。

正确做法：`docker inspect` 获取 bridge IP：

```python
def _fix_docker_network(service_name: str) -> str:
    import docker
    client = docker.from_env()
    container = client.containers.get(service_name)
    network = container.attrs["NetworkSettings"]["Networks"]
    bridge = list(network.values())[0]
    return bridge["IPAddress"]
```

替代方案：如果主机能直接访问容器端口且无 keep-alive 问题，可以用 127.0.0.1（需要验证）。

### 5. 健康检查前置

任何评分步骤开始前调用 `ensure_services()` 确认依赖服务就绪。这避免了"跑了一小时发现服务挂了"的灾难。

```python
health = ensure_services(required_services, profiles=["gpu"], timeout=180.0)
unavailable = [s for s, h in health.items() if not h["available"]]
if unavailable:
    sys.exit(1)  # 绝不继续
```

### 6. asyncio.gather 必须带 return_exceptions=True

否则一个协程抛出异常→所有协程被取消。这在批量处理中是毁灭性的。

```python
# ❌ 错误：一个失败全部取消
results = await asyncio.gather(*tasks)

# ✅ 正确：异常以返回值形式返回
results = await asyncio.gather(*tasks, return_exceptions=True)
for r in results:
    if isinstance(r, Exception):
        continue  # 宽容处理，不阻塞整体流程
```

### 7. 服务不可知调用

`ServiceClient` 对服务名透明——`predict_batch("hemopi2", items)` 和 `predict_batch("sodope", items)` 走同一套代码。服务地址和端口只由 `config.py` 管理，业务逻辑无需关心。

```python
# config.py 是唯一的参数控制面板
SERVICES["hemopi2"]   = {"port": 8004}
SERVICES["sodope"]    = {"port": 8012}

# 运行时可通过环境变量覆盖
# export HEMOPI2_HOST=192.168.1.50
```

---

## (b) 平衡资源与速度

### 1. CPU 先行，GPU 按重量排队

GPU 显存是 Pipeline 最稀缺的资源（49GB，但单模型可占 20GB+）。

安排服务顺序的规则：

1. **CPU 服务先跑**——SoDoPE、TIPred 等 CPU 服务对 GPU 无竞争，快速出结果
2. **GPU 服务由轻到重**——pLM4CPPs（~2GB）→ GraphCPP（~2GB）→ BepiPred3（~6GB）→ TemStaPro（~20GB）
3. **同一时间只有一个 GPU 服务加载模型**——避免显存竞争导致模型加载 OOM

### 2. 空闲资源利用（预打分）

当管线因 CPU 瓶颈（如 ToxinPred3 跑 16h）阻塞时，GPU 完全空闲。这时应该启动**预打分**——利用空闲 GPU 提前计算后续阶段的分数。

核心模式：

```
CPU 瓶颈服务（串行）  ←-- 管线等待
    ↓
GPU 空闲 → 预打分脚本 → roundN_scores 表（幂等写入）
    ↓
CPU 瓶颈完成 → 主管线继续 → 检查到预打分数据已完成 → 跳过评分阶段
```

预打分的"浪费"（部分候选最终被淘汰，预打分数据废弃）远小于"GPU 空闲等待"的损失。

### 3. 并发数不是越大越好

并发数存在两个硬约束：

- **显存上限**：单个 GPU 服务在同一时间加载一个模型。Semaphore(N) 导致 N 个请求排队，不增加显存压力（模型已加载），但请求堆积过多会导致超时。N 的经验值：`timeout / 单请求耗时 × 0.8`。
- **线程安全**：sklearn 的 C 扩展（`ExtraTreesClassifier`、`RandomForest`）在多线程下挂起。Semaphore(1) 或 Semaphore(2) 是安全上限。多容器 = 多进程 = 安全。

### 4. 多实例破解服务端单线程限制

对于线程不安全的服务（ToxinPred3），单进程 Semaphore(1) 的吞吐太低。解法：部署多个 Docker 容器实例，每个实例独立进程，客户端按候选块分配（round-robin 或分片）。

```python
# 多实例 = 多进程 = 物理隔离的 sklearn = 安全
TOXIN_INSTANCES = ["toxinpred3", "toxinpred3-2", "toxinpred3-3"]
for idx, inst in enumerate(TOXIN_INSTANCES):
    start = idx * per_instance
    # 每个实例负责独立的候选子集
    instance_batches.append((inst, all_candidates[start:end]))
```

每个实例内部仍然使用 Semaphore(2) 保守控制。这样吞吐等于 `N_实例 × 单实例吞吐`。

### 5. 按需启动服务

启动所有 16 个服务会浪费 GPU 显存（PyTorch CUDA context 占 ~GB 级）。每个 round 只启动需要的服务：

- Round 1: anoxpepred + algpred2（2 个）
- Round 2: toxinpred3 × 3 + hemopi2 + mhcflurry（5 个）
- Round 3: bepipred3 + temstapro + sodope + plm4cpps + graphcpp（5 个）

Docker Compose 的 `--profile` 机制天然支持。`s4_service_map.py` 记录了每个 round 的依赖。

---

## (c) 分批保存、容灾性、鲁棒性

### 1. 永不全部收集在内存中

这是管线最核心的数据安全原则。

每批数据打分完成后**立即写入数据库**，不积累。写入使用 `ON CONFLICT DO UPDATE`（幂等），同一行可被不同轮次写入安全更新。

```python
# ❌ 错误：所有分数收集在内存，最后一锤子写入
results = []
for chunk in chunks:
    scores = await score_chunk(chunk)
    results.extend(scores)
db.write_all(results)  # 跑了 16h 后崩溃 → 全部丢失

# ✅ 正确：每批打完立即写入
for chunk in chunks:
    scores = await score_chunk(chunk)
    db.insert_scores(scores)  # ON CONFLICT DO UPDATE，幂等
```

### 2. 先持久快数据

耗时差异大的多个服务并行时，快的先出结果、先落地。慢的服务即使崩溃，快的服务结果不丢。

例如 Round 2 的安全筛检：
1. HemoPI2 / MHCflurry（~分钟级）→ **立即写入 140 万行**
2. ToxinPred3（~16 小时）→ 每 200 条写一次

只要 HemoPI2/MHCflurry 写入了，后续 ToxinPred3 全部重跑也只损失 16h，不损失已经完成的 hemo/mhc 数据。

### 3. 幂等写入（ON CONFLICT DO UPDATE）

所有 `insert_*` 方法使用 `INSERT ... ON CONFLICT (candidate_id) DO UPDATE SET ...`。这意味着：

- 同一候选可被多次写入
- 不同轮次写入不同字段互不覆盖
- 重复执行不会产生重复行

这是预打分、断点续跑、崩溃恢复的基础。

### 4. Checkpoint + Resume

每阶段写 checkpoint，记录 `round`、`step`、`status`、`processed_items`。脚本启动时先检查：

```python
if get_checkpoint(round, step) == "done":
    log("已跳过（上次已完成）")
    continue  # 或根据 processed_items 决定从哪里继续
```

Checkpoint 支持两种恢复粒度：
- 服务级恢复（跳过已完成的服务）
- 候选级恢复（从上次处理的 candidate_id + 1 继续）

### 5. Snapshot 破单写锁

DuckDB 同一时刻只允许一个写连接。如果 Round 2 持有写锁，Round 3 预打分脚本无法读 pipeline.db。

解法：创建 snapshot：

```python
import shutil
snapshot = pipeline_db.with_name(f"pipeline_{ts}.db")
shutil.copy2(pipeline_db, snapshot)
# 从 snapshot 读取（独立文件，无锁）
# 写入 precompute.db（独立文件，不与 Round 2 竞争）
```

预打分结果写入**独立的 precompute.db**，Round 2 完成后通过 `--merge` 合并回 pipeline.db。

### 6. 最小损失窗口

Mini-batch 大小决定崩溃时的最大数据丢失量。合理选择：

| 服务 | Mini-batch | 最大丢失 | 考量 |
|------|-----------|---------|------|
| ToxinPred3 | 200 条 | ~3 分钟 | 单条 ~1s，200 条刚好一批 gather |
| HemoPI2 | 一次全部 | 0（写入后才继续） | 极快，无需分 mini-batch |
| 深度评分 | 1000 条 | ~2 分钟 | batch 天然边界 |

原则：mini-batch 大小 = 你愿意在崩溃时重新计算的最大工作量。

### 7. Exception 宽容

单个请求失败**不中断整体流程**。在 asyncio.gather 的返回值中检查 Exception 实例，遇到失败对应字段写 NULL 继续。等所有评分完成后，如果需要可以重试 NULL 项。

```python
scored = await asyncio.gather(*tasks, return_exceptions=True)
for c, s in zip(mini, scored):
    if isinstance(s, Exception):
        continue  # 写 NULL，不阻塞
    # 正常处理
```

### 8. 分离数据库

不同阶段使用独立的 DuckDB 文件，避免锁竞争：

| 数据库 | 用途 | 谁写入 |
|--------|------|--------|
| `pipeline.db` | 主管线状态 | Round 1-7 顺序执行 |
| `precompute.db` | 预打分暂存 | 预打分脚本（与 Round 2 并行） |
| (snapshot) | 只读快照 | cp 命令创建，被预打分读取 |

主管线按顺序执行时 `pipeline.db` 足够。只有需要在 CPU 瓶颈期并行预打分时才需要分离数据库。

---

## 原则的优先级

当原则之间冲突时，按此优先级裁决：

1. **数据不丢 > 速度** —— 宁可慢，不可丢。分批写入 > 一次写入性能更好但存在数据损失风险的方案
2. **幂等 > 特殊优化** —— 让写入可重复执行比"高性能但需小心处理边界"更安全
3. **显式配置 > 隐式假设** —— Semaphore 值、batch 大小、threshold 在代码头部显式声明
4. **简单正确 > 复杂高效** —— 顺序执行但正确的流程 > 并行但可能出错的设计

---

*本文档随管线经验持续更新。每条原则背后都有一次线上事故或长达数小时的调试作为代价。*
