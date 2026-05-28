"""
Stages2 Docker 工具 — 薄封装，复用 stages3 的按需启动机制。

保持与 stages3 独立，不冲突。只做健康检查和桥接 IP 检测，
不自动启动服务（假设 Docker 微服务已通过 stages3 或其他方式运行）。

用法:
    from main.stages2.docker_utils import ensure_services, detect_bridge_ip

    health = ensure_services(["anoxpepred", "algpred2"])
    if not all(h.get("available") for h in health.values()):
        sys.exit("服务不可用")
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根加入 sys.path，确保 main 包可导入
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# 从 stages3 复用核心功能（只读引用，不影响 stages3 运行）
from main.stages3.docker_utils import (
    ensure_services as _ensure_services,
    check_service_health as _check_service_health,
    detect_bridge_ip as _detect_bridge_ip,
    check_docker_daemon as _check_docker_daemon,
    wait_for_services as _wait_for_services,
)


def ensure_services(
    service_names: list[str],
    profiles: list[str] | None = None,
    timeout: float = 120.0,
) -> dict[str, dict]:
    """确保微服务可用。

    包装 stages3 的 ensure_services，添加 stages2 特定的日志前缀。

    Args:
        service_names: 服务名列表
        profiles: Docker Compose profile 列表。None 则从 SERVICES 推断
        timeout: 健康检查总超时

    Returns:
        {service_name: {"available": bool, "status": str, "error": str|None}, ...}
    """
    from main.stages2.common import log

    if not service_names:
        return {}

    log(f"Docker 服务检查: {', '.join(service_names)}")
    health = _ensure_services(service_names, profiles, timeout=timeout)

    available = [s for s, h in health.items() if h.get("available")]
    unavailable = [s for s, h in health.items() if not h.get("available")]

    if unavailable:
        log(f"⚠️ 服务不可用: {', '.join(unavailable)}")
    if available:
        log(f"✅ 服务就绪: {', '.join(available)}")

    return health


def detect_bridge_ip(container_name: str) -> str | None:
    """检测 Docker 容器 bridge IP。

    用于绕过 docker-proxy 直接连接容器（避免 httpx keep-alive 挂死）。
    """
    return _detect_bridge_ip(container_name)
