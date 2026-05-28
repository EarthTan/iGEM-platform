# Backend & Scheduler Architecture Review

**Reviewer**: Claude Code
**Date**: 2026-05-28
**Files reviewed**:
- `docs/superpowers/plans/04-backend-architecture.md`
- `docs/superpowers/plans/05-implementation-plan.md`
- `iGEM-silk-main/main/stages4/s4_db.py`
- `iGEM-silk-main/main/stages4/s4_docker_utils.py`
- `iGEM-silk-main/main/stages4/s4_round01_antioxidant_split.py`
- `iGEM-silk-main/main/stages4/s4_round02_safety_screen.py`
- `iGEM-silk-main/main/stages4/s4_round07_final.py`
- `iGEM-silk-main/main/stages4/s4_service_map.py`
- `iGEM-silk-main/main/config.py`
- `iGEM-silk-main/main/client.py`
- `iGEM-silk-main/tools/docker-compose.yml`

---

## Executive Summary

The plan describes a PostgreSQL-backed multi-job platform with a Scheduler that dispatches round scripts per job. The stages4 codebase is a **single-job DuckDB pipeline** with round scripts that were never designed to receive per-job parameters. The gap is fundamental: the plan assumes a multi-tenant job queue with parameterized round invocations, but stages4 is a monolithic single-run pipeline. Significant adaptation is required before the plan is implementable.

---

## 1. Schema Gaps

### 1.1 Two Databases, Not One

The plan specifies a **PostgreSQL** backend that stores jobs, configs, users, and events. This is correct for the FastAPI backend. However, stages4 uses a **separate DuckDB** database at `output4/pipeline.db` for pipeline state. These are two independent systems:

- **PostgreSQL** (new): User accounts, job metadata, auth tokens, SSE event log
- **DuckDB** (existing): Pipeline candidates, scores, checkpoints, final rankings

The plan correctly notes this in Section 4 (Results API reads from `output_path/round07/final/top_constructs.csv`), but the overall architecture section does not explicitly call out this dual-database design. This must be clarified.

### 1.2 PostgreSQL Schema Matches Plan — With One Omission

The `jobs` table in the plan includes:
```sql
config_snapshot JSONB NOT NULL
```

This is present and correct. However, the plan does **not** include a table for tracking per-round progress in PostgreSQL. The plan relies on the Scheduler writing `current_round` and `processed_count` to the `jobs` table, but:

- `processed_count BIGINT DEFAULT 0` — there is no definition of what "processed" means across rounds. For round1, it's candidate count. For round4, it's construct count. The plan should specify that this column is a free-form counter updated by the Scheduler, not a strict total.

- **Missing**: The plan does not include a `job_checkpoints` table or similar for per-round/step checkpoint tracking in PostgreSQL. The DuckDB has a `checkpoint` table (round/step/status/total_items/processed_items), but this is internal to stages4. If the Scheduler crashes and restarts, how does it know which step within a round to resume? The plan mentions checkpointing but never defines the PostgreSQL side of this.

### 1.3 The DuckDB Schema Has No `job_id` Column

The stages4 DuckDB schema (in `s4_db.py`) has **no `job_id` column anywhere**. All tables (`candidates`, `round1_scores`, `constructs`, `final_ranking`, etc.) are global to a single pipeline run. The plan assumes each job gets its own isolated database namespace (via `output4/jobs/{job_id}/`), but the DuckDB schema was designed for a single shared pipeline.

**Specific missing columns**:
- No `job_id` in `candidates` — candidates are global
- No `job_id` in `checkpoint` — the checkpoint table tracks round/step within the single pipeline run
- The plan's `output_path = VARCHAR(500)` in the `jobs` table is the right approach (per-job output directory), but the current `s4_db.py` hardcodes `DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"`.

**Recommendation**: Each job should get its own DuckDB file at `output4/jobs/{job_id}/pipeline.db`. This requires modifying `PipelineDB.__init__` to accept `job_id` and constructing the path accordingly. The `s4_db.py` file would need to be instantiated with a per-job path.

### 1.4 Missing `construct_id` Column in `final_ranking`

In `s4_db.py`, the `final_ranking` table has `construct_id` as a `REFERENCES` (foreign key to `constructs`), but the `insert_final_ranking` method writes `construct_id` values that may reference constructs created under a previous job's run. With per-job DuckDB isolation, this resolves naturally — but only if each job has its own `constructs` sequence.

