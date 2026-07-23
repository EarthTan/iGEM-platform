import time
from pathlib import Path
from unittest.mock import patch

from src.setup.dashboard.state import StateCache


def _fast_collectors(cache, *, keep_tailer=False):
    """Replace slow collectors with instant mocks."""
    if not keep_tailer:
        cache._tailer.read_recent = lambda: {}
    cache._db.read_coverage = lambda: []
    cache._db.read_db_stats = lambda: {"active_connections": 0, "active_queries": 0, "db_size": "0 MB"}
    cache._proc.snapshot = lambda: {"enrich_py": [], "master_sh": [], "services": {}}
    cache._sysmon.snapshot = lambda: {"cpu": {"pct": 0.0, "per_core": [], "load_avg": [], "n_cores": 1}, "memory": {}, "gpu": [], "top_procs": []}


def test_snapshot_empty_when_nothing_running(tmp_path: Path):
    cache = StateCache(log_dir=str(tmp_path), dashboard_root="/tmp", probe=False)
    _fast_collectors(cache)
    cache.start()
    try:
        # let it tick at least once
        for _ in range(40):
            time.sleep(0.1)
            if cache.last_tick_time() is not None:
                break
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
    _fast_collectors(cache, keep_tailer=True)
    cache.start()
    try:
        for _ in range(40):
            time.sleep(0.1)
            if cache.last_tick_time() is not None:
                break
        s = cache.snapshot()
        assert s["current_run"]["tool"] == "algpred2"
    finally:
        cache.stop()


def test_subsystem_failure_does_not_break_tick(tmp_path: Path):
    cache = StateCache(log_dir=str(tmp_path), dashboard_root="/tmp", probe=False)
    _fast_collectors(cache)
    cache.start()
    try:
        cache._proc.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        for _ in range(40):
            time.sleep(0.1)
            if cache.last_tick_time() is not None:
                break
        # even with proc_inpector blowing up, snapshot should still be present
        s = cache.snapshot()
        assert "generated_at" in s
    finally:
        cache.stop()
