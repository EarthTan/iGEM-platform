# 系统架构

> **文档编号**: 02
> **抽象层级**: 系统架构 / 模块边界 / 数据流
> **状态**: v2（根据代码审查更新）
> **更新说明**: 修正 SSE 实现为 cursor-based；增加 job_rounds 表；明确 dual-database per-job 架构；更新文件系统结构

---

## 一、整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                          Browser                                 │
│  React 18 + Vite + React Router v6 + React Query + NGL Viewer   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP (REST) + WebSocket
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend (:8000)                      │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────────┐  │
│  │ Auth API  │  │ Jobs API   │  │Results API│  │ Configs API │  │
│  │ /api/auth │  │ /api/jobs  │  │/api/rslts │  │ /api/cfgs   │  │
│  └───────────┘  └───────────┘  └───────────┘  └─────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
┌──────────────────┐  ┌──────────────┐  ┌──────────────────────┐
│  Pipeline        │  │ PostgreSQL   │  │  File System         │
│  Scheduler       │  │              │  │  (只读)              │
│  (独立进程)       │  │ users        │  │  output4/           │
│                  │  │ jobs         │  │    pipeline.db      │
│  ┌────────────┐  │  │ configs      │  │    pdb/             │
│  │ subprocess │  │  └──────────────┘  │    reports/        │
│  │ executor   │  │                      └──────────────────────┘
│  └────────────┘  │                      │
│  ┌────────────┐  │
│  │ job queue  │  │                      注意：
│  │ listener  │  │                      pipeline.db 是 DuckDB
│  └────────────┘  │                      (stages4产出，平台只读)
│  ┌────────────┐  │
│  │ progress   │  │
│  │ reporter   │  │
│  └────────────┘  │
└──────────────────┘
          │
          ▼ subprocess (uv run python -m main.stages4.s4_round*)
┌──────────────────────────────────────────────────────────────────┐
│                    stages4 Pipeline Scripts                        │
│  s4_round01_antioxidant_split.py  ──▶  s4_round02_safety_screen  │
│  ──▶  s4_round03_deep_scoring  ──▶  s4_round04_enumerate        │
│  ──▶  s4_round05_3d  ──▶  s4_round06_pdb_eval                  │
│  ──▶  s4_round07_final                                        │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼ 只写
                    output4/pipeline.db + pdb/
```

---

## 二、模块职责

### 2.1 FastAPI Backend

**职责**：
- 提供 RESTful API（Auth / Jobs / Results / Configs）
- 管理 HTTP 连接生命周期
- **不**直接运行 Pipeline，只与 PostgreSQL 和 Scheduler 交互

**边界**：
- 不写 `output4/` 文件
- 不调用微服务 HTTP API
- 不管理 Pipeline 进程

**子模块**：

| 子模块 | 路由前缀 | 职责 |
|--------|----------|------|
| Auth API | `/api/auth` | 注册/登录/JWT 签发/Token 刷新 |
| Jobs API | `/api/jobs` | 创建任务/查询状态/中断/历史 |
| Results API | `/api/results` | 读取排名数据/漏斗数据/construct 详情 |
| Configs API | `/api/configs` | 配置 Preset CRUD/公开分享 |

### 2.2 Pipeline Scheduler（独立进程）

**职责**：
- 常驻进程，监听 PostgreSQL `jobs` 表的 `pending` 状态
- 按 round 顺序执行 Pipeline 脚本（subprocess）
- 管理 GPU 资源（确保同时只有一个 Pipeline 使用 GPU）
- 向 PostgreSQL 写入每个 round 的进度（供 WebSocket 推送）
- 支持断点续跑（复用 `stages4` 现有的 checkpoint 机制）

**与 FastAPI 的边界**：
- FastAPI **不**管理任何 Pipeline 进程
- Scheduler **不**处理 HTTP 请求
- 两者通过 PostgreSQL 解耦

**状态机**：

```
PENDING → RUNNING (round01) → RUNNING (round02) → ... → RUNNING (round07) → COMPLETED
                │                 │
                ▼                 ▼
            FAILED             PAUSED (用户中断)
