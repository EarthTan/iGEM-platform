"""clients.py — 10 个 FASTA 微服务的统一 httpx 客户端。

每个 client 接受 [{peptide_id, sequence}, ...],
返回 [(peptide_id, score, label, details_dict), ...]。

特化点(写在 _handler_<tool> 里):
- 长度过滤(mhcflurry 硬限制 5-15aa,hemopi2 客户端截断 40)
- 响应字段映射(toxinpred3 的 details 字段、mhcflurry 的 affinity 等)

TOOL_REGISTRY 用 tool 名索引 → {client_class, default_port, ...}
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import httpx


@dataclass
class Score:
    peptide_id: int
    score: float | None
    label: str | None
    details: dict


# 端口表
PORT = {
    "toxinpred3": 8003,
    "tipred":     8007,
    "algpred2":   8008,
    "sodope":     8012,
    "anoxpepred": 8001,
    "hemopi2":    8004,
    "plm4cpps":   8006,
    "mhcflurry":  8005,
    "bepipred3":  8002,
    "temstapro":  8010,
}


def get_client(tool: str) -> "BaseClient":
    if tool not in TOOL_REGISTRY:
        raise ValueError(f"tool {tool} not in registry (have {sorted(TOOL_REGISTRY)})")
    return TOOL_REGISTRY[tool]


# --------------------------------------------------------------------- base


class BaseClient:
    tool_name: str = ""

    def __init__(self, base_url: str | None = None, timeout: float = 300.0):
        port = PORT[self.tool_name]
        self.base_url = base_url or os.environ.get(
            f"IGEM_{self.tool_name.upper()}_URL",
            f"http://127.0.0.1:{port}",
        )
        self.timeout = timeout
        # 一个长连接 client 就够;worker 单进程
        self._client = httpx.Client(timeout=self.timeout)

    def health(self) -> dict:
        r = self._client.get(f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()

    def info(self) -> dict:
        r = self._client.get(f"{self.base_url}/info")
        r.raise_for_status()
        return r.json()

    def _post_batch(self, items: list[tuple[int, str]]) -> dict:
        payload = {
            "sequences": [
                {"peptide_id": str(pid), "sequence": seq}
                for pid, seq in items
            ]
        }
        r = self._client.post(f"{self.base_url}/predict/batch", json=payload)
        r.raise_for_status()
        return r.json()

    # 给 default handler 用: 直接 list[ToolResult]
    def _parse(self, items: list[tuple[int, str]], resp: dict) -> list[Score]:
        results = []
        results_by_id = {str(r.get("peptide_id")): r for r in resp.get("results", [])}
        for pid, _ in items:
            r = results_by_id.get(str(pid))
            if r is None:
                results.append(Score(pid, None, "ERROR", {"reason": "missing in response"}))
                continue
            score = r.get("score")
            label = r.get("label")
            details = r.get("details") or {}
            results.append(Score(pid, float(score) if score is not None else None,
                                 str(label) if label is not None else None,
                                 details))
        return results

    def score(self, items: list[tuple[int, str]]) -> list[Score]:
        t0 = time.time()
        resp = self._post_batch(items)
        elapsed = time.time() - t0
        results = self._parse(items, resp)
        # 把 elapsed 写进最后一个 result 的 details(便于调度分析)
        if results:
            results[-1].details["__batch_elapsed_sec"] = round(elapsed, 3)
        return results

    def close(self):
        self._client.close()


# ------------------------------------------------------- 特化(长度/字段)


class MhcflurryClient(BaseClient):
    """MHCflurry 服务端 < 5aa 或 > 15aa 会返回 label='Invalid Length' + score=0
    我们在 DB 层已经过滤到 5..15,这里再做一遍兜底跳过。
    """
    tool_name = "mhcflurry"

    def _parse(self, items, resp):
        results = []
        results_by_id = {str(r.get("peptide_id")): r for r in resp.get("results", [])}
        for pid, seq in items:
            r = results_by_id.get(str(pid))
            if r is None:
                results.append(Score(pid, None, "ERROR", {"reason": "missing"}))
                continue
            label = str(r.get("label") or "")
            score = r.get("score")
            details = r.get("details") or {}
            if label == "Invalid Length":
                # 跳过,不入库 — DB 层已经过滤过,这里极少触发
                continue
            results.append(Score(pid, float(score) if score is not None else None,
                                 label, details))
        return results


class Hemopi2Client(BaseClient):
    """HemoPI2 在 predict_impl 里会自动截断 > 40 aa 的序列
    -> score 仍然有效,我们入库但加个 truncated 标记。
    """
    tool_name = "hemopi2"

    def _parse(self, items, resp):
        results = super()._parse(items, resp)
        for r in results:
            if r.details.get("length", 0) > 40:
                r.details["truncated"] = True
        return results


# --- registry -------------------------------------------------------------

TOOL_REGISTRY: dict[str, BaseClient] = {}


def _register(cls):
    inst = cls()
    TOOL_REGISTRY[inst.tool_name] = inst
    return inst


_register(type("ToxinPred3", (BaseClient,), {"tool_name": "toxinpred3"}))
_register(type("Tipred",     (BaseClient,), {"tool_name": "tipred"}))
_register(type("Algpred2",   (BaseClient,), {"tool_name": "algpred2"}))
_register(type("SoDoPE",     (BaseClient,), {"tool_name": "sodope"}))
_register(type("AnOxPePred", (BaseClient,), {"tool_name": "anoxpepred"}))
_register(_register.__globals__["Hemopi2Client"])
_register(type("Plm4CppS",   (BaseClient,), {"tool_name": "plm4cpps"}))
_register(type("TemStaPro",  (BaseClient,), {"tool_name": "temstapro"}))
_register(_register.__globals__["MhcflurryClient"])
# bepipred3 跳过(性价比太低),但保留占位以便失败时友好报错
TOOL_REGISTRY["bepipred3"] = type("BepiPred3", (BaseClient,), {"tool_name": "bepipred3"})()
