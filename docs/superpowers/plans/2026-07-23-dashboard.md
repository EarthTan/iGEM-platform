# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, LAN-accessible Flask dashboard at `src/setup/dashboard/` that polls the running enrichment pipeline + system stats without interfering with the writer.

**Architecture:** Single Flask process with one background `threading.Thread` that ticks every 2s, aggregates state from logparser / db_reader / proc_inspector / sysmon into an in-memory dict, and serves it via `GET /api/state`. Frontend is a single Jinja-rendered HTML page with ~30 lines of vanilla JS for polling.

**Tech Stack:** Python 3.10+, Flask 3, psutil, psycopg (already in project), stdlib only otherwise. No frontend framework.

**Spec:** [`../specs/2026-07-23-dashboard-design.md`](../specs/2026-07-23-dashboard-design.md)

---

## Global Constraints

These come from the spec and apply to every task. Read this section BEFORE starting any task.

- **Zero write operations**: No `INSERT`, `UPDATE`, `DELETE`, `os.system`, `subprocess.*shell=True`, `open(..., "w"|"a"|"r+")`. Verification: `find src/setup/dashboard -name '*.py' -exec grep -l 'INSERT\|UPDATE\|DELETE\|exec\|os\.system\|subprocess.*shell=True' {} \;` must produce no output.
- **Zero control surfaces**: Only `GET /`, `GET /api/state`, `GET /api/health`. Any `POST`/`PUT`/`DELETE` must return 405.
- **Zero lock / interference with writer**: logparser reads via `open(path, "rb")`, never calls external `tail`/`cat`/`head`, only seek-forwards, limits to 64KB/tick, no subprocess shell.
- **Zero persistence**: Dashboard process never writes files (besides Python `__pycache__`).
- **UI is minimal**: Single HTML page, ≤50 lines CSS, no JS framework, no charts, no dark mode, no responsive. Backend data still collected and exposed in JSON.
- **In-memory state only**: Single `StateCache` instance. No disk cache, no SQLite.
- **Default port 8088** on `127.0.0.1`. `--host 0.0.0.0` to expose.
- **Service→port mapping** is hardcoded (from `start_services.sh`):
  ```python
  SERVICE_PORTS = {
      "tipred": 8007, "algpred2": 8008, "SoDoPE_paper_2020": 8012,
      "AnOxPePred": 8001, "HemoPI2": 8004, "pLM4CPPs": 8006,
      "MHCflurry": 8005, "TemStaPro": 8010, "ToxinPred3": 8003,
  }
  ```
- **Database DSN**: read from `IGEM_PG_DSN` env var, fallback `host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026` (same as `enrich/lib/db.py`). Do NOT import from enrich.lib — make dashboard own its connection.
- **No new top-level config files**: only `requirements.txt` at repo root, `src/setup/dashboard/{*.py,templates/,static/,README.md}`, `tests/dashboard/`.
- **Git workflow**: commit after each task using the conventional `feat:`/`test:`/`docs:`/`chore:` prefixes. Branch is `main` (no remote, no PR flow).

---

## Task 1: Scaffold package + requirements

**Files:**
- Create: `requirements.txt`
- Create: `src/setup/dashboard/__init__.py`
- Create: `src/setup/dashboard/README.md`
- Create: `tests/__init__.py`
- Create: `tests/dashboard/__init__.py`
- Create: `tests/dashboard/conftest.py`

**Purpose:** Lays the directory skeleton. README documents the command. conftest.py pins pytest to find dashboard tests.

- [ ] **Step 1: Create `requirements.txt`**

Write to `requirements.txt`:
```
Flask>=3.0
psutil>=5.9
```

- [ ] **Step 2: Create `src/setup/dashboard/__init__.py`**

Empty file (just a marker so `python3 -m src.setup.dashboard` works).

- [ ] **Step 3: Create `tests/__init__.py` and `tests/dashboard/__init__.py`**

Empty files.

- [ ] **Step 4: Create `tests/dashboard/conftest.py`**

```python
"""Adds the project root to sys.path so `import src.setup.dashboard...` works."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

- [ ] **Step 5: Create `src/setup/dashboard/README.md`**

```markdown
# Dashboard

LAN-accessible, **read-only** progress monitor for the running enrichment pipeline.

## What it does

- Watches `logs/enrich_run/master_*.log` for the current tool / batch progress.
- Reads `v_peptide_enrichment_coverage` from PG for per-tool completion.
- Lists the enrich / run.sh / 9-microservice processes with CPU/RSS/GPU-mem.
- Shows CPU / memory / GPU load + top CPU processes.
- Streams the last 30 lines of master log.

## What it does NOT do

- No write operations (no INSERT/UPDATE/DELETE, no `tee`, no POST).
- No kill / pause / restart / skip controls.
- No auth, no history, no alerts.

## Run

```bash
pip install -r requirements.txt  # or --break-system-packages on Debian

# Local only
python3 -m src.setup.dashboard

# LAN (default port 8088)
python3 -m src.setup.dashboard --host 0.0.0.0

# Also poll the 9 microservice /health endpoints (default OFF)
python3 -m src.setup.dashboard --host 0.0.0.0 --probe
```

Open `http://<server-ip>:8088/`.

## Firewall (do this)

```bash
# Only allow LAN subnet
sudo ufw allow from 192.168.1.0/24 to any port 8088
```

**Do not expose to the public internet.** No auth, no rate limit.
```

- [ ] **Step 6: Verify scaffold**

Run: `python3 -c "import src.setup.dashboard; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add requirements.txt src/setup/dashboard/__init__.py \
        src/setup/dashboard/README.md tests/__init__.py \
        tests/dashboard/__init__.py tests/dashboard/conftest.py
git commit -m "chore(dashboard): scaffold package + requirements"
```

---

## Task 2: logparser — pure functions + tests

**Files:**
- Create: `src/setup/dashboard/logparser.py`
- Create: `tests/dashboard/test_logparser.py`

**Purpose:** Parse the structured `batch done` / `START` / `start` lines from a master log. Tested in isolation against a fixture string.

**Interfaces:**
- Consumes: raw bytes / strings from a master log file.
- Produces:
  - `parse_batch_done(line: str) -> dict | None`
  - `parse_start(line: str) -> dict | None`
  - `parse_start2(line: str) -> dict | None` (the `start tool=... eligible_remaining=...` variant)

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_logparser.py`:

