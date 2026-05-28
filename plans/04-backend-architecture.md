# 后端架构

> **文档编号**: 04
> **抽象层级**: FastAPI 后端 / 数据库 schema / API 设计
> **状态**: v2（根据代码审查更新）
> **更新说明**: 补全 config_data JSON schema；添加 Pydantic validators；修正 funnel 数据源为 DuckDB checkpoint；补充 job_rounds 表

---

## 一、技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步、类型安全、自动文档 |
| ORM | SQLAlchemy 2.0 (async) | asyncpg 驱动 |
| 数据库 | PostgreSQL | via docker-compose |
| 认证 | PyJWT | JWT token 签发/验证 |
| 密码加密 | passlib + bcrypt | |
| 任务队列 | FastAPI BackgroundTasks | Phase 1 方案 |
| 实时推送 | Server-Sent Events (SSE) | 原生 FastAPI 支持 |
| 静态文件 | FastAPI `StaticFiles` | PDB 文件服务 |
| 验证 | Pydantic v2 | 请求/响应 model |

---

## 二、项目结构（platform/backend/）

```
platform/backend/
├── main.py                  # FastAPI 实例 + 路由注册
├── config.py                 # 环境变量配置
├── requirements.txt
│
├── auth/
│   ├── router.py            # /api/auth 路由
│   ├── schemas.py           # Pydantic models
│   ├── service.py           # 登录/注册逻辑
│   └── dependencies.py      # get_current_user 依赖注入
│
├── jobs/
│   ├── router.py            # /api/jobs 路由
│   ├── schemas.py
│   ├── service.py           # Jobs 业务逻辑
│   └── dependencies.py
│
├── results/
│   ├── router.py
│   ├── schemas.py
│   └── service.py           # 读取 output4/ 结果文件
│
├── configs/
│   ├── router.py
│   ├── schemas.py
│   └── service.py
│
└── db/
    ├── session.py           # async SQLAlchemy session
    ├── models.py            # SQLAlchemy ORM models
    └── migrations/          # Alembic migrations (可选)
```

---

## 三、数据库 Schema

### 3.1 所有表（PostgreSQL）

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
    config_data JSONB NOT NULL,  -- 完整参数 JSON
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- 任务表
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    config_id UUID REFERENCES configs(id) ON DELETE SET NULL,
    name VARCHAR(255),  -- 可选，便于用户识别

    -- 状态机
    status VARCHAR(50) DEFAULT 'pending',
    -- pending / running / completed / failed / paused

    -- 进度
    current_round VARCHAR(50),
    processed_count BIGINT DEFAULT 0,
    total_count BIGINT,

    -- 时间戳
    created_at TIMESTAMP DEFAULT now(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- 错误信息（pipeline 失败时写入）
    error_message TEXT,

    -- 输出路径（该 job 独立的 output 目录）
    output_path VARCHAR(500) NOT NULL,

    -- 原始配置快照（独立存储，不依赖 configs 表）
    config_snapshot JSONB NOT NULL
);

-- 任务 Round 状态表（per-round checkpoint，支持断点续跑）
CREATE TABLE job_rounds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    round_name VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    -- pending / running / completed / failed
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    checkpoint_data JSONB,  -- 序列化 checkpoint JSON，用于断点续跑
    UNIQUE(job_id, round_name)
);

-- 任务事件表（SSE 推送用，cursor-based 查询需要主键）
CREATE TABLE job_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    round VARCHAR(50),
    event VARCHAR(50),
    -- job_created / round_started / round_completed / job_completed / job_failed / progress
    message TEXT,
    timestamp TIMESTAMP DEFAULT now()
);

-- 索引
CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_job_rounds_job_id ON job_rounds(job_id);
CREATE INDEX idx_job_events_job_id ON job_events(job_id);
CREATE INDEX idx_job_events_timestamp ON job_events(job_id, timestamp);  -- cursor-based SSE 查询
CREATE INDEX idx_configs_user_id ON configs(user_id);
```

### 3.2 config_data JSON Schema（Pydantic 定义）

```python
from pydantic import BaseModel, field_validator
from typing import Literal