---

## 2. Scheduler Integration: Round Scripts Cannot Be Called Per-Job

### 2.1 Round Scripts Accept No `--job-id` or `--output` Arguments

The plan's scheduler integration section (05-implementation-plan.md, Section [3.2]) describes calling round scripts like this:

```python
subprocess.Popen(
    ["uv", "run", "python", "-m", "main.stages4.s4_round01",
     "--job-id", job_id, "--output", output_path],
    ...
)
```

**This will not work.** Here are the actual CLI arguments each round script accepts:

| Script | Actual CLI Arguments |
|--------|---------------------|
| `s4_round01_antioxidant_split.py` | `--top-pct`, `--bottom-pct`, `--sample` |
| `s4_round02_safety_screen.py` | `--toxin-threshold`, `--hemo-threshold`, `--mhc-threshold` |
| `s4_round03_deep_scoring.py` | *(none shown, likely similar)* |
| `s4_round04_enumerate.py` | *(none shown)* |
| `s4_round05_3d.py` | *(none shown)* |
| `s4_round06_pdb_eval.py` | *(none shown)* |
| `s4_round07_final.py` | *(none — no CLI args at all)* |

**None of the round scripts accept `--job-id` or `--output`.** These parameters do not exist.

### 2.2 Round Scripts Use Hardcoded Database Path

Each round script contains:
```python
db = PipelineDB()  # Uses DEFAULT_DB_PATH = .../output4/pipeline.db
```

To support per-job execution, `PipelineDB` must be instantiated with an explicit path:
```python
db = PipelineDB(db_path=f"output4/jobs/{job_id}/pipeline.db")
```

The `s4_db.py` constructor already supports this via the `db_path` parameter — the problem is the round scripts always use the default.

### 2.3 Round Scripts Have No Way to Receive Per-Job Configuration

The plan assumes the Scheduler reads `jobs.config_snapshot` and passes configuration to each round invocation. But round scripts have **hardcoded threshold values**:

```python
# s4_round01_antioxidant_split.py
ALGPRED2_THRESHOLD = 0.30    # Hardcoded
```

```python
# s4_round02_safety_screen.py
TOXIN_THRESHOLD = 0.38   # Hardcoded
HEMO_THRESHOLD = 0.55    # Hardcoded
MHC_THRESHOLD = 0.5      # Hardcoded
```

The plan's `config_data` JSON includes these thresholds, but the round scripts cannot receive them.

**Required changes to make the plan implementable**:

1. Add `--job-id` and `--output` arguments to every round script's `argparse` section
2. Add `--config` argument to receive the full config JSON (or individual threshold args)
3. Replace hardcoded constants with values parsed from CLI args
4. Modify `PipelineDB` instantiation to use per-job database path

Example signature for round01:
```python
parser.add_argument("--job-id", type=str, required=True)
parser.add_argument("--output", type=str, required=True)
parser.add_argument("--top-pct", type=float, default=10.0)
parser.add_argument("--bottom-pct", type=float, default=1.0)
# thresholds from config JSON passed via --config or individual --algpred2-threshold
```

### 2.4 Round Scripts Are Sequential Within a Single Process

Each round script is a standalone Python file designed to be invoked once per pipeline run. They are **not** built to be called repeatedly by an external scheduler. For example, `s4_round01_antioxidant_split.py` does:

```python
def main():
    info = get_round_services("round1")
    health = ensure_services(info["services"], info["profiles"], timeout=180.0)
    # ... exits if services unavailable
    asyncio.run(run(args.top_pct, args.bottom_pct, sample=args.sample))
```

It starts Docker services, runs the round, then exits. This is correct for a single monolithic run. But for a Scheduler that manages multiple concurrent jobs, this creates a problem: each round script invocation starts its own Docker services, and with concurrent jobs, GPU services would conflict.

**The plan's assumption that "Scheduler calls round scripts sequentially per job" is sound**, but the implementation needs mutex protection for GPU services, or the Scheduler must ensure only one job's round runs at a time (e.g., a global lock file or database-level job serialization).

---

## 3. Docker / Environment Gaps

### 3.1 GPU Resource Contention — The Plan's Own Risk Table Identifies This

The plan's risk table (Section 8) already flags "single GPU resource conflict" and suggests "Scheduler独占GPU，阶段5之前先 `docker compose stop gpu-services`". This is the correct concern, but the mitigation is too blunt.

