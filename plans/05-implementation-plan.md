# 实施计划

> **文档编号**: 05
> **抽象层级**: 实施路线图 / 任务分解 / 交付里程碑
> **状态**: v2（根据代码审查更新）
> **更新说明**: 增加 round 脚本参数化修改为第一优先任务；补充完整的 config_data schema；修正 SSE 实现

---

## 实施阶段总览

```
Phase 1 MVP
├── Milestone 0：Round 脚本参数化改造  ★ 第一优先，所有后续任务的前提
│   ├── [0.1] 修改 s4_db.py 支持 per-job DuckDB 路径
│   ├── [0.2] 修改所有 round 脚本接受 --job-id / --output 参数
│   └── [0.3] 验证参数化后的 round 脚本可独立运行
│
├── Milestone 1：基础设施
│   ├── [1.1] PostgreSQL 部署 + DB Schema（新增 job_rounds 表）
│   ├── [1.2] FastAPI 项目骨架 + Docker Compose
│   └── [1.3] Pipeline Scheduler 骨架（读取 per-job 输出）
│
├── Milestone 2：认证
│   ├── [2.1] Auth API（注册/登录/JWT）
│   └── [2.2] 前端登录/注册页面
│
├── Milestone 3：任务管理
│   ├── [3.1] Jobs API（创建/查询/中断）
│   ├── [3.2] Scheduler 集成（带参数调用 round）
│   ├── [3.3] 前端任务列表页 + 详情页
│   └── [3.4] SSE 实时进度推送（cursor-based 修正版）
│
├── Milestone 4：结果展示
│   ├── [4.1] Results API（per-job DuckDB 读取）
│   ├── [4.2] 前端排名表格 + 漏斗可视化
│   └── [4.3] 首页配置面板 + RunButton
│
└── Milestone 5：MVP 交付
    └── [5.1] 端到端联调 + Bug 修复

Phase 2 扩展
├── [6.1] Configs API（配置保存/复用/公开市场）
├── [6.2] 前端配置管理 UI
├── [6.3] 结果对比页（多 run 对比）
└── [6.4] NGL Viewer 结构浏览
```

---

## Milestone 0：Round 脚本参数化改造

> 这是 Phase 1 所有工作**真正的第一步**。现有 round 脚本不接受任何 `--job-id` / `--output` 参数，无法被 Scheduler 驱动。这整块工作是新增的，计划初始版本完全遗漏了这个最关键的依赖。

### [0.1] 修改 `s4_db.py` 支持 per-job DuckDB 路径

**文件**：修改 `iGEM-silk-main/main/stages4/s4_db.py`

**改动**：
```python
# 当前（硬编码）：
DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"

# 修改后（支持自定义路径）：
def __init__(self, db_path: str | Path | None = None) -> None:
    self.db_path = str(db_path) if db_path else str(DEFAULT_DB_PATH)
```

**幂等检查**：确保 `init_schema()` 是 `CREATE TABLE IF NOT EXISTS`，多次调用不报错。

---

### [0.2] 所有 round 脚本增加 CLI 参数

**目标文件**：每个 `s4_round*.py` 都需要增加以下参数：

```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--job-id", type=str, default=None,
                   help="平台 Job UUID（用于 per-job 输出隔离）")
parser.add_argument("--output-dir", type=str,
                   default=str(PROJECT_ROOT / "output4"),
                   help="输出根目录")
args = parser.parse_args()

# 用法：
if args.job_id:
    JOB_OUTPUT_DIR = Path(args.output_dir) / "jobs" / args.job_id
else:
    JOB_OUTPUT_DIR = Path(args.output_dir)

JOB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
db = PipelineDB(db_path=str(JOB_OUTPUT_DIR / "pipeline.db"))
```

**受影响的 round 脚本**：

| 脚本 | 额外需要参数化 |
|------|--------------|
| `s4_round00_preprocess.py` | `--job-id`, `--output-dir` |
| `s4_round01_antioxidant_split.py` | `--job-id`, `--output-dir`, `--top-pct`, `--bottom-pct` |
| `s4_round02_safety_screen.py` | `--job-id`, `--output-dir`, `--toxin-threshold`, `--hemo-threshold`, `--mhc-threshold` |
| `s4_round03_*.py` | `--job-id`, `--output-dir` |
| `s4_round04_*.py` | `--job-id`, `--output-dir` |
| `s4_round05_3d.py` | `--job-id`, `--output-dir`（PDB 输出目录改为 per-job）|
| `s4_round06_pdb_eval.py` | `--job-id`, `--output-dir` |
| `s4_round07_final.py` | `--job-id`, `--output-dir` |