class ConfigData(BaseModel):
    # ===== 基础字段（所有 Tier 必需）=====
    function_type: Literal['antioxidant', 'antimicrobial', 'antiglycation']
    tier: Literal['A', 'B', 'C']

    # ===== Tier B 字段（可选，若不提供则用默认值）=====
    top_n: int | None = 20
    linkers: list[str] | None = ["Flex_GGGGSx2"]
    structure_tool: Literal['omegafold', 'esmfold'] | None = 'omegafold'

    # ===== Tier C 字段（可选）=====
    weights: dict[str, float] | None = None
    thresholds: dict[str, float] | None = None
    round_splits: dict[str, float] | None = None  # e.g. {"round1": 0.0125, "round2": 0.02}
    channel_strategy: Literal['greedy', 'balanced', 'exhaustive'] | None = 'balanced'

    # ===== Pydantic Validators =====

    @field_validator('top_n')
    @classmethod
    def validate_top_n(cls, v: int) -> int:
        if not (1 <= v <= 250):
            raise ValueError('top_n must be between 1 and 250')
        return v

    @field_validator('linkers')
    @classmethod
    def validate_linkers(cls, v: list[str] | None) -> list[str]:
        if v is None:
            return ["Flex_GGGGSx2"]
        # 白名单验证（从 s4_round04_enumerate.py 的 LINKER_POOL 提取）
        LINKER_WHITELIST = {
            "Flex_GGGGS", "Flex_GGGGSx2", "Flex_GGGGSx3",
            "FlexGGGGS", "EAAAK", "EAAAKx2", "EAAAKx3",
            "AEAAAKEAAAKA", "Kex2", "AutoRabbit",
            "Creator1", "Creator2", "Creator3",
        }
        for linker in v:
            if linker not in LINKER_WHITELIST:
                raise ValueError(f'Invalid linker: {linker}. Allowed: {LINKER_WHITELIST}')
        return v

    @field_validator('weights')
    @classmethod
    def validate_weights(cls, v: dict[str, float] | None) -> dict[str, float]:
        if v is None:
            return {}
        total = sum(v.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f'Weights must sum to 1.0, got {total}')
        for key, val in v.items():
            if val < 0:
                raise ValueError(f'Weight for {key} must be >= 0, got {val}')
        return v

    @field_validator('thresholds')
    @classmethod
    def validate_thresholds(cls, v: dict[str, float] | None) -> dict[str, float]:
        if v is None:
            return {}
        for key, val in v.items():
            if not (0 <= val <= 1):
                raise ValueError(f'Threshold for {key} must be in [0, 1], got {val}')
        return v

    @field_validator('round_splits')
    @classmethod
    def validate_round_splits(cls, v: dict[str, float] | None) -> dict[str, float]:
        if v is None:
            return {}
        for key, val in v.items():
            if not (0 < val < 1):
                raise ValueError(f'Split for {key} must be in (0, 1), got {val}')
        return v