```python
from src.setup.dashboard.logparser import parse_batch_done, parse_start, parse_start2


def test_parse_batch_done_happy():
    line = "13:08:30 INFO batch done | last_id=12923319 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=12635700 (62.4%) | ETA=0m00s"
    r = parse_batch_done(line)
    assert r == {
        "last_id": 12923319,
        "size": 1000,
        "inserted": 1000,
        "errs": 0,
        "elapsed_s": 0.05,
        "rate": 19903.4,
        "done": 12635700,
        "done_pct": 62.4,
        "eta": "0m00s",
    }


def test_parse_batch_done_returns_none_for_garbage():
    assert parse_batch_done("totally unrelated noise") is None
    assert parse_batch_done("HTTP Request: POST ...") is None


def test_parse_batch_done_handles_long_eta():
    line = "batch done | last_id=100 size=1000 inserted=1000 errs=0 | elapsed=0.5s | rate=2000.0 seq/s | done=1000 (0.1%) | ETA=2h05m"
    r = parse_batch_done(line)
    assert r["eta"] == "2h05m"
    assert r["rate"] == 2000.0


def test_parse_start_happy():
    line = "[2026-07-21T13:08:17] START tool=sodope batch=1000"
    r = parse_start(line)
    assert r == {"tool": "sodope", "batch": 1000, "started_at": "2026-07-21T13:08:17"}


def test_parse_start_returns_none_for_non_start():
    assert parse_start("INFO HTTP Request: GET /health") is None


def test_parse_start2_happy():
    line = "13:08:29 INFO start tool=sodope batch=1000 eligible_remaining=7614185 done_so_far=12634700 done_set_size=12634700 from_id=12922319"
    r = parse_start2(line)
    assert r == {
        "tool": "sodope",
        "batch": 1000,
        "eligible_remaining": 7614185,
        "done_so_far": 12634700,
        "done_set_size": 12634700,
        "from_id": 12922319,
    }


def test_parse_start2_returns_none_for_unrelated():
    assert parse_start2("INFO batch done | ...") is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_logparser.py -v`
