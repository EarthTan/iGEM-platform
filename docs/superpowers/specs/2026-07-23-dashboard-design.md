# Dashboard — 实时进度监控设计 Spec

**日期:** 2026-07-23
**作者:** Zed
**目标项目:** `/home/lenovo/Projects/iGEM-platform`
**目标代码:** `src/setup/dashboard/`
**关联 spec:**
- `2026-07-10-igem-peptide-database-design.md`(本仓库总体设计)
- `2026-07-16-igem-peptide-database-data-source-refresh-v2.md`(enrich pipeline 即跑之物)

**本文定位:** 一个**只读、HTTP 暴露、局域网可访问**的 dashboard,用于在不打断长跑 enrichment 的前提下,从任何设备观察 pipeline 进度 + 系统负载 + 进程状态。

---

## 0. 核心硬约束(写在最前)

| 约束 | 含义 |
|---|---|
| **零写操作** | dashboard 进程对 PG / 文件 / 进程 / HTTP 服务全部只读,无 INSERT/UPDATE/DELETE/tee/curl POST/curl PUT/process kill |
| **零控制功能** | 不暴露 pause / skip / restart / kill 任何控制接口 |
| **零锁/零占用** | dashboard 读 master log / 子日志 **不得**妨碍 enrich.py 写入(详见 §5.1.1) |
| **零持久化** | dashboard 自身不写文件(不写 SQLite、不写 state.json、不写 lock),除了 Python 解释器自动产物 `__pycache__` |
| **零副作用轮询** | 服务端口探针 `?probe=1` 默认关闭,polling 走状态缓存,不在长跑中的服务上堆压 |
| **唯一接口** | `GET /` 和 `GET /api/state`,全部幂等,无副作用 |

任何违反以上约束的代码改动必须在 PR 描述里显式标注原因,默认拒绝。

---

## 1. 背景与动机

### 1.1 现状

- 在跑 enrichment:9 个工具串行,预计 7.4 天
- 当前观测手段:
  - `tail -f logs/enrich_run/master_*.log`(只在当前终端)
  - `progress_watch.sh`(每 60s 写 `/tmp/enrich_progress.txt`,本地)
  - `psql` 查 `v_peptide_enrichment_coverage`
- 痛点:换设备(iPad/手机/另一台电脑)看不到进度,必须 SSH 到服务器

### 1.2 已有结构化日志

`logs/enrich_run/master_*.log` 和 `logs/enrich_run/{tool}.log` 含有结构化行:

```
[2026-07-21T13:08:17] START tool=sodope batch=1000
...
13:08:30 INFO batch done | last_id=12923319 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=12635700 (62.4%) | ETA=0m00s
...
13:08:29 INFO start tool=sodope batch=1000 eligible_remaining=7614185 done_so_far=12634700 done_set_size=12634700 from_id=12922319
```

正则可解析,直接复用。

### 1.3 目标

- 局域网任何设备打开浏览器看到:
  - 当前在跑哪个工具、当前 batch 进度、ETA、速率
  - 9 个工具的完成度全景
  - 进程级 CPU / 内存 / 显存占用
  - 系统 CPU / 内存 / GPU 整机负载
  - 最近 60s 的 batch 速率趋势(轻量折线)
  - 最近 30 批次的明细、最近 30 行日志尾巴
- 主路径零控制(看就行),唯一参数 `?probe=1` 才能探针服务端口

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│ Browser (any device on LAN)                             │
│  GET /                       HTML index                  │
│  GET /api/state              JSON snapshot (polling 2s)  │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼  read-only
┌─────────────────────────────────────────────────────────┐
│ Flask app (single process, single thread is fine)        │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐ │
│  │ LogTailer   │  │ DbReader    │  │ ProcInspector    │ │
│  │ (logparser) │  │ (psycopg)   │  │ (psutil)         │ │
│  └─────┬───────┘  └─────┬───────┘  └────────┬─────────┘ │
│  ┌─────────────┐                                         │
│  │ SysMon      │ (psutil + nvidia-smi)                   │
│  │ (sysmon)    │                                         │
│  └─────┬───────┘                                         │
│        └─────────────┬───────────────────┬──────────────┘│
│                ┌───────▼──────┐                           │
│                │ StateCache   │  (in-memory, 2s tick)     │
│                └───────┬──────┘                           │
│                        │                                  │
│                ┌───────▼──────┐                           │
│                │ /api/state   │  JSON                     │
│                └──────────────┘                           │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼  read-only(零写操作)
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ master.log   │  │ {tool}.log   │  │ PG views     │
   │ + 子日志     │  │ + checkpoint │  │ psql stats   │
   └──────────────┘  └──────────────┘  └──────────────┘