```

**进度上报机制**：
- Scheduler 在每个 round 完成后，向 `jobs` 表写入 `current_round`、`processed_count` 等字段
- 同时向 `job_events` 表插入事件记录（WebSocket 轮询或发布订阅）
- 前端 React Query 轮询 `/api/jobs/:id` 或通过 WebSocket 订阅

### 2.3 PostgreSQL Schema（核心表）

```sql
-- 用户表
users (
  id          UUID PRIMARY KEY,
  email       VARCHAR UNIQUE NOT NULL,
  password_hash VARCHAR NOT NULL,
  created_at  TIMESTAMP DEFAULT now()
)

-- 任务表（Phase 1 核心）
jobs (
  id              UUID PRIMARY KEY,
  user_id         UUID REFERENCES users(id),
  config_id       UUID REFERENCES configs(id),
  name            VARCHAR(255),
  status          VARCHAR(50) DEFAULT 'pending',
  -- pending / running / completed / failed / paused
  current_round   VARCHAR(50),
  processed_count BIGINT DEFAULT 0,
  total_count     BIGINT,
  created_at      TIMESTAMP DEFAULT now(),
  started_at      TIMESTAMP,
  completed_at    TIMESTAMP,
  error_message   TEXT,

  -- 该 job 独立的输出目录（per-job 隔离）
  output_path     VARCHAR(500) NOT NULL,
  -- 原始配置快照（完整 JSON，用于驱动 round 脚本）
  config_snapshot JSONB NOT NULL
)

-- 任务 Round 状态表（新增，用于 per-round checkpoint 和断点续跑）
job_rounds (
  id              UUID PRIMARY KEY,
  job_id          UUID REFERENCES jobs(id) ON DELETE CASCADE,
  round_name      VARCHAR(50) NOT NULL,
  status          VARCHAR(50) DEFAULT 'pending',
  -- pending / running / completed / failed
  started_at      TIMESTAMP,
  completed_at    TIMESTAMP,
  checkpoint_data JSONB,
  UNIQUE(job_id, round_name)
)

-- 任务事件表（用于 SSE 推送，必须有主键支持 cursor 翻页）
job_events (
  id        UUID PRIMARY KEY,
  job_id    UUID REFERENCES jobs(id) ON DELETE CASCADE,
  round     VARCHAR(50),
  event     VARCHAR(50),
  -- job_created / round_started / round_completed / job_completed / job_failed
  message   TEXT,
  timestamp TIMESTAMP DEFAULT now()
)

-- 配置方案表
configs (
  id           UUID PRIMARY KEY,
  user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
  name         VARCHAR NOT NULL,
  is_public    BOOLEAN DEFAULT false,
  config_data  JSONB NOT NULL,
  created_at   TIMESTAMP DEFAULT now(),
  updated_at   TIMESTAMP DEFAULT now()
)

-- 索引
CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_job_rounds_job_id ON job_rounds(job_id);
CREATE INDEX idx_job_events_job_id ON job_events(job_id);
CREATE INDEX idx_job_events_timestamp ON job_events(job_id, timestamp);
CREATE INDEX idx_configs_user_id ON configs(user_id);
```

**注意**：`jobs` 表的 `output_path` 指向该任务独立的输出目录。

### 2.4 文件系统结构

```
$PROJECT_ROOT/
├── main/                      # 共享 pipeline 代码（不修改）
│   ├── stages4/
│   │   ├── s4_round*.py
│   │   ├── s4_db.py
│   │   └── s4_docker_utils.py
│   ├── client.py
│   └── config.py
├── tools/                     # 微服务 docker-compose
│
├── output4/                   # 全局 pipeline 输出（只读）
│   ├── shared/
│   │   └── candidates.db      # ~2.6GB，全局共享只读候选库
│   ├── pipeline.db           # ⚠️ 已废弃：全局共享文件，多 job 并发写入会冲突
│   └── pdb/
│
├── jobs/                      # ⚠️ 已废弃：per-job 输出目录移至 output4/jobs/
│
├── platform/                  # 平台专用目录
│   ├── backend/               # FastAPI 后端
│   ├── scheduler/             # Pipeline Scheduler 独立进程
│   └── frontend/             # React 前端
└── docker-compose.yml        # 平台整体编排（含 PostgreSQL）
```

**Per-Job 隔离目录**（由 Scheduler 在创建 job 时初始化）：

```
output4/
├── shared/
│   └── candidates.db          # read-only for all jobs (通过共享 volume)
└── jobs/
    └── {job_id}/               # 每个 job 独立目录
        ├── pipeline.db         # per-job DuckDB（独立文件，无并发冲突）
        ├── pdb/                # 该 job 的 PDB 文件
        ├── reports/           # 各 round 报告
        ├── final/             # 最终结果 CSV
        └── checkpoint.json     # per-job checkpoint（断点续跑）