**每个脚本的 `--output-dir` 影响**：
- `s4_round05_3d.py`：`PDB_DIR = JOB_OUTPUT_DIR / "pdb"`（per-job pdb 目录）
- 所有脚本：checkpoint 文件、final 目录、run.log 都写入 `JOB_OUTPUT_DIR/`

**配置文件注入**：`config_snapshot` JSON 通过环境变量传递（避免 CLI 字符串过长）：
```python
# scheduler 设置环境变量
env = os.environ.copy()
env["PIPELINE_JOB_CONFIG"] = json.dumps(config_snapshot)  # JSON 字符串
subprocess.Popen(..., env=env)

# round 脚本读取
config = json.loads(os.environ.get("PIPELINE_JOB_CONFIG", "{}"))
```

---

### [0.3] 验证参数化后的 round 脚本可独立运行

**测试命令**（在 `iGEM-silk-main/` 目录下）：
```bash
# 测试 round01 参数化
uv run python -m main.stages4.s4_round01_antioxidant_split \
  --job-id "test-job-001" \
  --output-dir ./output4 \
  --top-pct 10 --bottom-pct 1

# 验证输出到正确位置
ls output4/jobs/test-job-001/
# 应包含：pipeline.db, pdb/, reports/, final/
```

---

## Milestone 1：基础设施

### [1.1] PostgreSQL 部署 + DB Schema

**文件**：
- 创建：`platform/docker-compose.yml`（含 PostgreSQL + Backend + Scheduler + 微服务网络）
- 创建：`platform/backend/db/models.py`（SQLAlchemy ORM）
- 创建：`platform/backend/db/session.py`（async session）
- 创建：`platform/backend/db/init.sql`（完整 Schema）

**PostgreSQL Schema（已更新）**：

```sql
-- 用户表
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- 配置方案表
CREATE TABLE configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    is_public BOOLEAN DEFAULT false,
    config_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- 任务表
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    config_id UUID REFERENCES configs(id) ON DELETE SET NULL,
    name VARCHAR(255),

    status VARCHAR(50) DEFAULT 'pending',
    current_round VARCHAR(50),
    processed_count BIGINT DEFAULT 0,
    total_count BIGINT,

    created_at TIMESTAMP DEFAULT now(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    output_path VARCHAR(500) NOT NULL,
    config_snapshot JSONB NOT NULL,
    error_message TEXT
);

-- 任务 Round 状态表（新增）
CREATE TABLE job_rounds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    round_name VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    checkpoint_data JSONB,
    UNIQUE(job_id, round_name)
);

-- 任务事件表（SSE 推送用，必须有主键以支持 cursor 翻页）
CREATE TABLE job_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    round VARCHAR(50),
    event VARCHAR(50),
    message TEXT,
    timestamp TIMESTAMP DEFAULT now()
);

-- 索引
CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_job_rounds_job_id ON job_rounds(job_id);
CREATE INDEX idx_job_events_job_id ON job_events(job_id);
CREATE INDEX idx_job_events_timestamp ON job_events(job_id, timestamp);
CREATE INDEX idx_configs_user_id ON configs(user_id);
```

---

### [1.2] FastAPI 项目骨架 + Docker Compose

**文件**：
- 创建：`platform/backend/main.py`
- 创建：`platform/backend/config.py`
- 创建：`platform/backend/requirements.txt`
- 创建：`platform/backend/Dockerfile`
- 修改：`platform/docker-compose.yml`

**docker-compose.yml 关键配置**（网络隔离方案）：