```

### 2.1 进程拓扑

- 一台机器单进程 dashboard:`python3 -m src.setup.dashboard`
- 后台一个 `threading.Thread` ticker,每 2s 调一次 `state.update()`
- Flask 主线程处理 HTTP,无写操作,安全单线程
- dashboard 进程崩溃:浏览器立即 `state.generated_at` 不再更新,可作为 alive 指标

### 2.2 端口与绑定

- 默认 `127.0.0.1:8088`,只本机访问
- 传 `--host 0.0.0.0` 暴露 LAN,README 必须配防火墙提示
- 默认端口 **不与已有 9 个服务冲突**(8001/8003/8004/8005/8006/8007/8008/8010/8012)

---

## 3. 文件结构

```
src/setup/dashboard/
├── __init__.py
├── app.py              # Flask app + 路由 + 启动入口(argparse)
├── state.py            # StateCache(2s 后台 tick,聚合所有数据)
├── logparser.py        # 解析 batch done / START / start 行
├── db_reader.py        # 只读 PG:coverage view + pg_stat_activity + db size
├── proc_inspector.py   # psutil: enrich.py / run.sh / 9 个 svc 进程
├── sysmon.py           # psutil + nvidia-smi:CPU/MEM/GPU 整机 + 进程级
├── templates/
│   └── index.html      # 单页,Jinja 渲染 + 极少量 JS(polling)
└── static/
    └── style.css       # 单文件,纯 CSS 无 JS 框架

