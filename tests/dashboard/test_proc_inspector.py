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
