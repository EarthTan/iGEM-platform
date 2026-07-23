"""sysmon.py — read-only CPU / memory / GPU / top processes.

Uses psutil for CPU/mem/procs, nvidia-smi for GPU (no pynvml C dep).
All subprocess calls use list args (no shell), capture_output only,
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
