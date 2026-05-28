# 系统集成架构审查报告

> **审查范围**: iGEM Silk Platform 与 iGEM-silk-main stages4 Pipeline 的集成设计
> **审查依据**: `iGEM-silk-main/README.md`, `main/stages4/PLAN.md`, `AGENTS.md`, `tools/test_all_services.py`, `tools/docker-compose.yml`, `pyproject.toml`, `docs/superpowers/plans/02-system-architecture.md`, `docs/superpowers/plans/04-backend-architecture.md`, `docs/superpowers/plans/05-implementation-plan.md`
> **状态**: 详细审查

---

## 一、多数据库架构

### 1.1 分工设计

架构文档（02-system-architecture.md § 七）确定的分工：

| 数据库 | 用途 | 管理方 |
|--------|------|--------|
| PostgreSQL | 平台元数据（users/jobs/configs）| 平台 |
| DuckDB | Pipeline 核心数据（candidates/constructs）| stages4，只写 |

**原则**：Pipeline 只写 DuckDB，FastAPI 只读 DuckDB（通过结果文件路径访问，不直连 DuckDB）。

### 1.2 平台能否安全读取 DuckDB？

**结论：当前设计存在 read/write 冲突风险，需要修正。**

**分析**：

`output4/pipeline.db` 是全局共享文件：
- stages4 的 round 脚本以 **read/write 模式**打开 DuckDB（`duckdb.connect(db_path)` — 默认读写）
- Platform Result API 如果直接连接同一个 `pipeline.db` 进行 SELECT，会与 pipeline 的写入事务冲突

DuckDB 的并发读写限制：
- 一个连接在写事务中持有 `WRITE` 锁时，其他连接的读事务会被阻塞直到写事务提交
- 如果 platform 在 pipeline 运行时读取 `pipeline.db`，读操作可能得到不一致的快照，或在超忙碌系统上超时

**关键问题**：架构文档说"FastAPI 只读 DuckDB（通过结果文件路径访问）"，但实现上 Result API 实际上是直连 `output4/pipeline.db`（从 02 § 三的描述可以看出）。这与 stages4 的写入产生竞争。

### 1.3 解决建议

**方案 A（推荐）：每个 job 独立的 DuckDB 实例**

修改 `s4_db.py` 的 `db_path` 参数化机制：

```python
# s4_db.py — 支持自定义路径
DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"

def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    self.db_path = str(db_path)
```

Scheduler 为每个 job 创建独立 DB：
```
output4/jobs/{job_id}/pipeline.db
```

这样：
- stages4 的 round 脚本接受 `--db-path` 参数
- 每个 job 有独立的 DuckDB 文件，零冲突
- stages4 本身可以复用 `output4/pipeline.db` 作为默认全局 DB（单用户场景）

**方案 B：平台永远读快照文件，不连 DuckDB**

Result API 只读取 round 输出目录中的 CSV/JSON 文件，从不直接连接 DuckDB。

**推荐方案 A**，因为：
- CSV 文件接力方式无法利用 DuckDB 的 SQL 查询能力（漏斗数据需要聚合）
- 架构文档 § 三描述 Result API 应能查询 funnel 数据，这需要 DuckDB 连接

### 1.4 如果 pipeline 和 platform 同时在同台机器运行

stages4 的 DuckDB 文件路径：
```python
# s4_db.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"
```

如果 pipeline 在机器 A 上运行（写 `output4/pipeline.db`），而 platform 在机器 B 上读取同一个 NFS 共享存储上的文件，会产生文件锁冲突。

**缓解**：
- 确保 pipeline 和 platform 在**同一台机器**运行（通过 docker-compose 编排在同一网络）
- 或者使用方案 A（per-job DuckDB）彻底隔离

---

## 二、Docker Compose 设置

### 2.1 两处 docker-compose.yml 的关系

| 文件 | 用途 | 服务 |
|------|------|------|
| `iGEM-silk-main/tools/docker-compose.yml` | 微服务集群 | 16 个微服务 + 3 个辅助服务 |
| `docs/superpowers/plans/05-implementation-plan.md` 提到 `platform/docker-compose.yml` | 平台整体编排 | PostgreSQL + backend + scheduler |

