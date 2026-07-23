"""Lock-safety: prove LogTailer does not stall the writer.

Run two writers in parallel — one writes to a file with no reader,
the other writes to a file with a LogTailer reading it concurrently.
Both should produce roughly the same number of lines in the same time.
"""
import multiprocessing as mp
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
        time.sleep(0.05)
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