```

**Linker 白名单来源**：从 `main/stages4/s4_round04_enumerate.py` 的 `LINKER_POOL` 提取，确保前端传的所有 linker 均被 round04 支持。

### 3.3 config_data JSON 结构示例

```json
{
  "function_type": "antioxidant",
  "tier": "B",
  "top_n": 20,
  "linkers": ["Flex_GGGGSx2"],
  "structure_tool": "omegafold",
  "weights": {
    "anoxpepred": 0.45,
    "bepipred3": 0.10,
    "toxinpred3": 0.13,
    "algpred2": 0.09,
    "hemopi2": 0.09,
    "mhcflurry": 0.05,
    "temstapro": 0.09
  },
  "thresholds": {
    "toxinpred3": 0.38,
    "algpred2": 0.30,
    "hemopi2": 0.55
  },
  "round_splits": {
    "round1": 0.0125,
    "round2": 0.02,
    "round3": 0.002
  },
  "channel_strategy": "balanced"
}
```

**各字段默认值**（后端自动补全，不要求前端传全）：

| Tier | 字段 | 默认值 |
|------|------|--------|
| A | `function_type` | 必填 |
| A | `tier` | `"A"` |
| B | `top_n` | `20` |
| B | `linkers` | `["Flex_GGGGSx2"]` |
| B | `structure_tool` | `"omegafold"` |
| C | `weights` | `{}`（各服务权重均等）|
| C | `thresholds` | `{}`（使用 round 脚本默认值）|
| C | `round_splits` | `{}`（使用 pipeline 默认 split）|
| C | `channel_strategy` | `"balanced"` |

---

## 四、Auth API 详细设计

### 4.1 注册

```
POST /api/auth/register
Body: { "email": "user@example.com", "password": "xxx" }
Response 201: { "id": "uuid", "email": "user@example.com" }
Response 409: { "detail": "Email already registered" }
```

### 4.2 登录

```
POST /api/auth/login
Body: { "email": "user@example.com", "password": "xxx" }
Response 200: {
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": "uuid", "email": "user@example.com" }
}
Response 401: { "detail": "Invalid credentials" }
```

JWT 有效载荷：
```json
{
  "sub": "user_id (UUID)",
  "email": "user@example.com",
  "exp": "now + 7 days"
}
```

### 4.3 获取当前用户

```
GET /api/auth/me
Headers: Authorization: Bearer <token>
Response 200: { "id": "uuid", "email": "user@example.com", "created_at": "..." }
Response 401: { "detail": "Not authenticated" }
```

---

## 五、Jobs API 详细设计

### 5.1 创建任务

```
POST /api/jobs
Headers: Authorization: Bearer <token>
Body: {
  "name": "抗氧化测试 run 1",       // 可选
  "config": { ... config_data ... } // 直接传配置，或
  "config_id": "uuid"               // 使用已有配置方案
}
Response 201: {
  "id": "uuid",
  "status": "pending",
  "created_at": "..."
}
```

**内部逻辑**：
1. 将 `config_snapshot` 存入 `jobs.config_snapshot`
2. 在 `jobs.output_path` 创建目录（如 `output4/jobs/{job_id}/`）
3. 将 job 插入 PostgreSQL（`status: pending`）
4. Scheduler 发现 pending job 后启动 Pipeline

### 5.2 任务列表

```
GET /api/jobs?page=1&page_size=20&status=completed
Headers: Authorization: Bearer <token>
Response 200: {
  "items": [
    {
      "id": "uuid",
      "name": "抗氧化测试 run 1",
      "status": "completed",
      "current_round": "round07",
      "created_at": "...",
      "completed_at": "..."
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

### 5.3 任务详情

```
GET /api/jobs/{job_id}
Headers: Authorization: Bearer <token>
Response 200: {
  "id": "uuid",
  "name": "抗氧化测试 run 1",
  "status": "running",
  "current_round": "round03",
  "processed_count": 1234,
  "total_count": 5000,
  "config_snapshot": { ... },
  "created_at": "...",
  "started_at": "...",
  "output_path": "/path/to/output4/jobs/{job_id}/"
}
Response 404: { "detail": "Job not found" }
Response 403: { "detail": "Not authorized" }
```

### 5.4 中断任务

```
DELETE /api/jobs/{job_id}
Headers: Authorization: Bearer <token>
Response 200: { "id": "uuid", "status": "paused" }
```

**内部逻辑**：
1. 向 `jobs` 表写入 `status: paused`
2. Scheduler 监听 `paused` 状态，停止当前 subprocess
3. 下一个 round 不会启动

### 5.5 SSE 事件流

```
GET /api/jobs/{job_id}/events
Headers: Authorization: Bearer <token>
Response: text/event-stream

# 事件格式：
event: round_completed
data: {"round": "round03", "message": "Deep scoring completed: 5000 -> 250", "processed": 250}

event: job_completed
data: {"job_id": "uuid", "status": "completed"}
```

前端用 `EventSource` API 消费此流。

---

## 六、Results API 详细设计

### 6.1 排名列表

```
GET /api/results/{job_id}/ranking?page=1&page_size=20&sort_by=composite_score&order=desc
Headers: Authorization: Bearer <token>
Response 200: {
  "items": [
    {
      "rank": 1,
      "construct_id": 42,
      "sequence": "WDHINNPEVYF...",
      "linker": "Flex_GGGGSx2",
      "position": "N端",
      "channel": "top",
      "composite_score": 0.8234,
      "anoxpepred": 0.81,
      "sodope": 0.72,
      "pLDDT": 0.88,
      "sasa_exposed": true
    }
  ],
  "total": 250,
  "page": 1,
  "page_size": 20
}
```

**数据来源**：读取 `output_path/round07/final/top_constructs.csv`
（per-job 目录：`output4/jobs/{job_id}/round07/final/top_constructs.csv`）

### 6.2 漏斗数据

```
GET /api/results/{job_id}/funnel
Headers: Authorization: Bearer <token>
Response 200: {
  "stages": [
    { "name": "Round 1", "count": 19900000, "color": "#94a3b8" },
    { "name": "Round 2", "count": 250000, "color": "#60a5fa" },
    { "name": "Round 3", "count": 5000, "color": "#34d399" },
    { "name": "Stage 4", "count": 250, "color": "#fbbf24" }
  ]
}
```

**数据来源（已修正）**：从 per-job DuckDB 的 `checkpoint` 表查询（不使用 CSV）：

```python
# results/service.py — funnel 数据（修正：不用 CSV，用 DuckDB checkpoint 表）
def get_funnel_data(job_id: str, job: Job) -> list[dict]:
    """
    读取 per-job DuckDB 的 funnel 数据（read_only 连接，无并发冲突）
    checkpoint 表结构：round_name, total_items, processed_items, status
    """
    import duckdb

    db_path = Path(job.output_path) / "pipeline.db"
    if not db_path.exists():
        # Round 尚未开始，返回空 stages
        return []

    conn = duckdb.connect(str(db_path), read_only=True)

    rows = conn.execute("""
        SELECT round_name, total_items
        FROM checkpoint
        WHERE status IN ('completed', 'running')
        ORDER BY round_name
    """).fetchall()

    conn.close()

    # 映射 round 名称到显示名称和颜色
    ROUND_DISPLAY_MAP = {
        'round0': {'name': 'Round 0', 'color': '#94a3b8'},
        'round1': {'name': 'Round 1', 'color': '#60a5fa'},
        'round2': {'name': 'Round 2', 'color': '#34d399'},
        'round3': {'name': 'Round 3', 'color': '#fbbf24'},
        'round4': {'name': 'Stage 4', 'color': '#f97316'},
        'round5': {'name': 'Stage 5', 'color': '#ef4444'},
        'round6': {'name': 'Stage 6', 'color': '#8b5cf6'},
        'round7': {'name': 'Final', 'color': '#10b981'},
    }

    stages = []
    for row in rows:
        round_name, total_items = row
        info = ROUND_DISPLAY_MAP.get(round_name, {'name': round_name, 'color': '#94a3b8'})
        stages.append({**info, 'count': total_items})

    return stages
```

**注意**：运行中（Round 未完成）的 job，`checkpoint` 表可能只有部分 round 记录，前端漏斗图对未完成阶段显示预估（从上一个实际值递减）。

### 6.3 PDB 文件服务

```
GET /api/results/{job_id}/pdb/{construct_id}
Headers: Authorization: Bearer <token>
Response: 文件流（application/octet-stream）
```

后端从 `output_path/pdb/{construct_id}.pdb` 读取并返回，使用 `safe_pdb_path()` 防止路径遍历。

**安全函数**：
```python
from pathlib import Path

def safe_pdb_path(job_output_path: str, construct_id: str) -> Path:
    """
    防止路径遍历：解析后的路径必须在 job_output_path 目录内
    """
    base = Path(job_output_path).resolve()
    requested = (base / "pdb" / f"{construct_id}.pdb").resolve()
    # 确认解析后的路径在 base 目录下
    if not str(requested).startswith(str(base)):
        raise ValueError(f"Invalid construct_id: {construct_id}")
    return requested
```

**前端使用**：
```tsx
const pdbUrl = `/api/results/${jobId}/pdb/${constructId}`;
<StructureViewer pdbUrl={pdbUrl} />
```

---

## 七、Configs API 详细设计

### 7.1 创建配置

```
POST /api/configs
Headers: Authorization: Bearer <token>
Body: {
  "name": "我的严格抗氧化方案",
  "is_public": false,
  "config": { ... config_data ... }
}
Response 201: { "id": "uuid", "name": "我的严格抗氧化方案", ... }
```

### 7.2 列表（我的私有配置）

```
GET /api/configs
Headers: Authorization: Bearer <token>
Response 200: [
  { "id": "uuid", "name": "我的严格抗氧化方案", "is_public": false, ... }
]
```

### 7.3 配置市场（公开配置）

```
GET /api/configs/public
Headers: Authorization: Bearer <token>
Response 200: [
  { "id": "uuid", "user_id": "...", "name": "团队共享方案", ... }
]
```

### 7.4 复制配置

```
POST /api/configs/{id}/copy
Headers: Authorization: Bearer <token>
Response 201: { "id": "new_uuid", "name": "我的复制 - 团队共享方案", ... }
```

将公开配置复制为我的私有配置。

---

## 八、Scheduler 与 FastAPI 的交互协议

Scheduler 和 FastAPI 通过 PostgreSQL 解耦，无直接进程间通信。

**协议约定**：

| 动作 | Scheduler 写入 | FastAPI 读取 |
|------|---------------|--------------|
| 任务创建 | — | `jobs.status = 'pending'` |
| 任务开始 | `jobs.status = 'running'`, `jobs.started_at` | 轮询 |
| Round 开始 | `job_events` INSERT | SSE 推送 |
| Round 完成 | `jobs.current_round`, `jobs.processed_count` 更新 | 轮询 |
| 任务完成 | `jobs.status = 'completed'`, `jobs.completed_at` | 轮询 + SSE |
| 任务失败 | `jobs.status = 'failed'`, `job_events` INSERT error | SSE 推送 |

**Scheduler 读取 FastAPI 的配置**：
- Scheduler 从 `jobs.config_snapshot` 读取该 job 的配置参数
- Scheduler 用 `--config` 参数调用 round 脚本：`subprocess.run(["uv", "run", "python", "-m", "main.stages4.s4_round01", "--config", config_json])`