Expected: `ModuleNotFoundError` or `ImportError` (logparser doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `src/setup/dashboard/logparser.py`:

```python
"""logparser.py — parse structured lines from enrich_run logs.

Pure functions + LogTailer file reader. No subprocess calls, no shell.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Optional


# "batch done | last_id=12923319 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=12635700 (62.4%) | ETA=0m00s"
BATCH_RE = re.compile(
    r"batch done \| last_id=(?P<last_id>\d+) "
    r"size=(?P<size>\d+) inserted=(?P<inserted>\d+) errs=(?P<errs>\d+) "
    r"\| elapsed=(?P<elapsed_s>[\d.]+)s \| rate=(?P<rate>[\d.]+) seq/s "
    r"\| done=(?P<done>\d+) \((?P<done_pct>[\d.]+)%\) \| ETA=(?P<eta>.+?)\s*$"
)

# "[2026-07-21T13:08:17] START tool=sodope batch=1000"
START_RE = re.compile(
    r"^\[(?P<started_at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]\s+START tool=(?P<tool>\w+) batch=(?P<batch>\d+)"
)

# "13:08:29 INFO start tool=sodope batch=1000 eligible_remaining=... done_so_far=... done_set_size=... from_id=..."
START2_RE = re.compile(
    r"start tool=(?P<tool>\w+) batch=(?P<batch>\d+) "
    r"eligible_remaining=(?P<eligible_remaining>\d+) "
    r"done_so_far=(?P<done_so_far>\d+) "
    r"done_set_size=(?P<done_set_size>\d+) "
    r"from_id=(?P<from_id>\d+)"
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def parse_batch_done(line: str) -> Optional[dict]:
    m = BATCH_RE.search(line)
    if not m:
        return None
    g = m.groupdict()
    return {
        "last_id": int(g["last_id"]),
        "size": int(g["size"]),
        "inserted": int(g["inserted"]),
        "errs": int(g["errs"]),
        "elapsed_s": float(g["elapsed_s"]),
        "rate": float(g["rate"]),
        "done": int(g["done"]),
        "done_pct": float(g["done_pct"]),
        "eta": g["eta"],
    }


def parse_start(line: str) -> Optional[dict]:
    m = START_RE.match(line)
    if not m:
        return None
    return {
        "tool": m.group("tool"),
        "batch": int(m.group("batch")),
        "started_at": m.group("started_at"),
    }


def parse_start2(line: str) -> Optional[dict]:
    m = START2_RE.search(line)
    if not m:
        return None
    return {
        "tool": m.group("tool"),
        "batch": int(m.group("batch")),
        "eligible_remaining": int(m["eligible_remaining"]),
        "done_so_far": int(m["done_so_far"]),
        "done_set_size": int(m["done_set_size"]),
        "from_id": int(m["from_id"]),
    }


class LogTailer:
    """Read master log incrementally. Never writes; never seeks backwards.

    Lock guarantees (per spec §5.1.1):
    - open(path, "rb") read-only, no flock, no truncate
    - only seek-forwards from a saved offset
    - bounded read: max 64KB per tick
    - no subprocess
    - file-disappearance → empty state, no crash
    """

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self._path: Optional[Path] = None
        self._fh = None
        self._master_log_path: Optional[Path] = None
        # state
        self._tool: Optional[str] = None
        self._tool_started_at: Optional[str] = None
        self._recent_batches: list[dict] = []
        self._log_tail: list[str] = []

    def _find_latest(self) -> Optional[Path]:
        try:
            paths = sorted(
                glob.glob(os.path.join(self.log_dir, "master_*.log")),
                key=os.path.getmtime,
                reverse=True,
            )
        except FileNotFoundError:
            return None
        return Path(paths[0]) if paths else None

    def _ensure_open(self) -> bool:
        latest = self._find_latest()
        if latest is None:
            self._path = None
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            return False
        if latest != self._path:
            if self._fh is not None:
                self._fh.close()
            self._path = latest
            self._master_log_path = latest
            self._fh = open(latest, "rb")  # read-only, no lock
        return True

    @property
    def master_log_path(self) -> Optional[Path]:
        return self._master_log_path

    def read_recent(self) -> dict:
        if not self._ensure_open():
            return {}
        try:
            chunk = self._fh.read(64 * 1024)
        except Exception:
            return {}
        if chunk:
            for raw_line in chunk.decode("utf-8", errors="replace").splitlines():
                self._ingest(raw_line)

        if self._tool is None:
            return {}
        last = self._recent_batches[-1] if self._recent_batches else None
        return {
            "master_log": str(self._master_log_path),
            "tool": self._tool,
            "tool_started_at": self._tool_started_at,
            "last_batch": last,
            "recent_batches": list(self._recent_batches[-30:]),
            "log_tail": list(self._log_tail[-30:]),
        }

    def _ingest(self, line: str) -> None:
        line = _strip_ansi(line)
        self._log_tail.append(line)
        if len(self._log_tail) > 30:
            self._log_tail = self._log_tail[-30:]

        s = parse_start(line)
        if s:
            self._tool = s["tool"]
            self._tool_started_at = s["started_at"]
            self._recent_batches = []
            return

        b = parse_batch_done(line)
        if b:
            self._recent_batches.append(b)
            if len(self._recent_batches) > 30:
                self._recent_batches = self._recent_batches[-30:]
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_logparser.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/logparser.py tests/dashboard/test_logparser.py
git commit -m "feat(dashboard): logparser + LogTailer with read-only guarantees"
```

---

## Task 3: db_reader — read-only PG queries

**Files:**
- Create: `src/setup/dashboard/db_reader.py`
- Create: `tests/dashboard/test_db_reader.py`

**Purpose:** Read PG for tool coverage + db stats. Own its own connection (no import from enrich.lib). Mock-friendly.

**Interfaces:**
- `DbReader(dsn: str | None = None)` — uses `IGEM_PG_DSN` env or fallback.
- `read_coverage() -> list[dict]` — one entry per tool.
- `read_db_stats() -> dict | None` — or None on failure.

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_db_reader.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from src.setup.dashboard.db_reader import DbReader


def _fake_cursor(fetchall_rows=None, fetchone_values=None):
    cur = MagicMock()
    cur.__enter__ = lambda *a: cur
    cur.__exit__ = lambda *a: None
    if fetchall_rows is not None:
        cur.fetchall.return_value = fetchall_rows
    if fetchone_values is not None:
        cur.fetchone.side_effect = fetchone_values
    return cur


def test_read_coverage_happy():
    rows = [
        ("sodope", 7614185, 7614185),
        ("tipred", 8200000, 8200000),
        ("algpred2", 12671700, 20248885),
    ]
    cur = _fake_cursor(fetchall_rows=rows)
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("psycopg.connect", return_value=conn):
        db = DbReader("dsn_test")
        out = db.read_coverage()

    assert out[0] == {"tool": "sodope", "done": 7614185, "eligible": 7614185, "pct": 100.0, "status": "done"}
    assert out[1]["status"] == "done"
    assert out[2]["pct"] == pytest.approx(62.58, abs=0.01)
    assert out[2]["status"] == "pending"

    assert cur.execute.call_count == 1
    sql = cur.execute.call_args[0][0]
    assert "SELECT" in sql.upper()
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()


def test_read_db_stats_happy():
    cur = _fake_cursor(fetchone_values=[(12,), (1,), ("8.2 GB",)])
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("psycopg.connect", return_value=conn):
        db = DbReader("dsn_test")
        out = db.read_db_stats()

    assert out == {"active_connections": 12, "active_queries": 1, "db_size": "8.2 GB"}


def test_read_db_stats_returns_none_on_failure():
    import psycopg
    with patch("psycopg.connect", side_effect=psycopg.OperationalError("nope")):
        db = DbReader("dsn_test")
        assert db.read_db_stats() is None


def test_read_coverage_returns_empty_on_failure():
    import psycopg
    with patch("psycopg.connect", side_effect=psycopg.OperationalError("nope")):
        db = DbReader("dsn_test")
        assert db.read_coverage() == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_db_reader.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement DbReader**

Create `src/setup/dashboard/db_reader.py`:

```python
"""db_reader.py — read-only PG queries for the dashboard.

Owns its own psycopg connection. Never imports from enrich.lib to avoid
circular dependencies. DSN comes from IGEM_PG_DSN env, same fallback as enrich.
"""
from __future__ import annotations

import os
from typing import Optional

import psycopg


DEFAULT_DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)


_COVERAGE_SQL = """
SELECT tool, done_count, eligible_count
  FROM v_peptide_enrichment_coverage
 ORDER BY tool
""".strip()


_STATS_SQL = [
    "SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL",
    "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'",
    "SELECT pg_size_pretty(pg_database_size('igem_peptides'))",
]


class DbReader:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or DEFAULT_DSN

    def _connect(self):
        return psycopg.connect(self.dsn)

    def read_coverage(self) -> list[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(_COVERAGE_SQL)
                    rows = cur.fetchall()
        except Exception:
            return []

        out = []
        for tool, done, eligible in rows:
            pct = (done / eligible * 100.0) if eligible else 0.0
            if pct >= 100.0:
                status = "done"
            else:
                status = "pending"
            out.append({
                "tool": tool,
                "done": int(done),
                "eligible": int(eligible),
                "pct": round(pct, 2),
                "status": status,
            })
        return out

    def read_db_stats(self) -> Optional[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(_STATS_SQL[0])
                    total = cur.fetchone()[0]
                    cur.execute(_STATS_SQL[1])
                    active = cur.fetchone()[0]
                    cur.execute(_STATS_SQL[2])
                    size = cur.fetchone()[0]
            return {
                "active_connections": int(total),
                "active_queries": int(active),
                "db_size": str(size),
            }
        except Exception:
            return None
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_db_reader.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/db_reader.py tests/dashboard/test_db_reader.py
git commit -m "feat(dashboard): read-only db_reader"
```

---

## Task 4: proc_inspector — psutil-based process listing

**Files:**
- Create: `src/setup/dashboard/proc_inspector.py`
- Create: `tests/dashboard/test_proc_inspector.py`

**Purpose:** Identify enrich.py / run.sh / 9 microservices by matching cmdline, optionally probe `/health` for the 9 services.

**Interfaces:**
- `SERVICE_PORTS: dict[str, int]` (module-level constant)
- `ProcInspector(probe: bool = False)` — only probes when True.
- `snapshot() -> dict` — returns `{"enrich_py": [...], "master_sh": [...], "services": {...}}`.

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_proc_inspector.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from src.setup.dashboard.proc_inspector import SERVICE_PORTS, ProcInspector


def _fake_proc(pid: int, name: str, cmdline: list[str]):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "cmdline": cmdline}
    return p


def test_finds_enrich_py_and_run_sh():
    procs = [
        _fake_proc(100, "python3", ["/usr/bin/python3", "src/setup/enrich/enrich.py", "--tool", "algpred2", "--batch", "1000"]),
        _fake_proc(200, "bash", ["/bin/bash", "src/setup/enrich/run.sh"]),
        _fake_proc(300, "htop", ["/usr/bin/htop"]),
    ]
    with patch("psutil.process_iter", return_value=iter(procs)):
        inspector = ProcInspector(probe=False)
        snap = inspector.snapshot()

    assert len(snap["enrich_py"]) == 1
    assert snap["enrich_py"][0]["pid"] == 100
    assert "algpred2" in snap["enrich_py"][0]["cmd"]
    assert len(snap["master_sh"]) == 1
    assert snap["master_sh"][0]["pid"] == 200