requirements.txt         # Flask>=3.0, psutil>=5.9  (新增,本来没有)
src/setup/dashboard/README.md  # 启动 + 防火墙 + 已知限制
```

---

## 4. 数据流 / State 缓存

### 4.1 Tick 机制

- `state.py` 内 `threading.Thread(daemon=True, target=tick_loop)`
- 每 2s 调一次 `_refresh()`:
  1. `logparser.LogTailer.read_recent()` → 当前 tool + recent_batches + rate_history + log_tail
  2. `db_reader.read_coverage()` → tools_overview
  3. `db_reader.read_db_stats()` → db info
  4. `proc_inspector.snapshot()` → enrich / run.sh / svc 进程
  5. `sysmon.snapshot()` → system + tracked_procs + top_procs(probe 配在 `proc_inspector`,不在 SysMon)
- 任一子系统抛异常 → stderr 记一次,tick 继续,旧 state 保留
- `/api/state` 直接返回内存 dict,不重新计算

### 4.2 State 形状(/api/state JSON)

```json
{
  "generated_at": "2026-07-23T14:23:15",
  "dashboard_version": "0.1.0",
  "current_run": {
    "master_log": "/home/lenovo/Projects/iGEM-platform/logs/enrich_run/master_20260721_130817.log",
    "tool": "algpred2",
    "tool_started_at": "2026-07-21T13:08:17",
    "elapsed": "2d 1h 14m",
    "concurrent": 1,
    "batch_size": 1000,
    "last_batch": {
      "last_id": 12959326,
      "elapsed_s": 0.05,
      "rate": 17663.0,
      "done": 12671700,
      "done_pct": 62.5,
      "eta": "0m00s"
    },
    "recent_batches": [
      {"last_id": 12959326, "elapsed_s": 0.05, "rate": 17663.0, "done": 12671700, "done_pct": 62.5, "eta": "0m00s"}
    ],
    "rate_history": [
      {"t": 1721734990, "rate": 17663.0},
      {"t": 1721734992, "rate": 17058.1}
    ],
    "log_tail": [
      "13:08:50 INFO batch done | last_id=12959326 ..."
    ]
  },
  "tools_overview": [
    {"tool": "sodope",     "done": 7614185,  "eligible": 7614185,  "pct": 100.0, "status": "done"},
    {"tool": "tipred",     "done": 8200000,  "eligible": 8200000,  "pct": 100.0, "status": "done"},
    {"tool": "algpred2",   "done": 12671700, "eligible": 20248885, "pct": 62.5,  "status": "running"},
    {"tool": "anoxpepred", "done": 0,        "eligible": 20248885, "pct": 0.0,    "status": "pending"}
  ],
  "processes": {
    "enrich_py": [
      {"pid": 1234, "cmd": "enrich.py --tool algpred2 --batch 1000", "elapsed": "2h 14m"}
    ],
    "master_sh": [
      {"pid": 1000, "cmd": "run.sh", "elapsed": "2d 1h"}
    ],
    "services": {
      "sodope":     {"pid": null, "port": 8012, "alive": null, "rtt_ms": null},
      "algpred2":   {"pid": 2000, "port": 8008, "alive": true,  "rtt_ms": 2.1}
    }
  },
  "db": {
    "active_connections": 12,
    "active_queries": 1,
    "db_size": "8.2 GB"
  },
  "system": {
    "cpu": {
      "pct": 42.3,
      "per_core": [38.1, 45.2, 41.0, 44.9],
      "load_avg": [1.2, 1.5, 1.8],
      "n_cores": 16
    },
    "memory": {
      "total_gb": 64.0,
      "used_gb": 41.2,
      "pct": 64.4,
      "available_gb": 22.8
    },
    "gpu": [
      {
        "index": 0,
        "name": "NVIDIA RTX 4090",
        "util_pct": 78.0,
        "mem_used_gb": 18.4,
        "mem_total_gb": 24.0,
        "mem_pct": 76.7,
        "temp_c": 72,
        "power_w": 285.0
      }
    ],
    "tracked_procs": [
      {"pid": 1234, "cmd": "enrich.py --tool algpred2", "cpu_pct": 8.2, "mem_rss_mb": 412.0, "gpu_mem_mb": 0},
      {"pid": 5678, "cmd": "service.py",                "cpu_pct": 24.1, "mem_rss_mb": 1820.0, "gpu_mem_mb": 14500.0}
    ],
    "top_procs": [
      {"pid": 9999, "user": "root",  "cmd": "python3 some_thing.py", "cpu_pct": 92.0, "mem_rss_mb": 2048.0, "gpu_mem_mb": 0},
      {"pid": 8888, "user": "lenovo", "cmd": "ffmpeg -i ...", "cpu_pct": 85.0, "mem_rss_mb": 1024.0, "gpu_mem_mb": 0}
    ]
  }
}
```

### 4.3 字段语义

- `current_run.tool`:从最近一个 `START tool=X` 解析;若 60s 内无 batch done → 视为停滞
- `current_run.elapsed`:`tool_started_at` 到现在
- `current_run.recent_batches`:最近 30 个 `batch done` 解析行
- `current_run.rate_history`:time-bucketed,2s 一桶,最近 60s(30 个点)
- `tools_overview.status`:`running` / `done` / `pending` / `failed`,v1 **只可能产生前三种**(`failed` 状态保留 enum 槽位,触发逻辑暂不实现)
- `processes.services[*].alive`:null 表示未探针(true/false 表示 `?probe=1` 后结果)
- `system.gpu`:空数组表示无 GPU 或 `nvidia-smi` 不可用
- `system.tracked_procs`:enrich.py + run.sh + 9 个 svc PID 解析后,交叉 psutil 拿 cpu/rss/gpu_mem
- `system.top_procs`:全系统 psutil 排序,Top 10 by cpu%

---

## 5. 组件详细设计

### 5.1 `logparser.py`

#### 5.1.1 锁保证(与 enrich.py 写入共存)

dashboard 读文件 **绝不会** 妨碍 enrich.py 写文件。具体保证:

- **只读句柄**:使用 `open(path, "rb")`,**不传 `+` / `w` / `a`**,内核不会拦截 writer
- **不做 advisory lock**:Python `open()` 默认不获取 flock,enrich.py 那边即使有 flock 也互不阻塞
- **不做强制锁**:Python `open()` 在 Linux 默认是 mandatory lock 关闭,假如 admin 开了 `mount -o mand`,我们仍因只读 `O_RDONLY` 与 writer 共存
- **增量 seek**:只 `seek(current_offset)` 往后读,**永不 seek(0) / truncate / fsync**
- **不 fork 外部 reader**:绝不调 `tail -F` / `cat` / `head`,纯 Python `read()`,崩了无僵尸
- **不开 subprocess shell**:不用 `subprocess.Popen(..., shell=True)`,不会保留 fd
- **限制每次读取**:每 tick 最多 `read(64 * 1024)`,避免一个被污染的巨行无限增长内存
- **打开错误不重试**:master log 不存在 → `current_run: null`(详见 §8),enrich 启动后再出现 master log,下一次 `ls -t` 自动找到
- **文件被原子替换**:`run.sh` 不会 mv master log,但 v1 防御:tick 内 `os.path.getmtime` 变化 → 重打开,旧 fd 释放

**验证**(验收项 C7):同台机器同时跑 enrich.py + dashboard,后者持续读 master log 1 小时,enrich.py 写入 `INFO batch done` 计数与不跑 dashboard 时一致。

**正则**(从已观测日志反推,v1 写死):

```python
BATCH_RE = re.compile(
    r"batch done \| last_id=(\d+) size=(\d+) inserted=(\d+) errs=(\d+) "
    r"\| elapsed=([\d.]+)s \| rate=([\d.]+) seq/s "
    r"\| done=(\d+) \(([\d.]+)%\) \| ETA=(.+?)(?:\s*$)"
)
START_RE = re.compile(r"START tool=(\w+) batch=(\d+)")
START2_RE = re.compile(
    r"start tool=(\w+) batch=(\d+) eligible_remaining=(\d+) "
    r"done_so_far=(\d+) done_set_size=(\d+) from_id=(\d+)"
)
```

**`LogTailer` 类**:
- 构造时记录 master_log 最新路径(`ls -t $LOG_DIR/master_*.log | head -1`)
- `read_recent()`:
  - 打开文件,`seek` 到上次 read offset
  - 读直到 EOF(限制 64KB/tick 防止异常日志暴增)
  - 解析 BATCH_RE / START_RE,把更新写回状态
- **不旋转日志**:同一次 run 内 master log 不会变,mtime 变了才重新 open
- 新日志检测:`ls -t` 找更新的 master_*.log
- log_tail 走 ANSI strip:正则去 `\x1b\[[0-9;]*m`

### 5.2 `db_reader.py`

**psycopg 连接**:复用 `enrich/lib/db.py` 里的连接构造(DSN、密码从 env 读),**不重写一遍**。

**只读 SQL**(v1):
```sql
-- 工具覆盖度
SELECT tool, done_count, eligible_count, ... FROM v_peptide_enrichment_coverage;

-- 活跃连接与查询
SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL;
SELECT count(*) FROM pg_stat_activity WHERE state = 'active';

-- DB 大小
SELECT pg_size_pretty(pg_database_size('igem_peptides'));
```

**事务**:`SET TRANSACTION READ ONLY` 防御性,确保即使 query 写错也回滚。

**失败**:连接失败 → stderr 记一次,`db: null`,前端展示 "DB unreachable"。

### 5.3 `proc_inspector.py`

**进程枚举**:`psutil.process_iter(['pid', 'name', 'cmdline', 'create_time'])`

**识别**:
- `enrich.py`:`cmdline` 包含 `enrich.py --tool`
- `run.sh`:`cmdline` 包含 `run.sh` 或 `src/setup/enrich/run.sh`
- 9 个 svc:`cmdline` 包含 `service.py` AND 端口匹配(start_services.sh 写的映射)

**9 服务→端口映射**(硬编码,跟 `start_services.sh` 保持一致):
```python
SERVICE_PORTS = {
    "tipred": 8007,
    "algpred2": 8008,
    "SoDoPE_paper_2020": 8012,
    "AnOxPePred": 8001,
    "HemoPI2": 8004,
    "pLM4CPPs": 8006,
    "MHCflurry": 8005,
    "TemStaPro": 8010,
    "ToxinPred3": 8003,
}
```

**探针**(`?probe=1`):
- `httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)`
- 串行打 9 个,每 2s tick 才一次
- 不打 `/predict` / 不发 POST

**无信号**:`psutil` 进程枚举不发送任何信号,只读。

### 5.4 `sysmon.py`

**CPU**:
- `psutil.cpu_percent(interval=0.1, percpu=True)` → 整机 + 每核
- **选择在 tick 内阻塞 100ms 拿首批有效值**(vs. `interval=None` 首次返回 0 需预热 1 tick),权衡:dashboard 进程本身只在 2s tick 阻塞 100ms,长跑不受影响；用户看到的状态从第一帧起就有 CPU 数字,不需要忍受空白 2s

**内存**:`psutil.virtual_memory()` → total/used/available/percent

**Load avg**:`os.getloadavg()`(Linux 一定有)

**GPU**:
```python
result = subprocess.run(
    ["nvidia-smi",
     "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
     "--format=csv,noheader,nounits"],
    capture_output=True, text=True, timeout=2.0
)
```
- 解析 csv,每个 GPU 一行
- 失败 / 超时 / 不存在 → `gpu: []`,stderr 记一次,**不阻塞其他 state**

**进程级 GPU**:
```python
result = subprocess.run(
    ["nvidia-smi",
     "--query-compute-apps=pid,used_memory",
     "--format=csv,noheader,nounits"],
    capture_output=True, text=True, timeout=2.0
)
```
- 返回 `pid → gpu_mem_mb` 映射
- 在 `tracked_procs` 和 `top_procs` 中交叉填 `gpu_mem_mb`

**top_procs 排序**:
- `psutil.process_iter(...)` 全部遍历
- 按 `cpu_percent()` 倒序,等值时按 `memory_info().rss` 倒序
- 取前 10
- 过滤:root 用户保留 + lenovo 用户保留 + 其他用户显示但不带 cmdline(隐私)

**磁盘 / 网络 / 温度**:v1 **不做**(btop 完整版不在范围)

### 5.5 `state.py`

```python
class StateCache:
    def __init__(self, log_dir, dashboard_root, probe=False):
        self._state = {}
        self._lock = threading.Lock()
        self._log_tailer = LogTailer(log_dir)
        self._db = DbReader()
        self._proc = ProcInspector()
        self._sysmon = SysMon()
        self._probe = probe
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._last_tick = None
        self._last_tick_error = None

    def start(self):
        self._tick_thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _tick_loop(self):
        while True:
            try:
                self._refresh()
            except Exception as e:
                self._last_tick_error = repr(e)
                sys.stderr.write(f"[state] tick error: {e}\n")
            time.sleep(2.0)

    def _refresh(self):
        new_state = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dashboard_version": "0.1.0",
            "current_run": self._log_tailer.read_recent(),
            "tools_overview": self._db.read_coverage(),
            "db": self._db.read_db_stats(),
            "processes": self._proc.snapshot(),
            "system": self._sysmon.snapshot(self._proc.snapshot()),
        }
        with self._lock:
            self._state = new_state
            self._last_tick = time.time()