**当前状态**：平台专用的 `platform/docker-compose.yml` **尚未创建**（实施计划中规划为 Milestone 1.1 的任务）。

### 2.2 两 compose 文件的共存方案

**方案 1：合并为单一 `docker-compose.yml`（推荐简单场景）**

将 PostgreSQL、backend、scheduler 添加到现有的 `tools/docker-compose.yml`，作为额外服务：

```yaml
# iGEM-silk-main/docker-compose.yml（或放在根目录）

services:
  # === 现有微服务（从 tools/docker-compose.yml 迁移）===
  anoxpepred:   {...}
  bepipred3:    {...}
  # ... 所有 16 个微服务 ...

  # === 新增：平台服务 ===
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: igem_platform
      POSTGRES_USER: igem_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U igem_user -d igem_platform"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: .
      dockerfile: platform/backend/Dockerfile
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform
      SECRET_KEY: ${SECRET_KEY}
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./iGEM-silk-main:/app/iGEM-silk-main:ro  # 只读挂载 pipeline 代码
      - output4:/app/output4:ro                    # 只读挂载 pipeline 输出

  scheduler:
    build:
      context: .
      dockerfile: platform/scheduler/Dockerfile
    environment:
      DATABASE_URL: postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform
      PIPELINE_ROOT: /app/iGEM-silk-main
      OUTPUT_ROOT: /app/output4
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./iGEM-silk-main:/app/iGEM-silk-main
      - output4:/app/output4
    # GPU 资源由 scheduler 独占管理
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  postgres_data:
  output4:
```

**方案 2：分离的 compose 文件（生产环境推荐）**

| 文件 | 范围 | 启动命令 |
|------|------|----------|
| `tools/docker-compose.yml` | 微服务集群 | `docker compose -f tools/docker-compose.yml --profile gpu --profile cpu up -d` |
| `platform/docker-compose.yml` | 平台服务 + PostgreSQL | `docker compose -f platform/docker-compose.yml up -d` |

两个 compose 通过共享的 `output4` volume 实现数据交换。

**推荐方案 2**（分离式），理由：
- stages4 工程师直接在 `tools/` 目录下管理微服务，不需要了解平台服务
- platform 工程师只操作 `platform/` 目录
- 避免 `tools/docker-compose.yml` 变得臃肿（已有 500+ 行）
- 微服务可以独立重启/升级，不影响平台服务

### 2.3 网络隔离

当前 `tools/docker-compose.yml` 使用默认 bridge 网络，没有显式定义网络。

**问题**：backend 和 scheduler 在不同 compose 文件中时，它们与微服务的网络联通性需要显式配置。

**解决方案**（方案 2）：创建共享网络

```yaml
# platform/docker-compose.yml
networks:
  default:
    name: igem_platform_network
  tools_external:
    external: true
    name: igem_silk_tools_default  # tools compose 创建的网络
```

或者在根目录创建一个 `docker-compose.yml` 作为"顶层编排文件"，同时引用微服务 + 平台服务：

```yaml
# docker-compose.yml（根目录，顶层编排）
include:
  - tools/docker-compose.yml
  - platform/docker-compose.yml
```

---

## 三、输出目录隔离

### 3.1 当前 output4 结构

```
output4/
├── pipeline.db                    # 全局 DuckDB（stages3/4 共用）
├── STATUS.md
├── reports/
├── pdb/                           # 全局 PDB 文件
│   └── con_XXXX/
├── logs/
└── final/
    ├── top100.csv
    └── bottom100.csv
```

### 3.2 Per-Job 隔离的需求

架构文档（02-system-architecture.md § 三）描述：

> 在 jobs 表插入新记录 { status: "pending" }
> `output_path` 指向该任务独立的输出目录

**但 stages4 的 round 脚本硬编码写 `output4/`**，不感知 job_id：

```python
# s4_db.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"

# s4_round05_3d.py
OUTPUT4 = PROJECT_ROOT / "output4"
PDB_DIR = OUTPUT4 / "pdb"
```