def test_no_probe_services_have_null_alive():
    with patch("psutil.process_iter", return_value=iter([])):
        inspector = ProcInspector(probe=False)
        snap = inspector.snapshot()
    assert set(snap["services"].keys()) == set(SERVICE_PORTS.keys())
    assert snap["services"]["algpred2"]["alive"] is None
    assert snap["services"]["algpred2"]["rtt_ms"] is None


def test_probe_returns_alive_true_on_health_200():
    with patch("psutil.process_iter", return_value=iter([])):
        with patch("httpx.get") as mock_get:
            r = MagicMock()
            r.status_code = 200
            r.elapsed = MagicMock(total_seconds=lambda: 0.005)
            mock_get.return_value = r
            inspector = ProcInspector(probe=True)
            snap = inspector.snapshot()

    assert snap["services"]["algpred2"]["alive"] is True
    assert snap["services"]["algpred2"]["rtt_ms"] == pytest.approx(5.0, abs=0.01)
    assert mock_get.call_count == len(SERVICE_PORTS)


def test_probe_returns_alive_false_on_connect_error():
    import httpx
    with patch("psutil.process_iter", return_value=iter([])):
        with patch("httpx.get", side_effect=httpx.ConnectError("nope")):
            inspector = ProcInspector(probe=True)
            snap = inspector.snapshot()
    assert snap["services"]["algpred2"]["alive"] is False
    assert snap["services"]["algpred2"]["rtt_ms"] is None


def test_service_ports_includes_all_nine():
    assert len(SERVICE_PORTS) == 9
    assert {8003, 8007, 8008, 8012, 8001, 8004, 8006, 8005, 8010}.issubset(set(SERVICE_PORTS.values()))
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_proc_inspector.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement ProcInspector**

Create `src/setup/dashboard/proc_inspector.py`:

```python
"""proc_inspector.py — list enrich / run.sh / 9 microservices by cmdline.

Read-only: psutil.process_iter never sends signals. httpx probe is GET only.
"""
from __future__ import annotations

from typing import Optional

import httpx
import psutil


SERVICE_PORTS: dict[str, int] = {
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


def _looks_like_enrich_py(cmdline: list[str]) -> bool:
    return any("enrich.py" in c for c in cmdline)


def _looks_like_run_sh(cmdline: list[str]) -> bool:
    return any("run.sh" in c and "enrich" in c for c in cmdline)


def _format_cmd(cmdline: list[str]) -> str:
    if not cmdline:
        return "<unknown>"
    return " ".join(cmdline)


class ProcInspector:
    def __init__(self, probe: bool = False):
        self.probe = probe

    def _iter_procs(self):
        try:
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                yield p.info
        except Exception:
            return

    def snapshot(self) -> dict:
        enrich_py: list[dict] = []
        master_sh: list[dict] = []

        for info in self._iter_procs():
            cmdline = info.get("cmdline") or []
            if _looks_like_enrich_py(cmdline):
                enrich_py.append({
                    "pid": info["pid"],
                    "cmd": _format_cmd(cmdline),
                })
            elif _looks_like_run_sh(cmdline):
                master_sh.append({
                    "pid": info["pid"],
                    "cmd": _format_cmd(cmdline),
                })

        services: dict[str, dict] = {}
        for tool, port in SERVICE_PORTS.items():
            entry: dict = {"pid": None, "port": port, "alive": None, "rtt_ms": None}
            if self.probe:
                try:
                    r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
                    if r.status_code == 200:
                        entry["alive"] = True
                        entry["rtt_ms"] = round(r.elapsed.total_seconds() * 1000.0, 1)
                    else:
                        entry["alive"] = False
                except Exception:
                    entry["alive"] = False
            services[tool] = entry

        return {
            "enrich_py": enrich_py,
            "master_sh": master_sh,
            "services": services,
        }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_proc_inspector.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/proc_inspector.py tests/dashboard/test_proc_inspector.py
git commit -m "feat(dashboard): proc_inspector"
```

---

## Task 5: sysmon — CPU / memory / GPU + top processes

**Files:**
- Create: `src/setup/dashboard/sysmon.py`
- Create: `tests/dashboard/test_sysmon.py`

**Purpose:** Read-only system stats via psutil + nvidia-smi. No C extension deps (no pynvml). Top 10 processes by CPU.

**Interfaces:**
- `SysMon()`
- `snapshot() -> dict` — returns `{"cpu": {...}, "memory": {...}, "gpu": [...], "top_procs": [...]}`. Empty arrays on failure.

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_sysmon.py`:

```python
from unittest.mock import MagicMock, patch

from src.setup.dashboard.sysmon import SysMon


def _fake_vmem(total, used, available, percent):
    v = MagicMock()
    v.total = total
    v.used = used
    v.available = available
    v.percent = percent
    return v


def _fake_proc(pid, name, cmdline, cpu, rss):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "cmdline": cmdline}
    p.cpu_percent.return_value = cpu
    p.memory_info.return_value.rss = rss
    return p


def test_cpu_memory_happy():
    with patch("psutil.cpu_percent", return_value=[40.0, 50.0, 60.0, 70.0]), \
         patch("psutil.virtual_memory", return_value=_fake_vmem(64 * 1024**3, 32 * 1024**3, 32 * 1024**3, 50.0)), \
         patch("os.getloadavg", return_value=(1.2, 1.5, 1.8)), \
         patch("psutil.cpu_count", return_value=4), \
         patch("psutil.process_iter", return_value=iter([])):
        sysmon = SysMon()
        snap = sysmon.snapshot()

    assert snap["cpu"]["pct"] == 50.0
    assert snap["cpu"]["n_cores"] == 4
    assert snap["cpu"]["per_core"] == [40.0, 50.0, 60.0, 70.0]
    assert snap["cpu"]["load_avg"] == [1.2, 1.5, 1.8]
    assert snap["memory"]["total_gb"] == 64.0
    assert snap["memory"]["used_gb"] == 32.0
    assert snap["memory"]["pct"] == 50.0


