"""
服务依赖映射 — stages4 每轮需要的微服务。

8 轮 (round0 ~ round7)，每轮只启动自己需要的服务。
"""

from __future__ import annotations

from typing import Any

ROUND_SERVICES: dict[str, dict[str, Any]] = {
    "round0": {
        "services": [],
        "profiles": [],
        "desc": "数据预处理（纯本地，可复用 stages3 DB）",
    },
    "round1": {
        "services": ["anoxpepred", "algpred2"],
        "profiles": ["gpu", "cpu"],
        "desc": "抗氧化单指标分选 — AnOxPePred(排序) + AlgPred2(硬阈值)",
    },
    "round2": {
        "services": ["toxinpred3", "hemopi2", "mhcflurry"],
        "profiles": ["cpu", "cpu", "gpu"],
        "desc": "安全筛检 — ToxinPred3(毒性) + HemoPI2(溶血) + MHCflurry(免疫)",
    },
    "round3": {
        "services": ["bepipred3", "temstapro", "sodope", "plm4cpps",
                     "toxinpred3", "toxinpred3-2", "toxinpred3-3"],
        "profiles": ["gpu", "gpu", "cpu", "gpu", "cpu", "cpu", "cpu"],
        "desc": "深度评分 + ToxinPred3 — BepiPred3 + TemStaPro + SoDoPE + pLM4CPPs + ToxinPred3×3",
    },
    "round4": {
        "services": ["sodope", "temstapro"],
        "profiles": ["cpu", "gpu"],
        "desc": "Construct 枚举 — SoDoPE(溶解度) + TemStaPro(热稳定)",
    },
    "round5": {
        "services": ["omegafold"],
        "profiles": ["gpu"],
        "desc": "3D 结构预测 — OmegaFold",
    },
    "round6": {
        "services": ["sasa", "aggrescan3d"],
        "profiles": ["cpu"],
        "desc": "PDB 评估 — SASA(溶剂可及) + Aggrescan3D(聚集)",
    },
    "round7": {
        "services": [],
        "profiles": [],
        "desc": "最终排名（纯本地，无服务依赖）",
    },
}


def get_round_services(round_name: str) -> dict[str, Any]:
    """查询指定 round 依赖的服务信息。"""
    info = ROUND_SERVICES.get(round_name)
    if info is None:
        raise KeyError(f"Unknown round: {round_name!r}. Valid: {list(ROUND_SERVICES.keys())}")
    return info


def validate_round(round_name: str) -> bool:
    """检查 round 名是否有效。"""
    return round_name in ROUND_SERVICES