**Gap**：stages4 的 round 脚本不知道它正在为哪个 job 运行，所有输出写入共享的 `output4/`。

### 3.3 解决方案

为 round 脚本增加 `--job-id` 和 `--output-dir` 参数：

```python
# s4_round*.py 统一的参数解析
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--job-id", type=str, default=None)
parser.add_argument("--output-dir", type=str, default=str(OUTPUT4))
args = parser.parse_args()

if args.job_id:
    JOB_OUTPUT = Path(args.output_dir) / "jobs" / args.job_id
else:
    JOB_OUTPUT = Path(args.output_dir)
```

Scheduler 调用时传递 job_id：

```python
subprocess.Popen(
    ["uv", "run", "python", "-m", "main.stages4.s4_round01",
     "--job-id", job_id, "--output-dir", str(OUTPUT_ROOT)],
    cwd=str(PIPELINE_ROOT),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
)
```

**Per-job 输出结构**：
```
output4/
├── shared/                    # 全局共享（stages3 预处理的 candidates）
│   └── pipeline.db
└── jobs/
    └── {job_id}/
        ├── pipeline.db       # 该 job 独立的 DuckDB
        ├── pdb/             # PDB 文件
        ├── reports/         # 各轮次报告
        └── final/           # 最终结果
```

### 3.4 多实例并发

**问题**：如果两个 scheduler 实例同时运行（或用户同时触发两个 job），它们会竞争写入同一个 `output4/` 目录。

**解决**：per-job DuckDB + per-job output 目录（§ 3.3 已解决）。另外需要在 PostgreSQL 层面加锁：`jobs.status` 的 `pending → running` 转换应该是原子操作，用 `SELECT ... FOR UPDATE` 实现。

---

## 四、服务发现与网络

### 4.1 Scheduler 如何访问微服务

**当前配置**（`main/config.py`）：
```python
SERVICE_HOST = "127.0.0.1"
SERVICES: dict[str, dict] = {
    "anoxpepred": {"port": 8001, "group": "score"},
    ...
}
def service_url(name: str) -> str:
    env_host = os.environ.get(f"{name.upper()}_HOST")
    host = env_host or SERVICES[name].get("host", SERVICE_HOST)
    port = ...
    return f"http://{host}:{port}"
```

**问题**：`SERVICE_HOST = "127.0.0.1"` 意味着微服务必须运行在**本机**（localhost）。但 Docker 容器内用 `127.0.0.1` 指向容器自己，不是宿主机。

**已知的正确做法**（来自 CLAUDE.md 和 stages4 经验）：
- 不用 `127.0.0.1`，而是用 `docker inspect <container>` 获取 bridge IP
- 微服务暴露端口到宿主机，scheduler 通过**宿主机 IP + 端口**访问

### 4.2 环境变量覆盖机制

现有的 `{NAME}_HOST` 环境变量覆盖机制是正确的设计：

```bash
# 宿主机 IP
export ANOXPEPRED_HOST=192.168.1.100
export ANOXPEPRED_PORT=8001
```

scheduler 容器内需要访问微服务（可能与 scheduler 不同容器）：
- **方案**：scheduler 和微服务在同一个 docker-compose 网络中，scheduler 用**服务名**访问微服务

```yaml
# platform/docker-compose.yml
services:
  scheduler:
    environment:
      ANOXPEPRED_HOST: anoxpepred  # docker-compose 服务名
      BEPIPRED3_HOST: bepipred3
      ...
```

### 4.3 Backend 和 Scheduler 与 PostgreSQL 的连接

```python
# backend
DATABASE_URL=postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform

# scheduler
DATABASE_URL=postgresql+asyncpg://igem_user:${POSTGRES_PASSWORD}@postgres:5432/igem_platform
```

**注意**：backend 和 scheduler 都连接同一个 PostgreSQL 实例。Backend 只读 `jobs` 表（查询状态），scheduler 读写 `jobs` 表（更新状态）。这个设计是合理的。

### 4.4 环境变量冲突风险

