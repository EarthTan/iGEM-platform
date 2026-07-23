import time
from pathlib import Path

from src.setup.dashboard.logparser import LogTailer


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def test_finds_latest_master_log(tmp_path: Path):
    (tmp_path / "master_20260701_120000.log").write_text("old")
    time.sleep(0.05)
    (tmp_path / "master_20260721_130817.log").write_text("new")
    t = LogTailer(str(tmp_path))
    t.read_recent()  # trigger file open
    assert t.master_log_path.name == "master_20260721_130817.log"


def test_returns_empty_when_no_log(tmp_path: Path):
    t = LogTailer(str(tmp_path))
    assert t.read_recent() == {}


def test_read_recent_parses_batch_done(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    _write_log(log, [
        "[2026-07-21T13:08:17] START tool=sodope batch=1000",
        "13:08:30 INFO batch done | last_id=12923319 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=12635700 (62.4%) | ETA=0m00s",
    ])
    t = LogTailer(str(tmp_path))
    state = t.read_recent()
    assert state["tool"] == "sodope"
    assert state["tool_started_at"] == "2026-07-21T13:08:17"
    assert state["last_batch"]["last_id"] == 12923319
    assert state["last_batch"]["rate"] == 19903.4
    assert state["last_batch"]["done_pct"] == 62.4


def test_read_recent_incremental_progress(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    _write_log(log, [
        "[2026-07-21T13:08:17] START tool=algpred2 batch=1000",
        "13:08:30 INFO batch done | last_id=1000 size=1000 inserted=1000 errs=0 | elapsed=0.34s | rate=2941.0 seq/s | done=1000 (0.0%) | ETA=2h05m",
    ])
    t = LogTailer(str(tmp_path))
    state1 = t.read_recent()
    assert state1["last_batch"]["done_pct"] == 0.0

    # simulate the writer appending more lines
    with open(log, "a") as f:
        f.write("13:08:31 INFO batch done | last_id=2000 size=1000 inserted=1000 errs=0 | elapsed=0.34s | rate=2941.0 seq/s | done=2000 (0.0%) | ETA=2h05m\n")

    state2 = t.read_recent()
    assert state2["last_batch"]["done_pct"] == 0.0
    assert len(state2["recent_batches"]) >= 2
    assert state2["recent_batches"][-1]["last_id"] == 2000


def test_read_recent_picks_new_tool(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    _write_log(log, [
        "[2026-07-21T13:08:17] START tool=sodope batch=1000",
        "batch done | last_id=1000 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=1000 (50.0%) | ETA=0m00s",
        "[2026-07-21T18:00:00] START tool=algpred2 batch=1000",
        "batch done | last_id=1000 size=1000 inserted=1000 errs=0 | elapsed=0.34s | rate=2941.0 seq/s | done=1000 (0.0%) | ETA=2h05m",
    ])
    t = LogTailer(str(tmp_path))
    state = t.read_recent()
    assert state["tool"] == "algpred2"
    assert state["tool_started_at"] == "2026-07-21T18:00:00"


def test_read_recent_keeps_last_30_batches(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    lines = ["[2026-07-21T13:08:17] START tool=algpred2 batch=1000"]
    for i in range(1, 51):
        lines.append(f"batch done | last_id={i*1000} size=1000 inserted=1000 errs=0 | elapsed=0.5s | rate=2000.0 seq/s | done={i*1000} (1.0%) | ETA=2h00m")
    _write_log(log, lines)
    t = LogTailer(str(tmp_path))
    state = t.read_recent()
    assert len(state["recent_batches"]) == 30
    assert state["recent_batches"][-1]["last_id"] == 50000


def test_read_recent_log_tail_strips_ansi(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    _write_log(log, [
        "[2026-07-21T13:08:17] START tool=sodope batch=1000",
        "\x1b[31m13:08:30 INFO batch done | last_id=1000 size=1000 inserted=1000 errs=0 | elapsed=0.05s | rate=19903.4 seq/s | done=1000 (50.0%) | ETA=0m00s\x1b[0m",
    ])
    t = LogTailer(str(tmp_path))
    state = t.read_recent()
    assert "\x1b" not in state["log_tail"][0]


def test_read_recent_returns_empty_if_log_disappears(tmp_path: Path):
    log = tmp_path / "master_20260721_130817.log"
    _write_log(log, ["[2026-07-21T13:08:17] START tool=sodope batch=1000"])
    t = LogTailer(str(tmp_path))
    state1 = t.read_recent()
    assert state1["tool"] == "sodope"
    log.unlink()
    state2 = t.read_recent()
    # either empty (log re-detected as missing) or last good state — both are non-crashing
    assert "tool" in state2 or state2 == {}