def test_top_procs_sorted_by_cpu():
    procs = [
        _fake_proc(1, "low", ["/usr/bin/low"], 5.0, 100 * 1024**2),
        _fake_proc(2, "high", ["/usr/bin/high"], 90.0, 200 * 1024**2),
        _fake_proc(3, "mid", ["/usr/bin/mid"], 50.0, 150 * 1024**2),
    ]
    with patch("psutil.cpu_percent", return_value=[10.0]), \
         patch("psutil.virtual_memory", return_value=_fake_vmem(1, 1, 1, 0.0)), \
         patch("os.getloadavg", return_value=(0, 0, 0)), \
         patch("psutil.cpu_count", return_value=1), \
         patch("psutil.process_iter", return_value=iter(procs)):
        sysmon = SysMon()
        snap = sysmon.snapshot()

    assert len(snap["top_procs"]) == 3
    assert snap["top_procs"][0]["pid"] == 2
    assert snap["top_procs"][1]["pid"] == 3
    assert snap["top_procs"][2]["pid"] == 1


def test_gpu_empty_on_nvidia_smi_failure():
    with patch("psutil.cpu_percent", return_value=[10.0]), \
         patch("psutil.virtual_memory", return_value=_fake_vmem(1, 1, 1, 0.0)), \
         patch("os.getloadavg", return_value=(0, 0, 0)), \
         patch("psutil.cpu_count", return_value=1), \
         patch("psutil.process_iter", return_value=iter([])), \
         patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
        sysmon = SysMon()
        snap = sysmon.snapshot()
    assert snap["gpu"] == []


def test_gpu_parses_csv():
    csv = "0, NVIDIA RTX 4090, 78, 18432, 24576, 72, 285.0\n"
    fake = MagicMock()
    fake.return_value = MagicMock(returncode=0, stdout=csv, stderr="")
    with patch("psutil.cpu_percent", return_value=[10.0]), \
         patch("psutil.virtual_memory", return_value=_fake_vmem(1, 1, 1, 0.0)), \
         patch("os.getloadavg", return_value=(0, 0, 0)), \
         patch("psutil.cpu_count", return_value=1), \
         patch("psutil.process_iter", return_value=iter([])), \
         patch("subprocess.run", fake):
        sysmon = SysMon()
        snap = sysmon.snapshot()
    assert len(snap["gpu"]) == 1
    assert snap["gpu"][0]["name"] == "NVIDIA RTX 4090"
    assert snap["gpu"][0]["util_pct"] == 78.0
    assert snap["gpu"][0]["mem_total_gb"] == 24.0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_sysmon.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement SysMon**

Create `src/setup/dashboard/sysmon.py`:

```python
"""sysmon.py — read-only CPU / memory / GPU / top processes.

Uses psutil for CPU/mem/procs, nvidia-smi for GPU (no pynvml C dep).
All subprocess calls use list args (no shell=True), capture_output only,
timeouts enforced to avoid blocking the dashboard tick.
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
from typing import Optional

import psutil


_NVIDIA_SMI_GPU = [
    "nvidia-smi",
    "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
    "--format=csv,noheader,nounits",
]

_NVIDIA_SMI_PROCS = [
    "nvidia-smi",
    "--query-compute-apps=pid,used_memory",
    "--format=csv,noheader,nounits",
]


def _run_nvidia_smi(argv: list[str], timeout: float = 2.0) -> Optional[str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _parse_gpu_csv(stdout: str) -> list[dict]:
    out = []
    for row in csv.reader(io.StringIO(stdout)):
        if len(row) < 7:
            continue
        try:
            out.append({
                "index": int(row[0].strip()),
                "name": row[1].strip(),
                "util_pct": float(row[2].strip()),
                "mem_used_mb": float(row[3].strip()),
                "mem_total_mb": float(row[4].strip()),
                "temp_c": int(row[5].strip()),
                "power_w": float(row[6].strip()),
            })
        except (ValueError, IndexError):
            continue
    return out


def _parse_gpu_proc_csv(stdout: str) -> dict[int, int]:
    out = {}
    for row in csv.reader(io.StringIO(stdout)):
        if len(row) < 2:
            continue
        try:
            out[int(row[0].strip())] = int(float(row[1].strip()))
        except (ValueError, IndexError):
            continue
    return out


def _bytes_to_gb(n: int) -> float:
    return round(n / (1024 ** 3), 2)


class SysMon:
    def snapshot(self) -> dict:
        cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)
        cpu_pct = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0
        try:
            load_avg = list(os.getloadavg())
        except (OSError, AttributeError):
            load_avg = [0.0, 0.0, 0.0]
        n_cores = psutil.cpu_count() or 1

        mem = psutil.virtual_memory()
        memory = {
            "total_gb": _bytes_to_gb(mem.total),
            "used_gb": _bytes_to_gb(mem.used),
            "available_gb": _bytes_to_gb(mem.available),
            "pct": float(mem.percent),
        }

        gpu_stdout = _run_nvidia_smi(_NVIDIA_SMI_GPU)
        gpu = _parse_gpu_csv(gpu_stdout) if gpu_stdout else []
        for g in gpu:
            g["mem_used_gb"] = round(g["mem_used_mb"] / 1024.0, 2)
            g["mem_total_gb"] = round(g["mem_total_mb"] / 1024.0, 2)
            g["mem_pct"] = round(g["mem_used_mb"] / g["mem_total_mb"] * 100.0, 1) if g["mem_total_mb"] else 0.0
            for k in ("mem_used_mb", "mem_total_mb"):
                g.pop(k, None)

        gpu_proc_stdout = _run_nvidia_smi(_NVIDIA_SMI_PROCS)
        gpu_pid_to_mem = _parse_gpu_proc_csv(gpu_proc_stdout) if gpu_proc_stdout else {}

        top_procs: list[dict] = []
        try:
            for p in psutil.process_iter(["pid", "name", "cmdline", "username"]):
                info = p.info
                try:
                    cpu = p.cpu_percent()
                    rss = p.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                cmdline = info.get("cmdline") or []
                top_procs.append({
                    "pid": info["pid"],
                    "user": info.get("username") or "",
                    "cmd": " ".join(cmdline)[:200] if cmdline else f"<{info.get('name', '?')}>",
                    "cpu_pct": float(cpu),
                    "mem_rss_mb": round(rss / (1024 ** 2), 1),
                    "gpu_mem_mb": gpu_pid_to_mem.get(info["pid"], 0),
                })
        except Exception:
            pass

        top_procs.sort(key=lambda x: (x["cpu_pct"], x["mem_rss_mb"]), reverse=True)
        top_procs = top_procs[:10]

        return {
            "cpu": {
                "pct": round(cpu_pct, 1),
                "per_core": [round(float(x), 1) for x in cpu_per_core],
                "load_avg": [round(float(x), 2) for x in load_avg],
                "n_cores": n_cores,
            },
            "memory": memory,
            "gpu": gpu,
            "top_procs": top_procs,
        }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_sysmon.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/sysmon.py tests/dashboard/test_sysmon.py
git commit -m "feat(dashboard): sysmon (cpu/mem/gpu/top procs)"
```