| 环境变量 | 微服务 | Backend/Scheduler | 冲突可能 |
|----------|--------|-------------------|---------|
| `PORT` | ✅ 用于服务端口 | ❌ 不需要 | 无 |
| `DATABASE_URL` | ❌ | ✅ PostgreSQL 连接 | 无 |
| `SECRET_KEY` | ❌ | ✅ JWT 签名 | 无 |
| `{NAME}_HOST` | ✅ 微服务发现 | ✅ scheduler 用 | 需要隔离配置 |

**风险**：微服务的 `docker-compose.yml` 中设置了 `PORT=8001` 等环境变量，这些不会影响 backend/scheduler。只要 docker-compose service name 不冲突（微服务用 `tools_` 前缀，或分离网络），就不会有问题。

---

## 五、安全问题

### 5.1 配置 JSON 验证

**风险**：用户提交任意 config JSON，其中可能包含恶意参数（如 `--rm -rf /` 类型的路径遍历，或负数阈值破坏筛选逻辑）。

**当前状态**：架构文档（04-backend-architecture.md § 三）只定义了 Pydantic schemas，没有定义验证逻辑。

**必须实现的验证**：

```python
# platform/backend/configs/schemas.py
from pydantic import BaseModel, field_validator
from typing import Literal

class WeightsConfig(BaseModel):
    anoxpepred: float = 0.0
    bepipred3: float = 0.0
    # ... 其他服务 ...

    @field_validator("*")
    @classmethod
    def weights_must_be_nonnegative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("权重不能为负数")
        return v

class ThresholdsConfig(BaseModel):
    toxinpred3: float | None = None
    algpred2: float | None = None
    hemopi2: float | None = None

    @field_validator("*")
    @classmethod
    def threshold_range_check(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("阈值必须在 [0, 1] 范围内")
        return v

class JobConfig(BaseModel):
    function_type: Literal["antioxidant", "antibacterial", "antiglycation"]
    tier: Literal["A", "B", "C"]
    top_n: int = 100
    linkers: list[str] | None = None
    structure_tool: Literal["omegafold", "esmfold", "alphafold3"] = "omegafold"
    weights: WeightsConfig | None = None
    thresholds: ThresholdsConfig | None = None

    @field_validator("top_n")
    @classmethod
    def top_n_range(cls, v: int) -> int:
        if v < 1 or v > 1000:
            raise ValueError("top_n 必须在 [1, 1000] 范围内")
        return v
```

**额外措施**：
- 路径遍历保护：验证 `output_path` 不包含 `../`
- 字符串参数白名单：linker 类型、function_type 等必须是预定义枚举值

### 5.2 PDB 文件读取路径遍历漏洞

**风险**：`GET /api/results/{job_id}/pdb/{construct_id}` 如果直接拼路径，用户可能提交 `construct_id=../../../etc/passwd` 读取任意文件。

**当前架构**（04-backend-architecture.md § 六）：
```python
# 从 output_path/pdb/{construct_id}.pdb 读取
path = output_dir / "pdb" / f"{construct_id}.pdb"
```

**防护措施**：

```python
from pathlib import Path

def safe_pdb_path(job_id: str, construct_id: str, output_base: Path) -> Path:
    # 白名单验证
    safe = (output_base / job_id / "pdb").resolve()
    requested = (safe / f"{construct_id}.pdb").resolve()
    # 防止路径遍历
    if not str(requested).startswith(str(safe)):
        raise HTTPException(403, "路径遍历检测")
    if not requested.exists():
        raise HTTPException(404, "PDB 文件不存在")
    return requested
```

### 5.3 JWT 密钥管理

**问题**：`SECRET_KEY` 在 `docker-compose.yml` 中明文或用 `.env` 文件管理。`.env` 文件不应该提交到 git（已在 `.gitignore`）。

**最小安全建议**：

```yaml
# docker-compose.yml
backend:
  environment:
    SECRET_KEY: ${SECRET_KEY:?SECRET_KEY must be set}
```