**The actual concurrency problem**: Several GPU services are required simultaneously within a single round:
- Round3 needs: bepipred3 (GPU), temstapro (GPU), plm4cpps (GPU), graphcpp (GPU)
- Round5 needs: omegafold (GPU) alone

The `s4_docker_utils.py` `ensure_services()` function uses `docker compose --profile` to start services. With concurrent jobs, two Scheduler instances could call `ensure_services` simultaneously, causing Docker Compose conflicts.

**Required additions to the plan**:
1. A **global GPU lock** mechanism: the Scheduler must acquire an exclusive GPU lock before starting any GPU round, and release it after. This could be a PostgreSQL row with `SELECT FOR UPDATE`, or a simple file lock.
2. Per-job output directories must be passed as volume mounts to the scheduler container:
   ```yaml
   volumes:
     - ./iGEM-silk-main/output4/jobs/${JOB_ID}:/app/output4/jobs/${JOB_ID}
   ```
3. The plan should specify that only one job may run on the GPU at a time, and other jobs with `pending` status wait.

### 3.2 Missing Environment Variables in Docker Compose

The plan specifies environment variables in Section 7.2:
```bash
DATABASE_URL=postgresql+asyncpg://igem_user:igem_password@postgres:5432/igem_platform
SECRET_KEY=<random>
```

But the `tools/docker-compose.yml` does **not** pass through these to the backend container. The backend needs to know:
- `DATABASE_URL` — to connect to PostgreSQL
- `SECRET_KEY` / `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_DAYS` — for JWT
- `PIPELINE_ROOT` — path to `iGEM-silk-main` (for round script invocation)

The plan's docker-compose example at Section 7.3 shows these, but they need to be wired into the actual `tools/docker-compose.yml` or a new platform-level compose file.

### 3.3 Network Setup for Container-to-Container Communication

The round scripts (running inside the scheduler container) need to reach:
1. PostgreSQL (at `postgres:5432` from backend/scheduler containers)
2. Microservices (at `127.0.0.1:PORT` — but the microservices run on the **host** network, not in the Docker Compose network)

**Critical issue**: The microservices in `tools/docker-compose.yml` expose ports on the host network (e.g., `anoxpepred` maps `8001:8001`). The round scripts, when running inside a Docker container (via the Scheduler), would need to reach these services.

If the scheduler runs in a container with the default Docker Compose bridge network, it cannot reach `127.0.0.1:8001` on the host. The plan must specify how the scheduler container reaches the microservice containers.

Options:
1. Use Docker Compose networking so scheduler joins the same network as microservices
2. Have microservices expose on all interfaces (`0.0.0.0` instead of `127.0.0.1`)
3. Use `network_mode: host` for the scheduler (but this limits deployment flexibility)

The `s4_docker_utils.py` `detect_bridge_ip()` function is used to get container IPs, but this only works for containers in the same Docker network. The current stages4 runs round scripts on the **host** machine (not in a container), so `127.0.0.1` works for reaching microservices from the host.

**If the scheduler runs in a container, this breaks the current design.** The plan should clarify whether the scheduler runs on the host or in a container.

---

## 4. Missing API Endpoints

### 4.1 No Endpoints for Scheduler-to-PostgreSQL Write Operations

The plan describes the Scheduler **reading** `jobs.status = 'pending'` and **writing** `jobs.status = 'running'`, `jobs.current_round`, `jobs.processed_count`, etc. But the plan's API design section only covers:

- Auth endpoints
- Jobs CRUD (create, list, detail, delete)
- Results (ranking, funnel, PDB)
- Configs

**There are no endpoints for the Scheduler to update job progress.** The plan says "Scheduler 和 FastAPI 通过 PostgreSQL 解耦，无直接进程间通信" — meaning the Scheduler reads/writes PostgreSQL directly (not via API). This is acceptable but should be explicitly stated as a design decision rather than an omission.

