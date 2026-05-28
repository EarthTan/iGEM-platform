"""
Docker 按需启动工具 — stages4 每轮只启动自己需要的微服务。

用法:
    from main.stages4.s4_docker_utils import ensure_services

    health = ensure_services(["anoxpepred", "algpred2"])
    health = ensure_services(["anoxpepred", "algpred2"])  # 幂等，瞬间返回

从 stages3/docker_utils.py 复制，仅修改了命令行入口的模块路径。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from main.config import SERVICES, service_url

# ────────────────────────────────────────────────────────────────
# 路径
# ────────────────────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
COMPOSE_FILE = TOOLS_DIR / "docker-compose.yml"


# ────────────────────────────────────────────────────────────────
# 缓存
# ────────────────────────────────────────────────────────────────

_bridge_cache: dict[str, str] = {}
_health_cache: dict[str, dict] = {}
_started_profiles: set[str] = set()


# ────────────────────────────────────────────────────────────────
# Docker 守护进程检查
# ────────────────────────────────────────────────────────────────

def check_docker_daemon() -> bool:
    """检查 Docker daemon 是否运行。"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ────────────────────────────────────────────────────────────────
# 按需启动
# ────────────────────────────────────────────────────────────────

def start_services(profiles: list[str], services: list[str]) -> bool:
    """通过 Docker Compose 启动指定 profile 下的服务。"""
    profiles_to_start = [p for p in profiles if p not in _started_profiles]
    if not profiles_to_start:
        return True

    if not COMPOSE_FILE.exists():
        print(f"  [docker] docker-compose.yml 不存在: {COMPOSE_FILE}")
        return False

    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    for p in profiles_to_start:
        cmd.extend(["--profile", p])
    cmd.extend(["up", "-d"])
    cmd.extend(services)

    print(f"  [docker] 启动: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            for p in profiles_to_start:
                _started_profiles.add(p)
            return True
        else:
            print(f"  [docker] 启动失败 (rc={result.returncode}): {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print("  [docker] 启动超时 (120s)")
        return False


# ────────────────────────────────────────────────────────────────
# Bridge IP 检测
# ────────────────────────────────────────────────────────────────

def detect_bridge_ip(container_name: str) -> str | None:
    """检测容器的 bridge 网络 IP。"""
    if container_name in _bridge_cache:
        return _bridge_cache[container_name]

    try:
        result = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        if not data:
            return None

        networks = data[0].get("NetworkSettings", {}).get("Networks", {})
        for net_name, net_info in networks.items():
            if "bridge" in net_name.lower():
                ip = net_info.get("IPAddress")
                if ip:
                    _bridge_cache[container_name] = ip
                    return ip
        return None
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


# ────────────────────────────────────────────────────────────────
# 健康检查
# ────────────────────────────────────────────────────────────────

async def check_service_health(
    service_name: str,
    timeout: float = 10.0,
) -> dict:
    """检查单个微服务的健康状态。"""
    url = f"{service_url(service_name)}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "available": data.get("model_loaded", False),
                    "status": data.get("status", "healthy"),
                    "error": None,
                }
            else:
                return {
                    "available": False,
                    "status": f"HTTP {resp.status_code}",
                    "error": f"HTTP {resp.status_code}",
                }
    except Exception as e:
        return {
            "available": False,
            "status": "unreachable",
            "error": str(e),
        }


async def wait_for_services(
    services: list[str],
    timeout: float = 120.0,
    poll_interval: float = 5.0,
) -> dict[str, dict]:
    """轮询等待指定服务全部变为 healthy。"""
    health: dict[str, dict] = {}
    start = time.monotonic()

    pending = set(services)
    while pending and (time.monotonic() - start) < timeout:
        for svc in list(pending):
            result = await check_service_health(svc)
            if result["available"]:
                health[svc] = result
                pending.remove(svc)
                print(f"  [health] {svc}: ✅ 可用")
            else:
                print(f"  [health] {svc}: ⏳ {'等待中' if pending else '不可用'}")

        if pending and (time.monotonic() - start) < timeout:
            await asyncio.sleep(poll_interval)

    for svc in pending:
        health[svc] = {"available": False, "status": "timeout", "error": "health check timeout"}
        print(f"  [health] {svc}: ❌ 超时")

    return health


# ────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────

def ensure_services(
    service_names: list[str],
    profiles: list[str] | None = None,
    timeout: float = 120.0,
    skip_docker: bool = False,
) -> dict[str, dict]:
    """
    确保指定的微服务可用。幂等 — 多次调用不重复启动。

    Args:
        service_names: 要确保可用的服务名列表
        profiles: Docker Compose profile 列表
        timeout: 健康检查总超时（秒）
        skip_docker: 如果 True，跳过 Docker 启动步骤

    Returns:
        {service_name: {"available": bool, "status": str, "error": str|None}, ...}
    """
    if not service_names:
        return {}

    cached_all = all(
        svc in _health_cache and _health_cache[svc].get("available")
        for svc in service_names
    )
    if cached_all:
        return {svc: _health_cache[svc] for svc in service_names}

    print(f"\n  [docker] 确保服务可用: {', '.join(service_names)}")

    if not skip_docker:
        if not check_docker_daemon():
            print("  [docker] ❌ Docker daemon 未运行。请先启动 Docker。")
            result: dict[str, dict] = {}
            for svc in service_names:
                result[svc] = {"available": False, "status": "docker_not_running",
                               "error": "Docker daemon not running"}
                _health_cache[svc] = result[svc]
            return result

        if profiles:
            start_services(profiles, service_names)

    print(f"  [docker] 等待服务就绪 (超时 {timeout}s)...")
    health = asyncio.run(wait_for_services(service_names, timeout))

    for svc, h in health.items():
        _health_cache[svc] = h

    available = [s for s, h in health.items() if h.get("available")]
    unavailable = [s for s, h in health.items() if not h.get("available")]

    if unavailable:
        print(f"  [docker] ⚠️ 部分服务不可用: {', '.join(unavailable)}")
    if available:
        print(f"  [docker] ✅ 就绪: {', '.join(available)}")

    return health


def clear_cache() -> None:
    """清空所有缓存（通常在 round 切换时调用）。"""
    _bridge_cache.clear()
    _health_cache.clear()
    _started_profiles.clear()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: uv run python -m main.stages4.s4_docker_utils <service_name> [...]")
        sys.exit(1)
    health = ensure_services(sys.argv[1:])
    for svc, h in health.items():
        status = "✅" if h["available"] else "❌"
        print(f"  {status} {svc}: {h['status']}")
