"""
============================================================================
 微服务 HTTP 客户端 — 异步并发调用所有评分/过滤服务
============================================================================

本模块封装了与 10 个微服务的全部 HTTP 通信。所有微服务共享相同的 API 契约
（由 ``tools/template/fasta_service.py`` 定义）：

  GET  /health        → {"status": "healthy", "model_loaded": true, ...}
  POST /predict       → 单序列预测
  POST /predict/batch → 批量预测（一次最多 1000 条）

设计要点
--------
1. 异步并发（asyncio + httpx）
   - 健康检查：10 个服务同时 ping，不排队
   - 批量评分：每个服务独立发送全部肽的 batch 请求，各服务并行执行
   - 同一个服务内的批量请求由微服务自身处理并发（内部 Semaphore(10)）

2. 故障容错
   - 健康检查失败的服务自动跳过，不影响其他服务
   - 单个服务调用失败不会被其他服务阻断（asyncio.gather 的异常隔离）
   - 所有错误收集到 errors 列表，写入 step05 输出供追溯

3. 为什么是肽级别评分而不是 construct 级别？
   - 这些模型（AnOxPePred, ToxinPred3 等）训练数据是短肽（5–50 aa）
   - 融合蛋白 construct 长度 350–400 aa，远超模型训练分布
   - 对全长蛋白的预测结果不可靠
   - 因此：先对功能肽评分，construct 继承其肽的分数
   - construct 之间的差异体现在"插入位置的结构兼容性"（见 enumeration.py 禁入区）
"""

from __future__ import annotations

import asyncio
import time

import httpx

from main.config import SERVICES, service_url