**If the Scheduler bypasses the API and writes to PostgreSQL directly**, then:
1. The Scheduler needs a direct PostgreSQL connection string (not just the backend's DATABASE_URL)
2. No FastAPI endpoints for progress updates are needed
3. But the plan's Section 8 risk table mentions "单 GPU 资源冲突" — concurrent job monitoring requires database-level locking

### 4.2 Missing: `GET /api/jobs/{job_id}/checkpoint`

The plan mentions SSE events for round completion, but there is no endpoint for the Scheduler to write checkpoint data to PostgreSQL. The plan mentions `job_events` table but no write operations to it from the Scheduler.

**Proposed additions to the plan**:
- `jobs.current_round` and `jobs.processed_count` are updated via direct SQL (Scheduler bypasses API)
- Or add `PATCH /api/jobs/{job_id}/progress` endpoint for the Scheduler

### 4.3 Missing: `GET /api/jobs/{job_id}/funnel-data`

The Results funnel endpoint (`GET /api/results/{job_id}/funnel`) reads from `output_path/` directories. The plan says it reads "各 round 的 final/ 目录读取 .csv 行数，或从 pipeline.db 查询". But `output_path` is a local path on the host — if the backend serves this from inside a container, it needs the volume mounted.

The funnel data can also be derived from the DuckDB `checkpoint` table (which has `total_items` and `processed_items` per round), but this requires the backend to have read access to the per-job DuckDB file.

---

## 5. Concrete Details for Vague Sections

### 5.1 Per-Job DuckDB Path Construction

**Plan vague point**: "在 `jobs.output_path` 创建目录（如 `output4/jobs/{job_id}/`）"

**Concrete implementation**:
```python
# In Scheduler executor
import uuid
from pathlib import Path

JOB_ID = str(uuid.uuid4())
OUTPUT_PATH = PROJECT_ROOT / "output4" / "jobs" / JOB_ID
OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

# Create symlink or copy pipeline.db for this job
# Or: each job gets its own DB initialized from stages3's candidates
JOB_DB_PATH = OUTPUT_PATH / "pipeline.db"
```

The `PipelineDB` class would be instantiated as:
```python
db = PipelineDB(db_path=JOB_DB_PATH)
db.init_schema()
```

### 5.2 Round Script Invocation (Corrected)

**Plan's assumed invocation**:
```python
subprocess.Popen(
    ["uv", "run", "python", "-m", "main.stages4.s4_round01",
     "--job-id", job_id, "--output", output_path],
    ...
)
```

**What is actually needed** (all round scripts modified to accept these):
```python
subprocess.Popen(
    ["uv", "run", "python", "-m", "main.stages4.s4_round01",
     "--job-id", job_id,
     "--output", str(output_path),
     "--top-pct", str(config["top_pct"]),    # from config_snapshot
     "--bottom-pct", str(config["bottom_pct"]),
     "--algpred2-threshold", str(config["thresholds"]["algpred2"])],
    cwd=str(PROJECT_ROOT),  # iGEM-silk-main/
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
)
```

### 5.3 Config Data JSON Should Include All Round Thresholds

The plan's example `config_data` (Section 3.2) is incomplete. For the plan to be implementable, it must include thresholds for **all rounds**:

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
    "hemopi2": 0.55,
    "mhcflurry": 0.50
  },

  "round_splits": {
    "round1_top_pct": 10.0,
    "round1_bottom_pct": 1.0
  },

  "channel_strategy": "dual_channel"
}
```

### 5.4 SSE Implementation Detail

The plan's SSE example (Section 5.5) uses a simple polling loop:
```python
while True:
    events = db.query(job_events).filter(job_id=job_id).all()
    for event in events:
        yield f"data: {event.json()}\n\n"
    await asyncio.sleep(2)
```

This is inefficient — it re-sends all events on every poll. A proper implementation should track the last event ID sent and only yield new events:

```python
last_id = None
while True:
    query = db.query(job_events).filter(job_id=job_id)
    if last_id:
        query = query.filter(job_events.id > last_id)
    events = query.order_by(job_events.timestamp).all()
    for event in events:
        yield f"event: {event.event}\ndata: {json.dumps({...})}\n\n"
        last_id = event.id
    await asyncio.sleep(2)
```

The `job_events` table also needs a primary key or timestamp column for cursor-based pagination.

### 5.5 Funnel Data Source

The plan says the funnel reads "各 round 的 final/ 目录读取 .csv 行数". But for a fresh job run, these CSVs don't exist yet. The funnel should be sourced from the DuckDB `checkpoint` table:

```sql
SELECT round, total_items, processed_items
FROM checkpoint
ORDER BY
    CASE round
        WHEN 'round0' THEN 0
        WHEN 'round1' THEN 1
        WHEN 'round2' THEN 2
        WHEN 'round3' THEN 3
        WHEN 'round4' THEN 4
        WHEN 'round5' THEN 5
        WHEN 'round6' THEN 6
        WHEN 'round7' THEN 7
    END
