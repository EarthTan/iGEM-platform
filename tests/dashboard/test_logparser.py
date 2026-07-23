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