```

**读写分离原则**：
- stages4 round 脚本：**只写** `output4/jobs/{job_id}/`
- FastAPI：**只读** `output4/jobs/{job_id}/` 下的文件（通过 DuckDB 只读连接或 CSV 解析）
- `shared/candidates.db`：**所有 job 只读**（通过共享 Docker volume）

---

## 三、数据流

### 3.1 创建任务的完整流程

```
1. 用户在前端配置参数，点击「运行」
2. 前端 POST /api/jobs { config_data }
3. FastAPI:
   a. 生成 job_id (UUID)
   b. 创建 per-job 输出目录：output4/jobs/{job_id}/
   c. 写入 jobs 表 { status: "pending", output_path, config_snapshot }
   d. 写入 job_rounds 表（各 round 的初始状态）
   e. 插入 job_events("job_created")
   f. 返回 { job_id, status: "pending" }
4. Pipeline Scheduler（已在监听）:
   a. SELECT FOR UPDATE 发现 jobs.status = "pending"（原子操作）
   b. 更新 jobs.status = "running", started_at = now()
   c. 注入 PIPELINE_JOB_CONFIG 环境变量，启动 round01 subprocess
   d. 等待 round 完成，写入 job_rounds + job_events
   e. round 循环直到 round07
   f. 更新 jobs.status = "completed"
5. 前端:
   a. React Query 轮询 GET /api/jobs/:id（running 时每 5s）
   b. SSE 订阅 GET /api/jobs/:id/events 实时获取事件
   c. 漏斗可视化组件根据 progress 更新条形图
```

### 3.2 Scheduler 调用 Round 脚本（带参数）

**前提**：所有 round 脚本已修改支持 `--job-id` 和 `--output` 参数（Milestone 0）。

```python
# scheduler/executor.py
import json, os
env = os.environ.copy()
env["PIPELINE_JOB_CONFIG"] = json.dumps(job["config_snapshot"])

subprocess.Popen([
    "uv", "run", "python", "-m", f"main.stages4.s4_{round_name}",
    "--job-id", job_id,
    "--output-dir", str(OUTPUT_ROOT),  # OUTPUT_ROOT = /app/output4
], env=env, cwd=PIPELINE_ROOT)
```

**Round 脚本内部处理**：
```python
# s4_round*.py 统一处理
if args.job_id:
    JOB_OUTPUT_DIR = Path(args.output_dir) / "jobs" / args.job_id
JOB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
db = PipelineDB(db_path=str(JOB_OUTPUT_DIR / "pipeline.db"))
```

### 3.3 结果数据读取流程

```
前端 GET /api/results/{job_id}/ranking
    ↓
FastAPI 根据 job.output_path 读取：
  · ranking CSV → parse_csv()
  · funnel data → duckdb.connect(output_path + "/pipeline.db", read_only=True)
    ↓
返回结构化 JSON（排名列表）
```

**注意**：结果文件的读取不经过 Scheduler，直接由 FastAPI 访问文件系统（只读）。

---

## 四、API 边界设计

### 4.1 Auth API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/auth/register` | 注册（email + password）|
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET | `/api/auth/me` | 当前用户信息 |

### 4.2 Jobs API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/jobs` | 创建新任务 |
| GET | `/api/jobs` | 列出当前用户的任务（分页）|
| GET | `/api/jobs/:id` | 任务详情 + 进度 |
| DELETE | `/api/jobs/:id` | 中断正在运行的任务 |
| GET | `/api/jobs/:id/events` | SSE/WebSocket 流式事件 |

### 4.3 Results API

| 方法 | 端点 | 描述 |
|------|------|------|
| GET | `/api/results/:job_id/ranking` | 排名列表（分页）|
| GET | `/api/results/:job_id/funnel` | 漏斗数据（每轮条数）|
| GET | `/api/results/:job_id/constructs/:id` | 单个 construct 详情 |
| GET | `/api/results/:job_id/pdb/:construct_id` | PDB 文件（静态文件）|

