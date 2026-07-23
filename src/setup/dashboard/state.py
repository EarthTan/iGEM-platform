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