```yaml
# platform/docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: igem_platform
      POSTGRES_USER: igem_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports: ["5432:5432"]
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U igem_user -d igem_platform"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks: [igem_platform]

  backend:
    build: ./platform/backend
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform
      SECRET_KEY: ${SECRET_KEY}
      # 微服务 Host（通过服务名访问同网络内的微服务）
      ANOXPEPRED_HOST: anoxpepred
      BEPIPRED3_HOST: bepipred3
      OMEGAFOLD_HOST: omegafold
      # ... 其他微服务
    depends_on: [postgres]
    volumes:
      - ../iGEM-silk-main:/app/iGEM-silk-main:ro   # pipeline 代码只读
      - pipeline_output:/app/output4:ro              # output4 只读（结果读取）
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
    networks: [igem_platform]

  scheduler:
    build: ./platform/scheduler
    environment:
      DATABASE_URL: postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform
      PIPELINE_ROOT: /app/iGEM-silk-main
      OUTPUT_ROOT: /app/output4
      CUDA_VISIBLE_DEVICES: "0"
    depends_on: [postgres]
    volumes:
      - ../iGEM-silk-main:/app/iGEM-silk-main        # 可写（调用 round 脚本）
      - pipeline_output:/app/output4
      - /var/run/docker.sock:/var/run/docker.sock   # 管理微服务容器
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
    networks: [igem_platform]

networks:
  igem_platform:
    name: igem_platform_network
    driver: bridge

volumes:
  postgres_data:
  pipeline_output:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${OUTPUT4_PATH:-../iGEM-silk-main/output4}
```

> **注意**：微服务（anoxpepred、bepipred3 等）需要在同一 `igem_platform_network` 网络中。如果微服务通过 `tools/docker-compose.yml` 管理，需在 `tools/` 目录下创建网络并连接：
> ```bash
> cd tools && docker network create igem_platform_network 2>/dev/null || true
> docker compose --network igem_platform_network up -d
> ```
> 或者使用 Docker Compose `include` 字段同时加载两个 compose 文件。

---

### [1.3] Pipeline Scheduler 骨架

**文件**：
- 创建：`platform/scheduler/main.py`
- 创建：`platform/scheduler/executor.py`
- 创建：`platform/scheduler/queue_listener.py`

**核心设计**：

```python
# queue_listener.py — 主循环
import asyncio
from db import get_pending_job

async def run():
    while True:
        job = get_pending_job()  # SELECT FOR UPDATE 保证原子性
        if job:
            await executor.run_job(job)  # 串行执行，不并发
        await asyncio.sleep(5)

# executor.py — 调用 round 脚本
def run_job(job):
    config = job["config_snapshot"]
    job_id = job["id"]
    output_path = job["output_path"]

    for round_name in ROUND_SEQUENCE:  # ["round01", "round02", ...]
        update_job_status(job_id, "running", round_name)

        # 注入 config_snapshot 到环境变量（JSON 太长不适于 CLI）
        env = os.environ.copy()
        env["PIPELINE_JOB_CONFIG"] = json.dumps(config)

        subprocess.Popen([
            "uv", "run", "python", "-m", f"main.stages4.s4_{round_name}",
            "--job-id", job_id,
            "--output-dir", str(Path(output_path).parent.parent)
        ], env=env, cwd=PIPELINE_ROOT)

        # 等待 round 完成（通过轮询进程或 checkpoint）
        wait_for_round_complete(round_name)
        insert_job_event(job_id, round_name, "round_completed", message)

    update_job_status(job_id, "completed")
```

**GPU 互斥**：Scheduler 用 `SELECT FOR UPDATE` 在 `jobs` 表上获取全局锁，确同一时刻只有一个 job 在 GPU 阶段运行。

---

## Milestone 2：认证

### [2.1] Auth API

**端点**：

| 方法 | 路由 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册（email + bcrypt 密码哈希）|
| POST | `/api/auth/login` | 登录，返回 JWT（7 天有效期）|
| GET | `/api/auth/me` | 当前用户（依赖 `get_current_user`）|

**JWT 刷新**：Phase 1 直接用 7 天过期，无 refresh token。过期后用户重新登录。

**验证必须项**（Pydantic）：