### 4.4 Configs API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/configs` | 创建配置方案 |
| GET | `/api/configs` | 列出我的私有配置 |
| GET | `/api/configs/public` | 公开配置市场 |
| GET | `/api/configs/:id` | 读取单个配置 |
| PUT | `/api/configs/:id` | 更新配置 |
| DELETE | `/api/configs/:id` | 删除配置 |
| POST | `/api/configs/:id/copy` | 复制配置（到我的配置）|

---

## 五、Pipeline Scheduler 内部设计

### 5.1 启动流程（正确版）

```
1. Scheduler 启动，连接 PostgreSQL
2. 进入主循环：
   SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
   （原子操作：锁定 pending job，防止多 Scheduler 实例冲突）
3. 若有 pending job：
   a. UPDATE jobs SET status = 'running', started_at = now() WHERE id = :job_id
   b. 读取 jobs.config_snapshot（JSON）
   c. 创建 output4/jobs/{job_id}/ 目录
   d. 注入 PIPELINE_JOB_CONFIG 环境变量，启动 round01 subprocess
4. 若无 pending：sleep(5) 后重试
```

**GPU 资源获取**（在启动 subprocess 前）：
```
SELECT FOR UPDATE ON jobs WHERE status = 'running'
→ 若成功获得锁，GPU_STATE = IN_USE
→ 若失败（已有 job 在运行），sleep(30) 后重试
```

**Round 执行顺序**（从 s4_service_map.py）：
```
round0 → round1 → round2 → round3 → round4 → round5 → round6 → round7
```

每个 round 完成后的动作：
1. 更新 `job_rounds` 表（`status = 'completed'`, `checkpoint_data`）
2. 向 `job_events` 表插入 `round_completed` 事件
3. 若还有下一 round：启动下一个 subprocess
4. 若已完成 round7：更新 `jobs.status = 'completed'`

### 5.2 断点续跑逻辑

```
resume 时：
1. Scheduler SELECT job_rounds WHERE job_id = :id AND status = 'completed' ORDER BY round_name
2. 取最后一个 completed round 的 checkpoint_data
3. 从下一个 round 继续执行（传给 subprocess --from-checkpoint）
4. 若 job_rounds 无 completed 记录（全新 job）：从 round0 开始
```

### 5.3 GPU 资源管理（补充）

```
GPU 状态：FREE / IN_USE

获取 GPU（SELECT FOR UPDATE 锁）：
  BEGIN
    SELECT id FROM jobs WHERE status = 'running' FOR UPDATE NOWAIT
    → 若失败（锁被占用）：GPU_STATE = WAIT, sleep(30) 重试
    → 若成功：GPU_STATE = IN_USE，执行 pipeline
  COMMIT

释放 GPU（pipeline 结束）：
  UPDATE jobs SET status = 'completed' WHERE id = :job_id
  GPU_STATE = FREE  ← 下一个 pending job 可以获取 GPU
```

### 5.4 与 stages4 现有机制的复用

| stages4 现有机制 | 平台复用方式 |
|-----------------|--------------|
| `checkpoint.json` | 断点续跑：读取 `output4/jobs/{job_id}/checkpoint.json`，传给下一个 round 的 `--from-checkpoint` 参数 |
| `s4_docker_utils.ensure_services()` | 每个 round 开始前调用，启动需要的微服务 |
| `s4_service_map.ROUND_SERVICES` | 读取服务依赖表，确定需要哪些 profile |
| `run.log` | 追加写入 job-specific 的日志文件 |
| `roundNN_final/` | 结果写入 `output4/job_id/roundNN_final/` |

---

## 六、实时推送方案（SSE cursor-based）

### 当前方案：SSE（Server-Sent Events）

原因：
- 实现比 WebSocket 简单（FastAPI 原生支持 `@router.get("/events", response_class=SSE)`）
- 单向推送（Server → Client），适合进度推送场景
- 前端用 `EventSource` API，React Query 配合 `useEffect` 订阅
- 30 分钟以上的长连接稳定（WebSocket 在某些代理下会断开）

### SSE 实现（已修正：cursor-based，防止重复推送）

**bug 修复**：原方案每一轮迭代返回**所有**事件，导致重复推送和内存泄漏。正确做法是追踪 `last_event_id`，只推送新事件。