```bash
# .env（不提交 git）
SECRET_KEY=your-256-bit-secret-key-here
POSTGRES_PASSWORD=your-db-password
```

生产环境使用 K8s Secrets 或 Vault。

---

## 六、资源隔离

### 6.1 GPU 显存管理

**当前问题**：stages4 Round 5 使用 OmegaFold（GPU），Round 3 的 BepiPred3/TemStaPro/pLM4CPPs 也需要 GPU。多个 GPU 服务同时运行会导致显存竞争。

**stages4 的并发控制**（来自 CLAUDE.md）：
```python
# s4_service_map.py
ROUND_SERVICES = {
    "round3": ["bepipred3", "temstapro", "sodope", "plm4cpps",
               "toxinpred3", "toxinpred3-2", "toxinpred3-3"],
    "round5": ["omegafold"],  # 串行，Semaphore(1)
}
```

**scheduler 的 GPU 独占管理**（来自 02-system-architecture.md § 五）：
```python
# scheduler 获取 GPU
if GPU_STATE == FREE:
    GPU_STATE = IN_USE
    start_pipeline()
else:
    sleep(30)  # 等待 GPU 释放
# pipeline 结束
GPU_STATE = FREE
```

**建议的 docker-compose 配置**：

```yaml
scheduler:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  # 确保 scheduler 容器独占 GPU
  # 只有 scheduler 容器能分配到 GPU
```

但更根本的问题是：**当前 stages4 的 round 脚本没有 `--gpu-device` 参数**，无法指定使用哪个 GPU。如果 scheduler 容器独占 GPU，round 脚本内的 PyTorch 调用会自动使用该 GPU。

### 6.2 Disk Space 管理

**现状**：`output4/` 目录目前没有自动清理机制。

**风险**：
- 每个 job 产生 ~500MB–2.7GB 数据（pipeline.db + PDB 文件）
- 长期使用后磁盘空间耗尽

**建议的分层存储策略**：

| 数据类型 | 位置 | 保留策略 |
|----------|------|---------|
| 完成后 job 的最终结果 | `output4/jobs/{job_id}/final/` | 永久保留（或用户删除时） |
| 中间 round 数据 | `output4/jobs/{job_id}/roundNN/` | 完成后 N 天删除 |
| PDB 文件 | `output4/jobs/{job_id}/pdb/` | 永久保留（或可配置） |
| 全局共享 DB | `output4/pipeline.db` | 永久保留 |

**实现**：在 PostgreSQL `jobs` 表添加 `output_path` 字段和 `disk_usage_bytes` 字段，scheduler 在 job 完成后更新。后台定期检查磁盘使用率，超过阈值时发出警告（但不自动删除，需用户确认）。

---

## 七、详细的 Docker Compose 配置

### 7.1 完整平台 docker-compose.yml 建议