```python
class JobConfig(BaseModel):
    function_type: Literal["antioxidant", "antimicrobial", "antiglycation"]
    tier: Literal["A", "B", "C"]
    top_n: int = Field(default=10000, ge=1000, le=100000)
    linkers: list[str] = Field(default_factory=lambda: ["Flex_GGGGSx2"])
    structure_tool: Literal["omegafold", "esmfold"] = "omegafold"
    weights: dict[str, float] | None = None
    thresholds: dict[str, float] | None = None

    @field_validator("linkers")
    @classmethod
    def linkers_must_be_valid(cls, v: list[str]) -> list[str]:
        valid = {"Flex_GGGGSx1", "Flex_GGGGSx2", "Flex_GGGGSx3",
                "Rigid_EAAAKx1", "Rigid_EAAAKx2", "Helix_AEAAAKEAAAKA",
                "PAS_linker", "Silk_like_GS", "Gly_rich_GPG", "Pro_rich_PPP"}
        for linker in v:
            if linker not in valid:
                raise ValueError(f"未知的 Linker 类型: {linker}")
        return v

    @field_validator("weights", "thresholds")
    @classmethod
    def dict_values_must_be_nonnegative(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        for key, val in v.items():
            if val < 0:
                raise ValueError(f"{key} 权重/阈值不能为负数")
        return v
```

---

### [2.2] 前端登录/注册页面

**文件**：`platform/frontend/src/{pages,api,store}/`

**必须实现的状态**：

| 状态 | 条件 | UI |
|------|------|-----|
| 初始加载 | `isInitializing = true` | 全页 Spinner |
| 未登录 | `isAuthenticated = false` 且 `isInitializing = false` | 正常显示登录页 |
| 已登录 | `token` 存在且有效 | 正常访问受保护页面 |
| Token 失效 | `/api/auth/me` 返回 401 | 清除 token，显示登录页 |

```tsx
// ProtectedRoute 正确实现
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore(s => s.isAuthenticated);
  const isInitializing = useAuthStore(s => s.isInitializing);

  if (isInitializing) return <FullPageSpinner />;
  if (!isAuthenticated) return <Navigate to="/login" />;
  return <>{children}</>;
}

// App.tsx 中的 Outlet 模式（必须！）
<Route path="jobs" element={<ProtectedRoute />}>
  <Route index element={<JobsPage />} />
  <Route path=":jobId" element={<JobDetailPage />} />
</Route>
```

---

## Milestone 3：任务管理

### [3.1] Jobs API

**必须实现的端点**：

| 方法 | 路由 | 关键行为 |
|------|------|---------|
| POST | `/api/jobs` | 生成 UUID，创建 `output4/jobs/{job_id}/`，插入 `jobs` + `job_rounds` |
| GET | `/api/jobs` | 分页（`page`, `page_size`）+ 状态过滤（`status`）|
| GET | `/api/jobs/:id` | 返回当前 round + `processed_count` |
| DELETE | `/api/jobs/:id` | `status = 'paused'`，Scheduler 检测到后停止当前 subprocess |

---

### [3.2] Scheduler 集成（带参数调用 round）

**必须完成的改动**（接 Milestone 0）：
1. Scheduler 读取 `jobs.config_snapshot` JSON，提取各 round 参数
2. 通过 `PIPELINE_JOB_CONFIG` 环境变量注入（JSON 可能超过 CLI 长度限制）
3. 每 round 完成后写入 `job_rounds` + `job_events`（直接 SQL，Scheduler bypass API）
4. Round 出错时写入 `job_events(event='job_failed')` 并更新 `jobs.status='failed'`

**Round 执行序列**：

```python
ROUND_SEQUENCE = [
    "round00_preprocess",
    "round01_antioxidant_split",
    "round02_safety_screen",
    "round03_precompute",
    "round03_deep_scoring",
    "round03_phase2_graphcpp",
    "round04_enumerate",
    "round04_phase2_bepipred3",
    "round05_3d",
    "round06_pdb_eval",
    "round07_final",
]
```

---

### [3.3] 前端任务列表 + 详情页

**必须实现的 UI 状态**：

| 组件 | 状态 | 实现方式 |
|------|------|---------|
| `JobStatusCard` | 加载中 | `query.isLoading` → Skeleton Card × 3 |
| `JobStatusCard` | 空列表 | `<EmptyState variant="no-data" title="还没有任务" action="运行第一个任务" />` |
| `FunnelVisualization` | 加载中 | Skeleton 条形（6 个条，递减宽度）|
| `FunnelVisualization` | 运行中 | 实际数据条 + 估算条（虚线），当前 round 高亮 |
| `RankingTable` | 加载中 | Skeleton Rows × 10 |
| `RankingTable` | 空 | "运行完成后将显示排名" |
| `RankingTable` | 错误 | `<InlineError message={error.message} onRetry={refetch} />` |