```python
# GET /api/jobs/{job_id}/events — 正确实现
@router.get("/{job_id}/events")
async def job_events(job_id: str, db=Depends(get_db)):
    async def generate():
        last_event_id = None
        while True:
            query = db.query(JobEvent).filter(
                JobEvent.job_id == job_id
            )
            if last_event_id:
                # 只取新的事件（cursor-based）
                query = query.filter(JobEvent.id > last_event_id)
            events = query.order_by(JobEvent.timestamp).limit(50).all()

            for event in events:
                yield f"event: {event.event}\ndata: {json.dumps({...})}\n\n"
                last_event_id = event.id

            await asyncio.sleep(1)

    return EventSourceResponse(generate())
```

**要求**：`job_events.id` 必须是自增主键（UUID PRIMARY KEY 或 BIGSERIAL），以支持 `id > last_event_id` 的 cursor 条件。

### Scheduler 事件写入（直接 SQL）

Scheduler 通过**直接 SQL**（而非 API）向 `job_events` 写入事件，绕过 FastAPI：

```python
# scheduler/progress_reporter.py
def report_round_completed(job_id: str, round_name: str, message: str):
    db.execute(
        "INSERT INTO job_events (job_id, round, event, message) VALUES (?, ?, ?, ?)",
        [job_id, round_name, "round_completed", message]
    )
```

原因：Pipeline 运行中的 round 脚本本身在写 DuckDB（高并发），再通过 API 写 PostgreSQL 会增加延迟，直接 SQL 更高效。

### 未来升级到 WebSocket

如 Phase 2 需要双向通信（用户实时发指令暂停 Pipeline），改为 WebSocket，只需修改前端 `EventSource` 为 `new WebSocket()`。

---

## 七、PostgreSQL vs DuckDB 分工（per-job 隔离）

### Dual-Database 架构

| 数据库 | 用途 | 管理方 |
|--------|------|--------|
| PostgreSQL | 平台元数据（users/jobs/configs）| 平台 |
| DuckDB（per-job）| Pipeline 核心数据（candidates/constructs）| stages4，写入 |
| CSV/JSON（per-job）| 排名结果快照 | stages4，写入 |

### Per-Job 隔离原则

**原来设计的问题**：`output4/pipeline.db` 是全局共享文件，多 job 并发写入会产生文件锁冲突。

**修正后的设计**：每个 job 有独立的 DuckDB 实例：

```
output4/
├── shared/                        # 全局共享数据（stages3 预处理结果）
│   └── candidates.db              # ~2.6GB，read-only for all jobs
└── jobs/
    └── {job_id}/
        ├── pipeline.db            # 该 job 独立的 DuckDB
        ├── pdb/                   # 该 job 的 PDB 文件
        ├── reports/               # 各 round 报告
        └── final/                 # 最终结果 CSV
```

**读写分离**：
- stages4 round 脚本写 `output4/jobs/{job_id}/pipeline.db`（per-job DuckDB）
- FastAPI **只读** `output4/jobs/{job_id}/` 下的文件（不直连 DuckDB）
- `shared/candidates.db` 对所有 job 只读（通过 `COPY` 或共享 volume）

**为什么不用直连 DuckDB 查 funnel**：Funnel 数据来自 DuckDB `checkpoint` 表，FastAPI 用只读连接查询 per-job DuckDB 文件，不会与 round 脚本的写事务冲突（因为每个 job 独立文件）。

```python
# FastAPI 读取 per-job DuckDB funnel 数据（只读连接，无冲突）
def get_funnel_data(job_id: str) -> list[dict]:
    job = get_job_from_db(job_id)
    job_db_path = job.output_path  # output4/jobs/{job_id}/pipeline.db

    import duckdb
    conn = duckdb.connect(job_db_path, read_only=True)
    rows = conn.execute("""
        SELECT round_name, total_items, processed_items
        FROM checkpoint
        ORDER BY ...
    """).fetchall()
    conn.close()
    return [...]
```

### 安全边界

- `jobs.output_path` 来自 PostgreSQL，由 Scheduler 在创建 job 时写入（不可由用户控制）
- FastAPI **不**允许用户指定文件路径，只通过 `job_id` 查 `output_path`
- PDB 文件读取必须用 `safe_pdb_path()` 防止路径遍历（详见 05-implementation-plan.md [4.1]）