```yaml
# platform/docker-compose.yml
version: "3.9"

services:

  # ===========================================================================
  # PostgreSQL — 平台元数据库
  # ===========================================================================
  postgres:
    image: postgres:16-alpine
    container_name: igem_postgres
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-igem_platform}
      POSTGRES_USER: ${POSTGRES_USER:-igem_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?postgres password required}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-igem_user} -d ${POSTGRES_DB:-igem_platform}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped
    networks:
      - igem_platform

  # ===========================================================================
  # FastAPI Backend
  # ===========================================================================
  backend:
    build:
      context: ../iGEM-silk-main  # 指向 iGEM-silk-main 根目录
      dockerfile: platform/backend/Dockerfile
    container_name: igem_backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-igem_user}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-igem_platform}
      SECRET_KEY: ${SECRET_KEY:?secret key required}
      ALGORITHM: ${ALGORITHM:-HS256}
      ACCESS_TOKEN_EXPIRE_DAYS: ${ACCESS_TOKEN_EXPIRE_DAYS:-7}
      # 微服务 Host 配置（供 ServiceClient 使用）
      ANOXPEPRED_HOST: ${ANOXPEPRED_HOST:-anoxpepred}
      BEPIPRED3_HOST: ${BEPIPRED3_HOST:-bepipred3}
      TOXINPRED3_HOST: ${TOXINPRED3_HOST:-toxinpred3}
      HEMOP12_HOST: ${HEMOP12_HOST:-hemopi2}
      ALGPRED2_HOST: ${ALGPRED2_HOST:-algpred2}
      MHCFLURRY_HOST: ${MHCFLURRY_HOST:-mhcflurry}
      PLM4CPPS_HOST: ${PLM4CPPS_HOST:-plm4cpps}
      TEMSTAPRO_HOST: ${TEMSTAPRO_HOST:-temstapro}
      SODOPE_HOST: ${SODOPE_HOST:-sodope}
      GRAPHCPP_HOST: ${GRAPHCPP_HOST:-graphcpp}
      SASA_HOST: ${SASA_HOST:-sasa}
      AGGRESCAN3D_HOST: ${AGGRESCAN3D_HOST:-aggrescan3d}
      OMEGAFOLD_HOST: ${OMEGAFOLD_HOST:-omegafold}
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ../iGEM-silk-main:/app/iGEM-silk-main:ro
      - pipeline_output:/app/output4:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    restart: unless-stopped
    networks:
      - igem_platform

  # ===========================================================================
  # Pipeline Scheduler — 独立进程
  # ===========================================================================
  scheduler:
    build:
      context: ../iGEM-silk-main
      dockerfile: platform/scheduler/Dockerfile
    container_name: igem_scheduler
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-igem_user}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-igem_platform}
      PIPELINE_ROOT: /app/iGEM-silk-main
      OUTPUT_ROOT: /app/output4
      # GPU 配置
      CUDA_VISIBLE_DEVICES: "0"
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ../iGEM-silk-main:/app/iGEM-silk-main
      - pipeline_output:/app/output4
      - /var/run/docker.sock:/var/run/docker.sock  # 管理微服务容器
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    networks:
      - igem_platform

  # ===========================================================================
  # 微服务集群（可选：与 tools/docker-compose.yml 分开管理）
  # 如果统一管理，在此包含 tools/docker-compose.yml 的服务
  # ===========================================================================
  # anoxpepred:
  #   build:
  #     context: ../iGEM-silk-main
  #     dockerfile: tools/AnOxPePred/Dockerfile
  #   container_name: anoxpepred
  #   ports:
  #     - "8001:8001"
  #   environment:
  #     - PORT=8001
  #   profiles: [gpu]
  #   deploy:
  #     resources:
  #       reservations:
  #         devices:
  #           - driver: nvidia
  #             count: 1
  #             capabilities: [gpu]
  #   healthcheck:
  #     test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
  #     interval: 30s
  #     timeout: 10s
  #     retries: 3
  #   networks:
  #     - igem_platform

networks:
  igem_platform:
    name: igem_platform_network
    driver: bridge

volumes:
  postgres_data:
    name: igem_platform_postgres
  pipeline_output:
    name: igem_platform_output
    # 指向宿主机上的 iGEM-silk-main/output4
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${OUTPUT4_PATH:-../iGEM-silk-main/output4}
```

### 7.2 关键配置说明

**healthcheck**：所有服务都有健康检查，确保依赖服务就绪后再启动下游服务（`depends_on` + `condition: service_healthy`）。

**restart policy**：`unless-stopped` 保证进程崩溃后自动重启，但允许手动停止。

**GPU 资源**：只有 `scheduler` 容器申请 GPU 资源（用于运行 stages4 round 脚本时调用 OmegaFold）。微服务容器**不**在这里声明 GPU，而是通过 scheduler 调用 `s4_docker_utils.ensure_services()` 动态启动，这些微服务容器由 docker 管理。

**网络隔离**：`igem_platform` 网络隔离了平台内部服务，微服务可以通过服务名互相访问。

**output4 volume 绑定**：`pipeline_output` volume 绑定到宿主机的 `iGEM-silk-main/output4`，backend 以只读方式挂载（`:ro`），scheduler 读写。

### 7.3 微服务与平台的网络联通性

微服务（`tools/docker-compose.yml`）目前使用默认 bridge 网络。平台服务（`platform/docker-compose.yml`）使用 `igem_platform` 网络。

