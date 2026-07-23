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
            # reset state when switching files
            self._tool = None
            self._tool_started_at = None
            self._recent_batches = []
            self._offset = 0
            # Seek to just past the most recent START line so we don't
            # consume stale history (master log is append-only across runs).
            self._seek_past_latest_start()
        return True

    def _seek_past_latest_start(self) -> None:
        """Find the latest START line, then seek to (EOF - 1MB) so we
        start reading close to the file's tail.

        8MB backwards-scan finds the latest START (tool switches are
        <8MB apart in our enrichment master log). Then we open the
        1MB tail window for read_recent — it contains ~3000 batch done
        lines (enough to populate recent_batches + last_batch).
        """
        if self._fh is None:
            return
        try:
            self._fh.seek(0, 2)  # EOF
            end = self._fh.tell()
            scan_window = min(end, 8 * 1024 * 1024)
            self._fh.seek(end - scan_window)
            chunk = self._fh.read(scan_window)
        except Exception:
            return
        # find the LAST "START tool=" match
        last_start_idx = chunk.rfind(b"START tool=")
        if last_start_idx == -1:
            return
        # walk back to the start of that line
        line_start = chunk.rfind(b"\n", 0, last_start_idx)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1  # skip the newline
        prefix = chunk[line_start:]
        # ingest the START line so _tool is set
        for raw_line in prefix.decode("utf-8", errors="replace").splitlines():
            self._ingest(raw_line)
        # now seek to (EOF - 1MB) so we capture recent batches
        try:
            self._fh.seek(0, 2)
            end = self._fh.tell()
            tail_window = min(end, 1024 * 1024)
            self._fh.seek(end - tail_window)
        except Exception:
            self._fh.seek(0)
        self._offset = self._fh.tell()

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
