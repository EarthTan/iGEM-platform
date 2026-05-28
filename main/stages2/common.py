"""
Stages2 共享工具模块 — 8 轮共用的辅助函数。

消除原设计中每个脚本各自复制粘贴 log/describe/checkpoint 的问题。
所有脚本统一从此模块导入工具函数。

用法:
    from main.stages2.common import log, setup_stage, describe, save_checkpoint, ...
    STAGE_DIR = setup_stage("round01_lightweight")
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output2"

# ── 全局日志文件 ──
LOG_FILE: Path | None = None


# ═══════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════

def set_log_file(path: Path) -> None:
    global LOG_FILE
    LOG_FILE = path


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ═══════════════════════════════════════════════════════════════════
# 目录 / 文件
# ═══════════════════════════════════════════════════════════════════

def make_dir(stage_dir: Path, name: str) -> Path:
    """在 stage_dir 下创建子目录。"""
    d = stage_dir / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def setup_stage(stage_name: str) -> Path:
    """初始化 stage 输出目录和日志文件。返回 STAGE_DIR。"""
    stage_dir = OUTPUT_DIR / stage_name
    stage_dir.mkdir(parents=True, exist_ok=True)
    set_log_file(stage_dir / "run.log")
    return stage_dir


# ═══════════════════════════════════════════════════════════════════
# 分布统计（带直方图）
# ═══════════════════════════════════════════════════════════════════

def describe(name: str, values: list[float]) -> str:
    """生成统一格式的分布报告 + ASCII 直方图。"""
    n = len(values)
    if n == 0:
        return f"{name}: 无有效数据"

    sorted_v = sorted(values)
    mean = sum(sorted_v) / n
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    variance = sum((x - mean) ** 2 for x in sorted_v) / n
    std = variance ** 0.5
    p5 = sorted_v[int(n * 0.05)]
    p25 = sorted_v[int(n * 0.25)]
    p75 = sorted_v[int(n * 0.75)]
    p95 = sorted_v[int(n * 0.95)]

    lines = [
        f"{name} 分布 (n={n}):",
        f"  均值:   {mean:.4f}",
        f"  中位数: {median:.4f}",
        f"  标准差: {std:.4f}",
        f"  最小值: {sorted_v[0]:.4f}",
        f"  最大值: {sorted_v[-1]:.4f}",
        f"  P5: {p5:.4f}  |  P25: {p25:.4f}  |  P75: {p75:.4f}  |  P95: {p95:.4f}",
        "",
        "  分布直方图:",
    ]

    vmin = sorted_v[0]
    vmax = sorted_v[-1]
    if vmax - vmin < 0.001:
        lines.append(f"  所有值 ≈ {vmin:.4f}，无分布")
        return "\n".join(lines)

    raw_bins = 8
    bin_width = (vmax - vmin) / raw_bins
    bins = [vmin + bin_width * i for i in range(raw_bins + 1)]
    bar_width = 14

    for i in range(len(bins) - 1):
        lo = bins[i]
        hi = bins[i + 1]
        count = sum(1 for v in values if lo <= v < hi)
        pct = count / n * 100
        filled = round(count / n * bar_width) if n > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        marker = "← 均值" if lo <= mean < hi else ""
        lines.append(f"  {lo:.4f}-{hi:.4f}: {bar}  ({count:,} 条, {pct:.1f}%) {marker}")

    lines.append("")
    return "\n".join(lines)


def describe_dict(name: str, values: list[float]) -> dict[str, Any]:
    """返回分布统计的 dict（用于 JSON 导出）。"""
    n = len(values)
    if n == 0:
        return {"name": name, "n": 0, "mean": None}
    sorted_v = sorted(values)
    mean = sum(sorted_v) / n
    median = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    variance = sum((x - mean) ** 2 for x in sorted_v) / n
    std = variance ** 0.5
    p5 = sorted_v[int(n * 0.05)]
    p25 = sorted_v[int(n * 0.25)]
    p75 = sorted_v[int(n * 0.75)]
    p95 = sorted_v[int(n * 0.95)]
    return {
        "name": name, "n": n, "mean": round(mean, 4), "median": round(median, 4),
        "std": round(std, 4), "min": round(sorted_v[0], 4), "max": round(sorted_v[-1], 4),
        "p5": round(p5, 4), "p25": round(p25, 4), "p75": round(p75, 4), "p95": round(p95, 4),
    }


# ═══════════════════════════════════════════════════════════════════
# 安全标记（用于主排名的 caution/danger 标记）
# ═══════════════════════════════════════════════════════════════════

def calc_safety_flag(
    peptide: dict,
    thresholds: dict[str, dict[str, float]],
) -> str:
    """根据服务分数和安全阈值计算安全标记。

    thresholds 格式:
        {"toxinpred3": {"caution": 0.60, "danger": 0.80}, ...}

    返回 "safe" 或 "toxinpred3:caution(0.65);hemopi2:danger(0.88)"
    """
    flags = []
    for svc_name, cfg in thresholds.items():
        score = peptide.get(svc_name)
        if score is None:
            continue
        if score >= cfg["danger"]:
            flags.append(f"{svc_name}:danger({score:.3f})")
        elif score >= cfg["caution"]:
            flags.append(f"{svc_name}:caution({score:.3f})")
    return ";".join(flags) if flags else "safe"


# ═══════════════════════════════════════════════════════════════════
# 检查点（断点续跑）
# ═══════════════════════════════════════════════════════════════════

CHECKPOINT_FILENAME = "checkpoint.json"


def save_checkpoint(
    stage_dir: Path,
    completed_ids: list[str] | set[str],
    extra: dict[str, Any] | None = None,
) -> None:
    """保存检查点到 stage 目录。

    格式: {"completed_ids": [...], "timestamp": "...", ...extra}
    """
    data: dict[str, Any] = {
        "completed_ids": sorted(completed_ids) if isinstance(completed_ids, set) else completed_ids,
        "timestamp": datetime.now().isoformat(),
    }
    if extra:
        data.update(extra)
    write_json(stage_dir / CHECKPOINT_FILENAME, data)


def load_checkpoint(stage_dir: Path) -> tuple[set[str], dict[str, Any]]:
    """加载检查点，返回 (completed_ids_set, extra_data)。"""
    path = stage_dir / CHECKPOINT_FILENAME
    if not path.exists():
        return set(), {}
    data = read_json(path)
    completed = set(data.get("completed_ids", []))
    extra = {k: v for k, v in data.items() if k not in ("completed_ids", "timestamp")}
    return completed, extra


# ═══════════════════════════════════════════════════════════════════
# Batch 处理工具
# ═══════════════════════════════════════════════════════════════════

def chunk_list(items: list, chunk_size: int) -> list[list]:
    """将列表按 chunk_size 分块。"""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


async def safe_gather(tasks: list, description: str = "") -> list:
    """安全地并发执行任务，隔离异常。

    - return_exceptions=True 防止单任务失败取消全部
    - 每 50 个任务分批，避免一次性创建大量协程
    """
    from asyncio import gather

    results = []
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch_tasks = tasks[i:i + batch_size]
        batch_results = await gather(*batch_tasks, return_exceptions=True)
        for j, r in enumerate(batch_results):
            if isinstance(r, Exception):
                results.append(None)
            else:
                results.append(r)
    return results


# ═══════════════════════════════════════════════════════════════════
# Bottom-N 安全筛选（抗氧化最差但其他维度安全的肽）
# ═══════════════════════════════════════════════════════════════════

BOTTOM_SAFETY_THRESHOLDS: dict[str, float] = {
    "toxinpred3": 0.60,
    "algpred2": 0.50,
    "hemopi2": 0.70,
    "mhcflurry": 0.50,
    "bepipred3": 0.60,
}


def is_safe_in_all_dimensions(
    peptide: dict,
    thresholds: dict[str, float] | None = None,
) -> bool:
    """检查肽是否在所有安全维度上通过阈值。

    用于 Bottom-N 筛选：只保留安全维度正常、仅抗氧化活性低的肽。
    返回 True 表示该肽在所有安全维度上都正常。
    """
    th = thresholds or BOTTOM_SAFETY_THRESHOLDS
    for svc, threshold in th.items():
        score = peptide.get(svc)
        if score is not None and score >= threshold:
            return False
    return True


def select_bottom_n(
    scored_peptides: list[dict],
    n: int = 10,
    score_key: str = "anoxpepred",
    thresholds: dict[str, float] | None = None,
) -> list[dict]:
    """从已评分肽中选择 Bottom-N。

    筛选逻辑：
        1. 只保留所有安全维度通过阈值的肽
        2. 按 score_key（默认 AnOxPePred）升序排列
        3. 取最后 N 条（活性最低）

    返回 Bottom-N 肽列表（按 score_key 升序排列）。
    """
    safe = [p for p in scored_peptides if is_safe_in_all_dimensions(p, thresholds)]
    safe.sort(key=lambda p: p.get(score_key) or 0)
    return safe[:n]


# ═══════════════════════════════════════════════════════════════════
# CSV 工具
# ═══════════════════════════════════════════════════════════════════

def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """写入 CSV 文件。"""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    """读取 CSV 文件，返回字典列表。"""
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows
