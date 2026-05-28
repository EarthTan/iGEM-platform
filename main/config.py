"""
全局配置中心 — 微服务地址。

本文件是流水线唯一的参数控制面板。
修改服务地址/端口不需要改动任何业务逻辑代码，只改这里即可。
"""

from __future__ import annotations

import os

# ═══════════════════════════════════════════════════════════════════════════════
# 微服务 URL 配置
# ═══════════════════════════════════════════════════════════════════════════════
#
# 每个微服务是独立运行的 FastAPI 进程，默认监听在 127.0.0.1 的不同端口。
# group 字段决定该服务在流水线中的角色：
#   - "score"     → 参与肽序列综合评分
#   - "filter"    → 参与肽序列硬过滤（一票否决）
#   - "structure" → 序列生成 3D 结构
#   - "pdb_score" → PDB 结构评分
#
# 远程服务：设置环境变量 {NAME}_HOST 可将某个服务的地址指向远程机器。
#   例：export ANOXPEPRED_HOST=192.168.1.100   # GPU 服务器
#       export ANOXPEPRED_PORT=8001            # 可选，默认用配置中的端口

SERVICE_HOST = "127.0.0.1"

SERVICES: dict[str, dict] = {
    # ═══════ 评分型服务 ═══════
    "anoxpepred":   {"port": 8001, "group": "score"},
    "bepipred3":    {"port": 8002, "group": "score"},
    "mhcflurry":    {"port": 8005, "group": "score"},
    "plm4cpps":     {"port": 8006, "group": "score"},
    "tipred":       {"port": 8007, "group": "score"},
    "graphcpp":     {"port": 8009, "group": "score"},
    "temstapro":    {"port": 8010, "group": "score"},
    "sodope":       {"port": 8012, "group": "score"},

    # ═══════ 过滤型服务 ═══════
    "toxinpred3":   {"port": 8003, "group": "filter"},
    "toxinpred3-2": {"port": 8013, "group": "filter"},
    "toxinpred3-3": {"port": 8014, "group": "filter"},
    "hemopi2":      {"port": 8004, "group": "filter"},
    "algpred2":     {"port": 8008, "group": "filter"},

    # ═══════ 结构预测服务 ═══════
    "alphafold3":   {"port": 8201, "group": "structure"},
    "pepfold4":     {"port": 8202, "group": "structure"},
    "esmfold":      {"port": 8203, "group": "structure"},
    "omegafold":    {"port": 8204, "group": "structure"},
    "waveflow":     {"port": 8205, "group": "structure"},

    # ═══════ PDB 评分服务 ═══════
    "sasa":         {"port": 8101, "group": "pdb_score"},
    "aggrescan3d":  {"port": 8102, "group": "pdb_score"},
}


def service_url(name: str) -> str:
    """根据服务名拼接完整 HTTP base URL。

    优先级: 环境变量 {NAME}_HOST > SERVICES[name]["host"] > SERVICE_HOST。
    例如 ``export ANOXPEPRED_HOST=192.168.1.100`` 可指向远程 GPU 服务器。
    """
    env_host = os.environ.get(f"{name.upper()}_HOST")
    host = env_host or SERVICES[name].get("host", SERVICE_HOST)
    env_port = os.environ.get(f"{name.upper()}_PORT")
    port = int(env_port) if env_port else SERVICES[name]["port"]
    return f"http://{host}:{port}"