**如果分开管理**，需要确保微服务和平台在**同一网络**：

```bash
# 方案：在 tools/ 目录下启动微服务时加入平台网络
cd tools && docker network create igem_platform_network 2>/dev/null || true
docker compose --network igem_platform_network up -d
```

或者使用 `docker-compose.yml` 的 `include` 字段（Docker Compose v2.5+）同时加载两个文件。

---

## 八、关键 Gap 总结

| 类别 | Gap | 优先级 | 建议 |
|------|-----|--------|------|
| 数据库 | stages4 round 脚本不感知 job_id，所有输出写全局 `output4/` | 高 | 增加 `--job-id` 和 `--output-dir` 参数，实现 per-job DuckDB |
| 数据库 | Platform Result API 直连全局 `pipeline.db` 与 pipeline 写入冲突 | 高 | 改为读 per-job DuckDB 或 CSV 快照 |
| 网络 | 两处 docker-compose.yml 需显式配置网络联通 | 高 | 使用 `include` 或创建共享网络 |
| 安全 | config JSON 缺少严格的 Pydantic 验证 | 高 | 实现 §5.1 的验证逻辑 |
| 安全 | PDB 路径遍历漏洞潜在风险 | 高 | 实现 §5.2 的路径验证 |
| 资源 | `output4/` 无磁盘清理策略 | 中 | 添加 job 生命周管理，定期检查磁盘 |
| 资源 | stages4 round 脚本无 GPU 设备指定参数 | 中 | PyTorch 会自动使用 CUDA_VISIBLE_DEVICES指定的GPU |
| 配置 | stages4 round 脚本不支持 `--db-path` 自定义 | 中 | 增加 §3.3 的参数支持 |
| 部署 | 平台 docker-compose.yml 尚未创建 | 高 | 按 §7.1 实现 |

---

## 九、stages4 脚本的参数化修改清单

为支持多 job 并发，stages4 的 round 脚本需要以下修改（按优先级）：

### 必须修改

1. **`s4_db.py`**：
   - `db_path` 参数化，支持 `PipelineDB(db_path="/path/to/per-job.db")`
   - 添加 `get_checkbar()` / `set_checkpoint()` 支持 per-job checkpoint

2. **所有 `s4_round*.py` 脚本**：
   ```python
   import argparse
   parser = argparse.ArgumentParser()
   parser.add_argument("--job-id", type=str, default=None)
   parser.add_argument("--output-dir", type=str, default=str(PROJECT_ROOT / "output4"))
   args = parser.parse_args()

   if args.job_id:
       OUTPUT_BASE = Path(args.output_dir) / "jobs" / args.job_id
   else:
       OUTPUT_BASE = Path(args.output_dir)
   ```

3. **`s4_docker_utils.py`**：
   - `ensure_services()` 幂等缓存需要按 job_id 隔离（否则跨 job 的服务状态混淆）

### 建议修改

4. **`s4_round05_3d.py`** 的 PDB 目录路径
5. **`s4_round07_final.py`** 的最终输出路径

---

## 十、已验证可行的设计

以下设计在当前代码中已经正确实现，无需修改：

1. **环境变量覆盖服务地址**（`main/config.py` 的 `service_url()`）— 允许运行时重定向微服务地址
2. **按需启动微服务**（`s4_docker_utils.ensure_services()`）— 幂等，智能缓存
3. **DuckDB 批量插入**（`s4_db.py` 的 `INSERT VALUES` 批处理）— 20k rows/s
4. **asyncio 安全**（`asyncio.gather(*tasks, return_exceptions=True)`）— 异常隔离
5. **ToxinPred3 串行化**（Semaphore(1) + socket timeout）— 避免 C extension 挂死
6. **BepiPred3 串行**（Semaphore=1, timeout=600s）— 避免 GPU OOM
7. **OmegaFold 串行**（Semaphore(1)）— 避免阻塞事件循环
8. **健康检查机制**（所有微服务都有 `/health` 端点 + docker healthcheck）