**轮询策略（React Query）**：
```tsx
const { data: job } = useQuery({
  queryKey: ["job", jobId],
  queryFn: () => api.getJob(jobId),
  refetchInterval: (q) =>
    q.state.data?.status === "running" ? 5000 : false,
  // completed / failed 时停止轮询
})
```

---

### [3.4] SSE 实时进度推送（cursor-based 修正版）

**当前计划 bug（已修正）**：SSE 每一轮都推送**所有**事件，导致重复。

**正确实现**：

```python
# jobs/router.py — SSE 端点
from fastapi import APIRouter

@router.get("/{job_id}/events")
async def job_events(job_id: str, db=Depends(get_db)):
    async def generate():
        last_event_id = None
        while True:
            # 只查新事件（cursor-based）
            query = db.query(JobEvent).filter(
                JobEvent.job_id == job_id
            )
            if last_event_id:
                query = query.filter(JobEvent.id > last_event_id)
            events = query.order_by(JobEvent.timestamp).limit(50).all()

            for event in events:
                yield f"event: {event.event}\ndata: {json.dumps({...})}\n\n"
                last_event_id = event.id

            await asyncio.sleep(1)  # 1s 间隔，足够快的响应

    return EventSourceResponse(generate())
```

**`job_events` 表必须有自增主键**（`id UUID PRIMARY KEY`），以支持 `id > last_event_id` 的 cursor 条件。

---

## Milestone 4：结果展示

### [4.1] Results API

**per-job 输出读取**（所有路径基于 `jobs.output_path`）：

| 数据 | 来源 | 读取方式 |
|------|------|---------|
| 排名数据 | `{job_id}/round07/final/*.csv` | pandas `read_csv`（不直连 DuckDB）|
| 漏斗数据 | `{job_id}/pipeline.db` 的 `checkpoint` 表 | DuckDB SELECT（每个 job 独立 DB）|
| PDB 文件 | `{job_id}/pdb/{construct_id}.pdb` | `StreamingResponse` |

**漏斗数据正确来源**：
```sql
-- 从 per-job DuckDB 的 checkpoint 表读取（而非 CSV）
SELECT round_name, total_items, processed_items
FROM checkpoint
ORDER BY CASE round_name WHEN 'round0' THEN 0 ... END
```

**PDB 路径遍历防护**（必须实现）：

```python
def safe_pdb_path(job_id: str, construct_id: str, output_path: str) -> Path:
    safe = (Path(output_path) / "pdb").resolve()
    requested = (safe / f"{construct_id}.pdb").resolve()
    if not str(requested).startswith(str(safe)):
        raise HTTPException(403, "Invalid construct ID")
    if not requested.exists():
        raise HTTPException(404, "PDB not found")
    return requested
```

---

### [4.2] 前端排名表格 + 漏斗可视化

**Tier A/B/C ConfigPanel 完整字段定义**：

| 字段 | Tier | 类型 | 默认值 | 验证 |
|------|------|------|--------|------|
| `function_type` | A | `antioxidant \| antimicrobial \| antiglycation` | 必选 | 枚举 |
| `top_n` | B | `number` | `10000` | 1000–100000 |
| `linkers` | B | `string[]` | `["Flex_GGGGSx2"]` | 白名单 |
| `structure_tool` | B | `omegafold \| esmfold` | `omegafold` | 枚举 |
| `weights` | C | `Record<string, float>` | 各服务 `1.0` | ≥ 0 |
| `thresholds` | C | `Record<string, float>` | 合理范围 | 0–1 |

**Linker 白名单**（来自 `data/linker.fasta`）：
```
Flex_GGGGSx1, Flex_GGGGSx2, Flex_GGGGSx3,
Rigid_EAAAKx1, Rigid_EAAAKx2, Helix_AEAAAKEAAAKA,
PAS_linker, Silk_like_GS, Gly_rich_GPG, Pro_rich_PPP
```