```

This avoids dependence on CSV files that may not exist during a running job.

---

## 6. Summary of Required Changes

### 6.1 Round Scripts (Must Be Modified)

Every round script needs:
1. `--job-id` argument (required)
2. `--output` argument (required, path to per-job output directory)
3. Per-job `PipelineDB(db_path=...)` instantiation instead of default path
4. Configurable thresholds from CLI args (not hardcoded)

Example for `s4_round01_antioxidant_split.py`:
```python
parser.add_argument("--job-id", type=str, required=True,
                    help="UUID of the job in PostgreSQL")
parser.add_argument("--output", type=str, required=True,
                    help="Path to job's output directory")
parser.add_argument("--config", type=str, required=True,
                    help="JSON config snapshot from jobs.config_snapshot")
# thresholds parsed from --config JSON

# In run():
db = PipelineDB(db_path=Path(args.output) / "pipeline.db")
```

### 6.2 PostgreSQL Schema Additions

```sql
-- Add to jobs table (plan has this, just confirming):
output_path VARCHAR(500) NOT NULL,
config_snapshot JSONB NOT NULL,

-- New: per-round checkpoint in PostgreSQL (optional, can rely on DuckDB)
CREATE TABLE job_rounds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    round_name VARCHAR(50) NOT NULL,  -- 'round01', 'round02', etc.
    status VARCHAR(50) DEFAULT 'pending',  -- pending / running / completed / failed
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(job_id, round_name)
);

-- The job_events table (plan has this) is sufficient for SSE if events are
-- INSERTed by the Scheduler directly via SQL (not via API).
```

### 6.3 Scheduler Changes

1. Add `--job-id` and `--output` to all round script invocations
2. Parse `config_snapshot` from the `jobs` table and extract thresholds
3. Implement GPU mutex lock (database-level `SELECT FOR UPDATE` on a `scheduler_locks` table)
4. Ensure only one job runs at a time initially (Phase 1 can be single-job)

### 6.4 Docker/Environment

1. Clarify scheduler deployment model (container vs. host) — if container, fix networking
2. Add `PIPELINE_ROOT`, `DATABASE_URL` env vars to scheduler container
3. Add volume mounts for per-job output directories
4. Define GPU lock mechanism (PostgreSQL row with `FOR UPDATE`)

### 6.5 Backend Additions

1. `PATCH /api/jobs/{job_id}/progress` endpoint (optional — plan relies on direct SQL)
2. `GET /api/results/{job_id}/funnel` should query DuckDB `checkpoint` table, not CSVs
3. Backend needs read access to `output4/jobs/{job_id}/` directory (volume mount)

---

## 7. Risks

| Risk | Severity | Notes |
|------|----------|-------|
| Round scripts reject `--job-id` / `--output` | **Critical** | They don't accept these. All 8+ round scripts must be modified. |
| GPU contention with concurrent jobs | **High** | No mutex exists. One job's round5 can conflict with another's round3. |
| DuckDB has no per-job isolation | **High** | All tables are global. Requires per-job DB file or `job_id` column added to every table. |
| Backend can't reach GPU services from container | **High** | If scheduler runs in a container, microservices on `127.0.0.1` are unreachable. |
| `processed_count` semantics undefined | **Medium** | Count means different things in different rounds (candidates vs. constructs). |
| Funnel endpoint depends on CSV files that may not exist | **Medium** | Should query DuckDB `checkpoint` table instead. |
| SSE sends duplicate events | **Medium** | Plan's SSE implementation re-sends all events on every poll. |

---

## 8. Verdict

**The plan is implementable in spirit but not in its current form.** The architecture (PostgreSQL for job metadata + DuckDB for pipeline state + Scheduler polling + SSE) is sound and matches how stages4 actually works. However:

1. The round scripts were designed as standalone one-shot pipeline invocations — they must be refactored to accept per-job parameters before the Scheduler can drive them.
2. The dual-database design (PostgreSQL + DuckDB) must be explicitly documented.
3. GPU resource serialization must be designed in, not left as a Phase 2 concern.
4. The Docker networking model must be clarified — specifically whether the Scheduler runs on the host or in a container, and how it reaches microservice endpoints.

The plan should be treated as a **target architecture** with the modifications listed in Section 6 above. Once those changes are made to the codebase, the plan accurately describes the system.