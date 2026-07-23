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

    assert snap["cpu"]["pct"] == 55.0
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
    csv_out = "0, NVIDIA RTX 4090, 78, 18432, 24576, 72, 285.0\n"
    fake = MagicMock()
    fake.return_value = MagicMock(returncode=0, stdout=csv_out, stderr="")
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