---

### [4.3] 首页配置面板 + RunButton

**RunButton 行为**：
1. 验证 `config_data`（Pydantic，前端也要做一次校验）
2. `POST /api/jobs { config_data }`
3. 成功 → `navigate(/jobs/{jobId})`
4. 失败 → Toast 提示错误信息

---

## Milestone 5：MVP 交付

### [5.1] 端到端联调

**完整用户旅程测试**：

```bash
# 1. 注册用户
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'

# 2. 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}' \
  | jq -r '.access_token')

# 3. 触发任务（抗氧化，Tier B，TopN=10000）
curl -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "function_type": "antioxidant",
      "tier": "B",
      "top_n": 10000,
      "linkers": ["Flex_GGGGSx2"],
      "structure_tool": "omegafold"
    }
  }'

# 4. 检查任务状态（等待 1 分钟轮询）
curl http://localhost:8000/api/jobs/{job_id} \
  -H "Authorization: Bearer $TOKEN"

# 5. 检查漏斗数据
curl http://localhost:8000/api/results/{job_id}/funnel \
  -H "Authorization: Bearer $TOKEN"

# 6. 检查排名
curl "http://localhost:8000/api/results/{job_id}/ranking?page=1&page_size=20" \
  -H "Authorization: Bearer $TOKEN"
```

**验收标准**（Phase 1 交付门槛）：

- [ ] 单用户完成注册 → 配置 → 触发 → 看到漏斗进度 → 查看排名，全流程跑通
- [ ] Pipeline 完整执行到 `round07_final` 不崩溃
- [ ] SSE 每 1 秒推送新事件，无重复
- [ ] 多 job 并发时，后启动的 job 等待前一个 job 的 GPU 阶段完成（队列串行）
- [ ] PDB 文件读取有路径遍历保护

---

## Phase 2 详细任务

### [6.1] Configs API

- 配置 CRUD + 公开分享 + 复制（详见 04-backend-architecture.md § 七）
- **Phase 2 补充**：配置 JSON 的 Pydantic 严格验证（见 §5.1 审查报告）

### [6.2] 前端配置管理 UI

- 配置列表页（两个 Tab：我的 / 公开市场）
- PipelineRunnerCard 下拉"加载方案"按钮

### [6.3] 结果对比页

- 多 run Checkbox 选择 → 对比表格（热力图）
- 漏斗形状叠加显示

### [6.4] NGL Viewer 结构浏览

- RankingTable 行添加"查看结构"按钮
- StructurePage：左侧 NGL Viewer + 右侧信息面板

---

## 环境与依赖

### .env 文件模板

```bash
# platform/.env（不提交 git）
SECRET_KEY=<生成一个 256-bit 随机字符串>
POSTGRES_PASSWORD=<数据库密码>
POSTGRES_DB=igem_platform
POSTGRES_USER=igem_user
POSTGRES_PORT=5432
OUTPUT4_PATH=/path/to/iGEM-silk-main/output4
```

### 开发启动命令

```bash
# 完整启动（从 iGEM-silk-main 根目录）
cd platform
docker compose up -d --build

# 查看日志
docker compose logs -f backend
docker compose logs -f scheduler

# 触发 round 脚本测试
uv run python -m main.stages4.s4_round01_antioxidant_split \
  --job-id "test-123" --output-dir ./output4 --top-pct 10 --bottom-pct 1
```

---

## 关键风险（已更新）

| 风险 | 缓解措施 |
|------|---------|
| round 脚本不接受 `--job-id` 参数 | **Milestone 0 已在第一位**，无此问题则无法继续 |
| GPU 资源冲突 | Scheduler 用 PostgreSQL `SELECT FOR UPDATE` 全局锁，串行执行所有 job |
| SSE 重复推送 | 已修正为 cursor-based，`last_event_id` 追踪 |
| 两处 docker-compose 网络不通 | 使用 `docker compose include` 或显式创建共享网络 |
| PDB 路径遍历漏洞 | 已实现 `safe_pdb_path()` 函数 |
| config JSON 无验证 | Pydantic `field_validator` 已在 [2.1] 实现 |
| output4/ 磁盘耗尽 | Phase 2 添加定期检查和清理提醒 |