```

### 5.6 `app.py`

```python
app = Flask(__name__)
state_cache: StateCache = None  # 启动时注入

@app.route("/")
def index():
    return render_template("index.html", initial_state=state_cache.snapshot())

@app.route("/api/state")
def api_state():
    return jsonify(state_cache.snapshot())

@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "last_tick": state_cache._last_tick})

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--log-dir", default="/home/lenovo/Projects/iGEM-platform/logs/enrich_run")
    p.add_argument("--probe", action="store_true", help="开启 9 服务端口探针")
    args = p.parse_args()

    global state_cache
    state_cache = StateCache(log_dir=args.log_dir, probe=args.probe)
    state_cache.start()

    app.run(host=args.host, port=args.port, threaded=True, debug=False)
```

**路由清单**:
- `GET /` → HTML
- `GET /api/state` → JSON
- `GET /api/health` → JSON(dashboard 自身 alive)

**没有其他路由**。所有路径 GET,无 POST/PUT/DELETE。

### 5.7 前端

**设计原则:后端为主,UI 极简**。v1 原则:
- 不做暗黑主题(默认浏览器背景)
- 不做响应式(桌面浏览器看就行)
- 不做折线图(选项 5 删除)
- 不做独立 "Recent batches" 表(合成到 current_run.last_batch 一行)
- 不做独立 "Tracked pipeline processes" 块(合并到 Top 10)
- 允许进度条退化:用文字明示 `done=12671700 / 20248885 (62.5%)`,不画 div
- 允许 9 工具覆盖度退化:不画状态 icon,只用文字 `done|running|pending`

**`templates/index.html`**:
- Jinja 渲染初始 state(防白屏)
- ~30 行 JS:`fetch('/api/state').then(render); setInterval(() => fetch(...), 2000)`
- 失败重试:指数退避 2s → 4s → 8s → 上限 30s
- 不引入任何 JS 框架 / 图表库

**`static/style.css`**:
- **单文件 ≤ 50 行**
- 系统字体栈
- 不画 progress bar(用文字)
- 表格用原生 `<table>`,浏览器默认样式
- 唯一定制:mono 字体给数字列,空白表不增加 row-gap

**布局顺序**(顶到底,合计 5 块):
1. **Header:** generated_at + 手动刷新 + 跳探针开关 `?probe=1`
2. **Current run:** tool / port / started / elapsed / done / total / pct / last_rate / ETA (一行或两面表格)
3. **Tools overview:** 9 工具表 — tool | done / eligible | pct | status(running / done / pending)
4. **System:** CPU / MEM / GPU(有 n 个列 n 行) + Top 10 procs by CPU
5. **Log tail:** 最近 30 行 plain text,pre 标签

**删除项**:
- ~~Rate trend SVG~~
- ~~Recent batches 表~~
- ~~Tracked pipeline processes 独立块(合并到 Top 10)~~
- ~~暗黑主题~~
- ~~移动端响应式~~
- ~~进度条 div~~

---

## 6. 启动方式

### 6.1 安装

```bash
# 沿用项目已有 Python 环境,装两个新包
pip install -r requirements.txt
# 或:pip install --break-system-packages Flask>=3.0 psutil>=5.9
```

### 6.2 启动

```bash
# 默认:只本机
python3 -m src.setup.dashboard