---

## Task 6: state.py — aggregator with 2s background tick

**Files:**
- Create: `src/setup/dashboard/state.py`
- Create: `tests/dashboard/test_state.py`

**Purpose:** Owns the four collectors, ticks every 2s, exposes a thread-safe snapshot.

**Interfaces:**
- `StateCache(log_dir: str, dashboard_root: str, probe: bool = False)`
- `start()` — spawns daemon thread.
- `snapshot() -> dict` — returns latest state, thread-safe.
- `last_tick_time() -> float | None` — wall-clock seconds since epoch of last successful tick.

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_state.py`:

```python
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.setup.dashboard.state import StateCache


def test_snapshot_empty_when_nothing_running(tmp_path: Path):
    cache = StateCache(log_dir=str(tmp_path), dashboard_root="/tmp", probe=False)
    cache.start()
    try:
        # let it tick at least once
        time.sleep(2.5)
        s = cache.snapshot()
        assert "generated_at" in s
        assert s["dashboard_version"] == "0.1.0"
        assert s["current_run"] == {}
        assert s["tools_overview"] == []
        assert s["db"] is None or isinstance(s["db"], dict)
        assert "system" in s
    finally:
        cache.stop()


def test_snapshot_includes_log_state(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "master_20260721_130817.log"
    log.write_text(
        "[2026-07-21T13:08:17] START tool=algpred2 batch=1000\n"
        "batch done | last_id=1000 size=1000 inserted=1000 errs=0 | elapsed=0.5s | rate=2000.0 seq/s | done=1000 (0.0%) | ETA=2h05m\n"
    )
    cache = StateCache(log_dir=str(log_dir), dashboard_root="/tmp", probe=False)
    cache.start()
    try:
        time.sleep(2.5)
        s = cache.snapshot()
        assert s["current_run"]["tool"] == "algpred2"
    finally:
        cache.stop()


def test_subsystem_failure_does_not_break_tick(tmp_path: Path):
    cache = StateCache(log_dir=str(tmp_path), dashboard_root="/tmp", probe=False)
    cache.start()
    try:
        with patch.object(cache._proc, "snapshot", side_effect=RuntimeError("boom")):
            time.sleep(2.5)
            # even with proc_inpector blowing up, snapshot should still be present
            s = cache.snapshot()
            assert "generated_at" in s
    finally:
        cache.stop()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_state.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement StateCache**

Create `src/setup/dashboard/state.py`:

```python
"""state.py — aggregate collectors into a single in-memory snapshot.

One background thread ticks every 2s. Subsystem failures are isolated:
one blowing up does not prevent the others from refreshing.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from typing import Optional

from .db_reader import DbReader
from .logparser import LogTailer
from .proc_inspector import ProcInspector
from .sysmon import SysMon


DASHBOARD_VERSION = "0.1.0"
TICK_INTERVAL = 2.0


class StateCache:
    def __init__(self, log_dir: str, dashboard_root: str, probe: bool = False):
        self.log_dir = log_dir
        self.dashboard_root = dashboard_root
        self._probe = probe
        self._tailer = LogTailer(log_dir)
        self._db = DbReader()
        self._proc = ProcInspector(probe=probe)
        self._sysmon = SysMon()
        self._state: dict = {}
        self._lock = threading.Lock()
        self._last_tick: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._tick_loop, daemon=True, name="dashboard-tick")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def last_tick_time(self) -> Optional[float]:
        return self._last_tick

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception as e:
                sys.stderr.write(f"[state] tick error: {e!r}\n")
            self._stop.wait(TICK_INTERVAL)

    def _refresh(self) -> None:
        # Each collector is isolated. Failure → keep last value (or empty).
        current_run = self._safe(self._tailer.read_recent, {})
        tools_overview = self._safe(self._db.read_coverage, [])
        db_stats = self._safe(self._db.read_db_stats, None)
        processes = self._safe(self._proc.snapshot, {"enrich_py": [], "master_sh": [], "services": {}})
        system = self._safe(self._sysmon.snapshot, {"cpu": {}, "memory": {}, "gpu": [], "top_procs": []})

        new_state = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dashboard_version": DASHBOARD_VERSION,
            "current_run": current_run,
            "tools_overview": tools_overview,
            "db": db_stats,
            "processes": processes,
            "system": system,
        }
        with self._lock:
            self._state = new_state
            self._last_tick = time.time()

    def _safe(self, fn, default):
        try:
            return fn()
        except Exception:
            return default
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_state.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/state.py tests/dashboard/test_state.py
git commit -m "feat(dashboard): StateCache with 2s background tick"
```

---

## Task 7: app.py — Flask routes + argparse entry

**Files:**
- Create: `src/setup/dashboard/app.py`
- Create: `tests/dashboard/test_app.py`

**Purpose:** Single Flask app with only GET routes. Argparse for `--host`, `--port`, `--log-dir`, `--probe`.

**Interfaces:**
- Routes: `GET /`, `GET /api/state`, `GET /api/health`.
- `main()` — argparse entry point.

- [ ] **Step 1: Write the failing test**

Create `tests/dashboard/test_app.py`:

```python
import json

import pytest

from src.setup.dashboard.app import create_app


@pytest.fixture
def client(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    app = create_app(log_dir=str(log_dir), probe=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"<!DOCTYPE html>" in r.data or b"<html" in r.data


def test_api_state_returns_json(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "generated_at" in data
    assert "current_run" in data
    assert "tools_overview" in data
    assert "system" in data


def test_api_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["ok"] is True


def test_post_to_api_state_returns_405(client):
    r = client.post("/api/state")
    assert r.status_code == 405


def test_post_to_api_health_returns_405(client):
    r = client.post("/api/health")
    assert r.status_code == 405


def test_unknown_route_returns_404(client):
    r = client.get("/api/control")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=. pytest tests/dashboard/test_app.py -v`
Expected: `ImportError: cannot import name 'create_app' from 'src.setup.dashboard.app'`.

- [ ] **Step 3: Implement app.py**

Create `src/setup/dashboard/app.py`:

```python
"""app.py — Flask app + argparse entry point.

Routes (all GET, no exceptions):
  GET /              → HTML
  GET /api/state     → JSON
  GET /api/health    → JSON
"""
from __future__ import annotations

import argparse
import sys

from flask import Flask, jsonify, render_template

from .state import StateCache


def create_app(log_dir: str, probe: bool = False) -> tuple[Flask, StateCache]:
    """Factory for tests and main(). Sets up the StateCache and wires routes."""
    cache = StateCache(log_dir=log_dir, dashboard_root="/tmp", probe=probe)
    cache.start()

    app = Flask(__name__)
    app.config["STATE_CACHE"] = cache

    @app.route("/")
    def index():
        return render_template("index.html", initial_state=cache.snapshot())

    @app.route("/api/state")
    def api_state():
        return jsonify(cache.snapshot())

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True, "last_tick": cache.last_tick_time()})

    return app, cache


def main() -> None:
    p = argparse.ArgumentParser(description="iGEM dashboard (read-only)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8088, help="bind port (default 8088)")
    p.add_argument("--log-dir", default="/home/lenovo/Projects/iGEM-platform/logs/enrich_run")
    p.add_argument("--probe", action="store_true", help="probe microservice /health endpoints each tick")
    args = p.parse_args()

    app, _cache = create_app(log_dir=args.log_dir, probe=args.probe)
    print(f"[dashboard] http://{args.host}:{args.port}/  log_dir={args.log_dir} probe={args.probe}", file=sys.stderr)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `PYTHONPATH=. pytest tests/dashboard/test_app.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/app.py tests/dashboard/test_app.py
git commit -m "feat(dashboard): Flask app + routes"
```

---

## Task 8: minimal HTML + CSS

**Files:**
- Create: `src/setup/dashboard/templates/index.html`
- Create: `src/setup/dashboard/static/style.css`

**Purpose:** Minimal single-page UI. ≤50 lines CSS. Renders initial state from Jinja, then JS polls `/api/state` every 2s.

- [ ] **Step 1: Create `src/setup/dashboard/templates/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>iGEM dashboard</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header>
    <h1>iGEM dashboard</h1>
    <div id="meta">
      <span>updated: <span id="ts">—</span></span>
      <a href="?probe=1">probe services</a>
      <a href="{{ url_for('api_state') }}">/api/state</a>
    </div>
  </header>

  <section id="current-run">
    <h2>Current run</h2>
    <div id="current-body">—</div>
  </section>

  <section id="tools-overview">
    <h2>Tools overview</h2>
    <table id="tools-table">
      <thead><tr><th>tool</th><th>done / eligible</th><th>%</th><th>status</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>

  <section id="system">
    <h2>System</h2>
    <div id="system-body">—</div>
  </section>

  <section id="log-tail">
    <h2>Log tail</h2>
    <pre id="log-body">—</pre>
  </section>

  <script>
    const initial = {{ initial_state | tojson }};
    let backoff = 2000;
    function render(s) {
      document.getElementById('ts').textContent = s.generated_at || '—';
      const cur = s.current_run || {};
      const lb = cur.last_batch || {};
      document.getElementById('current-body').textContent =
        (cur.tool ? 'tool=' + cur.tool + '  ' : '') +
        (cur.tool_started_at ? 'started=' + cur.tool_started_at + '  ' : '') +
        (lb.last_id ? 'last_id=' + lb.last_id + '  ' : '') +
        (lb.rate ? 'rate=' + lb.rate + ' seq/s  ' : '') +
        (lb.done ? 'done=' + lb.done + '  ' : '') +
        (lb.done_pct != null ? lb.done_pct + '%  ' : '') +
        (lb.eta ? 'eta=' + lb.eta : '');
      const tb = document.querySelector('#tools-table tbody');
      tb.innerHTML = (s.tools_overview || []).map(t =>
        '<tr><td>' + t.tool + '</td><td>' + t.done + ' / ' + t.eligible + '</td><td>' + t.pct + '</td><td>' + t.status + '</td></tr>'
      ).join('');
      const sys = s.system || {};
      const cpu = sys.cpu || {}, mem = sys.memory || {}, gpu = sys.gpu || [];
      const gpuLines = gpu.map(g => g.name + ' util=' + g.util_pct + '% mem=' + g.mem_used_gb + '/' + g.mem_total_gb + 'GB');
      const top = (sys.top_procs || []).slice(0, 10).map(p =>
        p.pid + ' ' + p.user + ' ' + p.cmd + '  cpu=' + p.cpu_pct + '%  rss=' + p.mem_rss_mb + 'MB' + (p.gpu_mem_mb ? '  gpu=' + p.gpu_mem_mb + 'MB' : '')
      );
      document.getElementById('system-body').textContent =
        'cpu=' + cpu.pct + '% (' + (cpu.per_core || []).join(',') + ') load=' + (cpu.load_avg || []).join(',') + '\n' +
        'mem=' + mem.used_gb + '/' + mem.total_gb + 'GB (' + mem.pct + '%)\n' +
        'gpu:\n' + gpuLines.join('\n') + '\n' +
        'top procs:\n' + top.join('\n');
      const tail = (s.current_run || {}).log_tail || [];
      document.getElementById('log-body').textContent = tail.join('\n');
    }
    function poll() {
      fetch('/api/state').then(r => r.json()).then(s => {
        render(s);
        backoff = 2000;
      }).catch(() => {
        backoff = Math.min(backoff * 2, 30000);
      }).finally(() => setTimeout(poll, backoff));
    }
    render(initial);
    setTimeout(poll, 2000);
  </script>
</body>
</html>
```

- [ ] **Step 2: Create `src/setup/dashboard/static/style.css`**

```css
body { font-family: system-ui, sans-serif; margin: 1.5em; max-width: 1200px; }
header { border-bottom: 1px solid #ccc; padding-bottom: 0.5em; margin-bottom: 1em; }
h1 { margin: 0 0 0.5em 0; font-size: 1.4em; }
h2 { margin: 1.5em 0 0.4em 0; font-size: 1.1em; }
#meta { display: flex; gap: 1em; font-size: 0.9em; color: #555; }
#meta a { color: #06c; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #eee; }
th { background: #f5f5f5; }
td:nth-child(2), td:nth-child(3), #system-body { font-family: ui-monospace, monospace; }
pre { background: #f5f5f5; padding: 0.8em; overflow-x: auto; white-space: pre-wrap; font-size: 0.85em; }
#system-body { background: #f5f5f5; padding: 0.8em; white-space: pre-wrap; font-size: 0.85em; }
```

- [ ] **Step 3: Re-run app tests with templates**

Run: `PYTHONPATH=. pytest tests/dashboard/test_app.py -v`
Expected: 6 passed (still).

- [ ] **Step 4: Manual smoke check**

Run: `python3 -m src.setup.dashboard --port 18099 &`
Then: `curl -s http://127.0.0.1:18099/api/state | head -c 200`
Expected: JSON starting with `{"dashboard_version": "0.1.0", ...`.
Then: `curl -s http://127.0.0.1:18099/ | head -c 100`
Expected: `<!DOCTYPE html>`.
Then: kill the server (`pkill -f "src.setup.dashboard"` or `kill $!`).

- [ ] **Step 5: Commit**

```bash
git add src/setup/dashboard/templates/index.html src/setup/dashboard/static/style.css
git commit -m "feat(dashboard): minimal HTML + CSS"
```

---

## Task 9: lock-safety acceptance test + grep verification

**Files:**
- Create: `tests/dashboard/test_lock_safety.py`
- Modify: (none — just verify file system state)

**Purpose:** Prove the dashboard does not interfere with writers. Run a fake writer that appends to a file in a tight loop for some seconds, start a LogTailer next to it, count how many lines the writer emitted. With LogTailer reading, the count should match the count without it.

- [ ] **Step 1: Write the test**

Create `tests/dashboard/test_lock_safety.py`:

```python
"""Lock-safety: prove LogTailer does not stall the writer.

Run two writers in parallel — one writes to a file with no reader,
the other writes to a file with a LogTailer reading it concurrently.
Both should produce roughly the same number of lines in the same time.
"""
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path

from src.setup.dashboard.logparser import LogTailer


def _writer(path: str, duration: float, ready_evt, done_evt) -> None:
    with open(path, "a") as f:
        ready_evt.set()
        end = time.time() + duration
        n = 0
        while time.time() < end:
            f.write(f"batch done | last_id={n} size=1000 inserted=1000 errs=0 | elapsed=0.5s | rate=2000.0 seq/s | done={n} (1.0%) | ETA=0m00s\n")
            n += 1
        f.flush()
    done_evt.set()


def _reader_loop(log_dir: str, duration: float, ready_evt, done_evt) -> None:
    tailer = LogTailer(log_dir)
    ready_evt.set()
    end = time.time() + duration
    while time.time() < end:
        tailer.read_recent()
        time.sleep(0.1)
    done_evt.set()


def test_writer_unaffected_by_reader():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        # Scenario A: writer alone
        path_a = log_dir / "master_alone.log"
        path_a.touch()
        ready_a, done_a = mp.Event(), mp.Event()
        wa = mp.Process(target=_writer, args=(str(path_a), 2.0, ready_a, done_a))
        wa.start()
        done_a.wait(10)
        wa.join()
        lines_a = sum(1 for _ in open(path_a))

        # Scenario B: writer + reader
        path_b = log_dir / "master_with_reader.log"
        path_b.touch()
        ready_b, done_b = mp.Event(), mp.Event()
        ready_r, done_r = mp.Event(), mp.Event()
        wb = mp.Process(target=_writer, args=(str(path_b), 2.0, ready_b, done_b))
        rb = mp.Process(target=_reader_loop, args=(str(log_dir), 2.0, ready_r, done_r))
        wb.start(); rb.start()
        done_b.wait(10); done_r.wait(10)
        wb.join(); rb.join()
        lines_b = sum(1 for _ in open(path_b))

        # Lines should be within 50% of each other (writer is CPU/IO bound, not reader-bound).
        # If the reader held any lock, B would be MUCH less than A.
        assert lines_b >= lines_a * 0.5, f"reader starved writer: alone={lines_a}, with-reader={lines_b}"
```

- [ ] **Step 2: Run test, verify it passes**

Run: `PYTHONPATH=. pytest tests/dashboard/test_lock_safety.py -v`
Expected: 1 passed.

(Tolerance is 50% to allow for scheduling noise on shared CPU. If B is dramatically smaller than A, that would indicate real lock contention.)

- [ ] **Step 3: Verify zero-write constraint via grep**

Run:
```bash
find src/setup/dashboard -name '*.py' -exec grep -lE 'INSERT|UPDATE|DELETE|exec\(|os\.system|subprocess.*shell=True' {} \;
```
Expected: (no output)

If anything matches, fix it before continuing.

- [ ] **Step 4: Run full test suite**

Run: `PYTHONPATH=. pytest tests/dashboard -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/dashboard/test_lock_safety.py
git commit -m "test(dashboard): lock-safety acceptance + zero-write grep"
```

---

## Task 10: final smoke + run all tests

**Files:**
- Modify: (none)

**Purpose:** End-to-end sanity check. Run the full test suite, start the dashboard, hit the endpoints, kill it.

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=. pytest tests/dashboard -v`
Expected: all tests pass (target: ~30 tests across 7 files).

- [ ] **Step 2: Smoke-launch the dashboard**

Run:
```bash
python3 -m src.setup.dashboard --port 18099 &
DPID=$!
sleep 3
curl -s http://127.0.0.1:18099/api/health
echo
curl -s http://127.0.0.1:18099/api/state | python3 -c "import json,sys; s=json.load(sys.stdin); print('keys:', sorted(s.keys()))"
curl -s -o /dev/null -w "GET /: %{http_code}\n" http://127.0.0.1:18099/
curl -s -o /dev/null -w "POST /api/state: %{http_code}\n" -X POST http://127.0.0.1:18099/api/state
kill $DPID
```
Expected:
- `/api/health` returns `{"ok": true, ...}`
- `/api/state` JSON has keys: `current_run`, `dashboard_version`, `db`, `generated_at`, `processes`, `system`, `tools_overview`
- `GET /`: 200
- `POST /api/state`: 405

- [ ] **Step 3: Final commit if any tweaks**

If you tweaked anything in step 2:
```bash
git add -A
git commit -m "chore(dashboard): final smoke fixups"
```

---

## Self-Review (run before declaring done)

- [ ] Spec coverage: every section in `../specs/2026-07-23-dashboard-design.md` has a corresponding task. (Sections 0-13 all mapped.)
- [ ] No placeholders: grep `.md` for `TBD|TODO|FIXME`. Fix anything found.
- [ ] Type consistency: `LogTailer.read_recent()` returns dict; `state.snapshot()` returns dict; `ProcInspector.snapshot()` returns dict with `enrich_py`, `master_sh`, `services`. All consistent.
- [ ] Lock safety: Task 9 explicitly tests writer vs writer+reader.
- [ ] Zero write: Task 9 step 3 explicitly greps.
- [ ] 405 on POST: tested in Task 7.
- [ ] UI minimal: ≤50 lines CSS, no JS framework — verified in Task 8.