class ServiceClient:
    """
    微服务 HTTP 客户端。

    使用方式
    --------
        client = ServiceClient()
        try:
            health = await client.check_health()
            scores = await client.evaluate_peptides(peptides, health=health)
        finally:
            await client.close()
    """

    def __init__(self, timeout: float = 120.0):
        """
        timeout: 单次 HTTP 请求的超时秒数。
        默认 120s，因为首次调用某些服务可能需要加载模型（如 ESM-2 下载）。
        """
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        """
        懒加载 HTTP 客户端实例。
        复用同一个 AsyncClient（底层复用 TCP 连接池），避免重复建连开销。
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """释放 HTTP 连接池。应在流水线结束时调用。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ────────────────────────────────────────────────────────────────
    # 健康检查
    # ────────────────────────────────────────────────────────────────

    async def check_health(self, service_names: list[str] | None = None) -> dict:
        """
        并发 ping 所有微服务的 /health 端点。

        返回每个服务的状态字典：
            {
              "anoxpepred": {
                "available": True/False,    # 模型是否已加载且就绪
                "status": "healthy"/"loading"/"unreachable",
                "tool_name": "anoxpepred",
                "error": null / "Connection refused"
              },
              ...
            }

        通过 asyncio.gather 并发执行，10 个服务的检查耗时 ≈ 最慢那个的时间。
        """
        if service_names is None:
            service_names = list(SERVICES.keys())

        client = await self._get_client()
        results: dict[str, dict] = {}

        async def _check_one(name: str) -> None:
            """检查单个服务。异常被捕获，不向上传播。"""
            url = f"{service_url(name)}/health"
            try:
                # 健康检查给较短超时（10s），快速判断服务是否存活
                resp = await client.get(url, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    results[name] = {
                        "available": data.get("model_loaded", False),
                        "status": data.get("status", "unknown"),
                        "tool_name": data.get("tool_name", name),
                        "error": None,
                    }
                else:
                    results[name] = {
                        "available": False,
                        "status": f"HTTP {resp.status_code}",
                        "error": f"HTTP {resp.status_code}",
                    }
            except Exception as e:
                # 连接被拒绝、超时、DNS 解析失败等
                results[name] = {
                    "available": False,
                    "status": "unreachable",
                    "error": str(e),
                }

        # 并发检查所有服务
        tasks = [_check_one(name) for name in service_names]
        await asyncio.gather(*tasks)
        return results

    # ────────────────────────────────────────────────────────────────
    # 单序列预测
    # ────────────────────────────────────────────────────────────────

    async def predict_single(self, service_name: str, sequence: str,
                             peptide_id: str = "unknown") -> dict:
        """
        对单条序列调用指定微服务的 /predict 端点。

        主要用于调试和手动验证，流水线中批量预测更高效。
        """
        client = await self._get_client()
        url = f"{service_url(service_name)}/predict"
        payload = {"sequence": sequence, "peptide_id": peptide_id}
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e), "peptide_id": peptide_id}

    # ────────────────────────────────────────────────────────────────
    # 批量预测
    # ────────────────────────────────────────────────────────────────

    async def predict_batch(self, service_name: str, sequences: list[dict]) -> dict:
        """
        对多条序列调用指定微服务的 /predict/batch 端点。

        参数
        ----
        sequences: [{"sequence": "YVPLPNVPQG", "peptide_id": "pep_001"}, ...]

        返回
        ----
        {
          "success": True/False,
          "results": [{"peptide_id": ..., "score": 0.85, "label": "...", "details": {...}}, ...],
          "total": 100,
          "error": null / "error message"
        }

        失败时返回 success=False，errors 列表收集所有失败信息。
        """
        client = await self._get_client()
        url = f"{service_url(service_name)}/predict/batch"
        payload = {"sequences": sequences}
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e), "results": [], "total": 0}

    # ────────────────────────────────────────────────────────────────
    # PDB 结构评分
    # ────────────────────────────────────────────────────────────────

    async def predict_pdb_single(
        self,
        service_name: str,
        pdb_content: str,
        peptide_id: str = "unknown",
        sequence: str | None = None,
        chain_id: str | None = None,
    ) -> dict:
        """
        对单个 PDB 结构调用指定 PDB 评分服务的 /predict 端点。

        适用于 sasa、aggrescan3d 等 pdb_service。注意它和序列评分服务不同：
        payload 中传的是 pdb_content，而不是 sequence。
        """
        client = await self._get_client()
        url = f"{service_url(service_name)}/predict"
        payload = {
            "pdb_content": pdb_content,
            "peptide_id": peptide_id,
            "sequence": sequence,
            "chain_id": chain_id,
        }
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e), "peptide_id": peptide_id}

    async def predict_pdb_batch(self, service_name: str, requests: list[dict]) -> dict:
        """
        对多个 PDB 结构调用指定 PDB 评分服务的 /predict/batch 端点。

        requests 示例：
            [{"pdb_content": "...", "peptide_id": "construct_001", "chain_id": "A"}]
        """
        client = await self._get_client()
        url = f"{service_url(service_name)}/predict/batch"
        payload = {"requests": requests}
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e), "results": [], "total": 0}

    # ────────────────────────────────────────────────────────────────
    # 异步结构预测（AlphaFold3 async Job 模式）
    # ────────────────────────────────────────────────────────────────

    async def predict_structure_async(
        self,
        service_name: str,
        sequence: str,
        peptide_id: str = "unknown",
        poll_interval: float = 30.0,
        timeout: float = 7200.0,
    ) -> dict:
        """
        通过异步 Job 模式提交结构预测，轮询等待完成。

        对上层保持同步接口（await 返回最终结果），内部轮询:

            1. POST /predict/async        → job_id
            2. 每隔 poll_interval 秒 GET /status/{job_id}
            3. 完成后 GET /result/{job_id} → StructureResult

        参数
        ----
        service_name : 服务名 (支持 async 模式的服务: alphafold3, esmfold, omegafold)
        sequence     : 氨基酸序列
        peptide_id   : 序列编号
        poll_interval: 轮询间隔（秒，默认 30s）
        timeout      : 总超时（秒，默认 2h）

        返回
        ----
        {
            "success": True/False,
            "peptide_id": "...",
            "sequence": "...",
            "pdb_content": "...",
            "confidence": 0.87,
            "details": {...},
            "error": null / "error message"
        }
        """
        client = await self._get_client()
        base_url = service_url(service_name)

        # 1. 提交异步任务
        try:
            resp = await client.post(
                f"{base_url}/predict/async",
                json={"sequence": sequence, "peptide_id": peptide_id},
                timeout=30.0,
            )
            if resp.status_code != 202:
                return {
                    "success": False,
                    "peptide_id": peptide_id,
                    "error": f"Submit failed: HTTP {resp.status_code}",
                }
            data = resp.json()
            job_id = data.get("job_id")
            if not job_id:
                return {
                    "success": False,
                    "peptide_id": peptide_id,
                    "error": "No job_id in response",
                }
        except Exception as e:
            return {"success": False, "peptide_id": peptide_id, "error": str(e)}

        # 2. 轮询等待
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return {
                    "success": False,
                    "peptide_id": peptide_id,
                    "error": f"Timed out after {timeout}s",
                    "job_id": job_id,
                }

            await asyncio.sleep(poll_interval)

            try:
                status_resp = await client.get(
                    f"{base_url}/status/{job_id}", timeout=15.0
                )
                if status_resp.status_code == 404:
                    return {
                        "success": False,
                        "peptide_id": peptide_id,
                        "error": f"Job {job_id!r} not found on server (may have been cleaned up or service restarted)",
                        "job_id": job_id,
                    }
                if status_resp.status_code != 200:
                    continue
                status_data = status_resp.json()
                job_status = status_data.get("status", "unknown")

                if job_status == "success":
                    # 获取结果
                    result_resp = await client.get(
                        f"{base_url}/result/{job_id}", timeout=30.0
                    )
                    if result_resp.status_code == 200:
                        r = result_resp.json()
                        return {
                            "success": True,
                            "peptide_id": peptide_id,
                            "sequence": sequence,
                            "pdb_content": r.get("pdb_content", ""),
                            "confidence": r.get("confidence"),
                            "details": r.get("details", {}),
                            "job_id": job_id,
                            "error": None,
                        }
                    else:
                        return {
                            "success": False,
                            "peptide_id": peptide_id,
                            "error": "Result endpoint failed after success",
                            "job_id": job_id,
                        }

                elif job_status == "failed":
                    return {
                        "success": False,
                        "peptide_id": peptide_id,
                        "error": status_data.get("progress", "Job failed"),
                        "job_id": job_id,
                    }

            except Exception as e:
                # 网络抖动等临时错误 — 继续轮询
                continue

    # ────────────────────────────────────────────────────────────────
    # 全服务评估（流水线 Step 5 的核心调用）
    # ────────────────────────────────────────────────────────────────

    async def evaluate_peptides(
        self,
        peptides: list[dict],
        service_names: list[str] | None = None,
        health: dict | None = None,
    ) -> dict:
        """
        对肽列表并发调用所有可用微服务，返回聚合评分。

        这是 Step 5 的核心逻辑：

        1. 健康检查（或复用已缓存的结果）
        2. 筛选可用服务列表
        3. 对每个可用服务，发送全部肽的批量请求
        4. 各服务并发执行（asyncio.gather）
        5. 结果按 peptide_id 聚合为统一字典

        参数
        ----
        peptides     : 通过 Step 2 预筛选的肽列表
        service_names: 要调用的服务名（默认全部 10 个）
        health       : 预先执行的健康检查结果（避免重复 ping）

        返回
        ----
        {
          "peptide_scores": {
            "pep_001": {
              "sequence": "KELEEK",
              "anoxpepred":  {"score": 0.85, "label": "antioxidant", "details": {...}},
              "toxinpred3":  {"score": 0.12, "label": "Non-Toxin", "details": {...}},
              ...
            },
            ...
          },
          "service_status": {
            "available": ["anoxpepred", "toxinpred3", ...],
            "unavailable": ["graphcpp", ...]
          },
          "errors": [
            {"service": "graphcpp", "error": "Connection refused"},
            ...
          ]
        }
        """
        if service_names is None:
            service_names = list(SERVICES.keys())

        # ── 确定可用服务 ──
        if health is None:
            health = await self.check_health(service_names)
        available = [n for n in service_names if health.get(n, {}).get("available")]
        unavailable = [n for n in service_names if not health.get(n, {}).get("available")]

        # ── 初始化结果容器 ──
        # peptide_scores: {peptide_id → {sequence, service_name → {score, label, details}}}
        peptide_scores: dict[str, dict] = {}
        for pep in peptides:
            pid = pep.get("peptide_id", pep.get("sequence", "unknown"))
            peptide_scores[pid] = {"sequence": pep["sequence"]}

        errors: list[dict] = []

        # ── 并发调用各服务 ──
        async def _eval_service(name: str) -> None:
            """
            对单个服务：构建批量请求 → 发送 → 解析结果 → 写入 peptide_scores。

            异常不会中断其他服务的执行（被 asyncio.gather 容错）。
            """
            try:
                # 构建该服务的批量请求（所有肽一起发送）
                batch = [
                    {
                        "sequence": pep["sequence"],
                        "peptide_id": pep.get("peptide_id", pep.get("sequence", "unknown")),
                    }
                    for pep in peptides
                ]
                result = await self.predict_batch(name, batch)

                # 解析返回结果，按 peptide_id 写入对应条目
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        pid = r.get("peptide_id", "unknown")
                        if pid in peptide_scores:
                            peptide_scores[pid][name] = {
                                "score": r.get("score"),
                                "label": r.get("label"),
                                "details": r.get("details", {}),
                            }
                else:
                    errors.append({
                        "service": name,
                        "error": result.get("error", "no results returned"),
                    })
            except Exception as e:
                errors.append({"service": name, "error": str(e)})

        if available:
            tasks = [_eval_service(name) for name in available]
            await asyncio.gather(*tasks)
        # 注意：asyncio.gather 默认不会因单个 task 异常而取消其他 task，
        # 因为我们内部已经 try/except 了所有异常

        return {
            "peptide_scores": peptide_scores,
            "service_status": {
                "available": available,
                "unavailable": unavailable,
            },
            "errors": errors,
        }

    # ────────────────────────────────────────────────────────────────
    # 评分映射：肽 → construct
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def map_scores_to_constructs(
        constructs: list[dict],
        peptide_scores: dict[str, dict],
    ) -> list[dict]:
        """
        将肽级别的评分"广播"到每条 construct。

        每条 construct 通过其 peptide_id 查找对应的评分数据。
        同一个肽在不同插入位置的所有 construct 共享相同的微服务评分。

        这是纯 CPU 操作（无 I/O），所以不需要 async。
        """
        scored_constructs: list[dict] = []
        for c in constructs:
            pid = c["peptide_id"]
            scores = peptide_scores.get(pid, {})
            # 复制评分数据到 construct，排除 sequence 字段（已存在于 construct 本身）
            c["service_scores"] = {
                k: v for k, v in scores.items()
                if k != "sequence"
            }
            scored_constructs.append(c)
        return scored_constructs