# 局域网访问
python3 -m src.setup.dashboard --host 0.0.0.0 --port 8088

# 开启服务端口探针(可选)
python3 -m src.setup.dashboard --host 0.0.0.0 --probe
```

浏览器:`http://<server-ip>:8088/`

### 6.3 防火墙

README 必须写:
```
# 仅允许内网网段(假设 192.168.1.0/24)
sudo ufw allow from 192.168.1.0/24 to any port 8088
# 不要暴露公网:无 auth,纯信任 LAN
```

---

## 7. 测试策略

### 7.1 单元测试

- `logparser`:喂固定的 master log 切片,断言 BATCH_RE 提取正确
- `db_reader`:用 SQLite 内存库 mock,断言 SQL 只读且状态正确
- `proc_inspector`:mock `psutil.process_iter`,断言识别逻辑
- `sysmon`:mock `psutil` 和 `nvidia-smi`,断言 fields
- `state.StateCache`:mock 各子系统,断言聚合 + 异常不崩

### 7.2 集成测试

- `python3 -m src.setup.dashboard --port 18088`
- `curl http://127.0.0.1:18088/api/state` 断言 JSON 结构
- `curl http://127.0.0.1:18088/api/health` 断言 `ok=true`
- `curl -X POST http://127.0.0.1:18088/api/state` 断言 405(没有 POST 路由)

