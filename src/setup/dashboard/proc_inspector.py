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