### 7.3 离线 fixture

- `tests/fixtures/master_sample.log`:20 行的真实日志子集
- `tests/fixtures/coverage_sample.json`:v_peptide_enrichment_coverage 的 mock 输出

### 7.4 不测

- 真实浏览器 UI(纯用户验收)
- 真实 nvidia-smi 集成(只 mock)

---

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| master log 不存在 | `current_run: null`,前端显示 "no master log found" |
| DB 不可达 | `db: null`,前端显示 "DB unreachable" |
| nvidia-smi 失败 | `gpu: []`,前端显示 "no GPU" |
| psutil 进程消失 | `tracked_procs` 不显示该 PID,`top_procs` 不显示 |
| 子系统异常 | stderr 记一次,旧 state 保留,`generated_at` 继续更新 |
| dashboard 自身崩溃 | 浏览器发现 `generated_at` 停止,可视作 alive 指标 |
| 端口被占 | argparse 报错,exit 1 |

---

## 9. v1 明确不做(YAGNI 清单)

| 不做 | 原因 |
|---|---|
| 认证 / token | LAN 信任模型,RFC 已在 README 写"不要暴露公网" |
| 控制接口(pause/skip/restart/kill) | 用户硬约束,零控制 |
| 历史 run 回放 | 单次长跑,历史无价值 |
| SSE / WebSocket | polling 2s 够用,降低复杂度 |
| Alerts / 通知 | 用户没要求,且违反"只读"原则 |
| 磁盘 IO / 网络 IO / 温度 / 风扇 | btop 完整版不在范围 |
| 进程树 | 信息密度低,YAGNI |
| 磁盘 / 网卡配置面板 | 监控面板不是配置面板 |
| 写入 state 文件 / SQLite | 违反"零持久化" |
| 跨主机 dashboard | 单机长跑,无 v1 场景 |
| 任何 POST/PUT/DELETE 路由 | 违反"零控制" |
| 自定义主题 / 样式切换 | 暗色 + 响应式 |

---

## 10. 风险与已知限制

| 风险 | 缓解 |
|---|---|
| dashboard 读 log 锁住文件 → enrich.py 写不进去 | §5.1.1 锁保证:只读句柄 + 无 advisory lock + 只 seek 增量 + 限 64KB/tick + 不 fork 外部 reader |
| dashboard 自己的 polling 加重 CPU | 2s tick + 缓存,browser 端每 2s 一次 fetch 仅读内存 |
| `?probe=1` 在 9 个 svc 健康时打 9 次/health | 串行,2s tick 一次,服务/health 通常 < 5ms |
| `nvidia-smi` 调用开销 | 单次 ~50ms,2s tick 一次,可接受 |
| 9 个 PID 匹配失败时显示空 | 二进制 cmdline 不可读 → `cmd: "<unknown>"`,不崩 |
| master log 写入异常导致 read 不完整 | 增量读,下次 tick 重试,不污染旧 state |
| dashboard 与 long run 进程争抢 psutil | psutil 不阻塞,枚举 ~10ms,可接受 |
| 多 master log 切换 | `ls -t` 找最新,新文件出现时切换,旧 state 保留直到第一个新行 |
| 浏览器自动 polling 触发 server 端口限制 | Flask 单线程够用,长跑不会把 dashboard 打挂 |

---

## 11. 验收标准

1. ✅ `python3 -m src.setup.dashboard` 启动无报错
2. ✅ 浏览器打开 `http://127.0.0.1:8088/` 看到 5 块内容(Header / Current run / Tools overview / System / Log tail)
3. ✅ `python3 -m src.setup.dashboard --host 0.0.0.0` 后,从同网段另一台设备能打开
4. ✅ 2s 后 current_run / system / log tail 自然刷新(不用手动)
5. ✅ `?probe=1` 后,9 个 svc 的 `alive/rtt_ms` 显示实时值
6. ✅ `curl -X POST http://127.0.0.1:8088/api/state` 返回 405
7. ✅ dashboard 进程 grep 不到 `INSERT|UPDATE|DELETE|curl.*-X POST|nvidia-smi ... -dm`、或任何写操作
8. ✅ `find src/setup/dashboard -name '*.py' -exec grep -l 'INSERT\|UPDATE\|DELETE\|exec\|os.system\|subprocess.*shell=True' {} \;` 输出为空
9. ✅ README 包含防火墙提示
10. ✅ 单元测试 + 集成测试全过
11. ✅ **锁保证验收**:同台机器同时跑 enrich.py + dashboard 1h,enrich 写入行数 与 不跑 dashboard 时一致(允许 ±0 行偏差)

---

## 12. 依赖新增

`requirements.txt`(v1 新增文件,本来仓库没有):
```
Flask>=3.0
psutil>=5.9
```

**不引入**:
- `pynvml`(C 依赖,直接调 `nvidia-smi` 即可)
- `httpx` / `requests` 的新版本(项目已有,沿用)
- 任何前端框架 / 图表库 / CSS 框架

---

## 13. 实施拆分(后续 writing-plans 阶段)

预估:
- M1:logparser + 单测(0.5d)
- M2:db_reader + 单测(0.5d)
- M3:proc_inspector + 单测(0.5d)
- M4:sysmon + 单测(0.5d)
- M5:state.py 聚合(0.5d)
- M6:app.py + 极简 HTML/CSS(0.5d)
- M7:集成测试 + 锁保证验收 + README(0.5d)

总计 ~3 工作日。
