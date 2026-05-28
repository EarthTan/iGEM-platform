"""
Round 3: 重服务评分 — 50K 双通道 (TemStaPro 预筛版)

流程:
  1. TemStaPro 跑全部 50K (快, ~15min)
  2. 每个通道取 TemStaPro 前 30% → 15K 候选
  3. BepiPred-3.0 只跑这 15K (~1h)
  4. 7 服务加权综合分 → top80 + bottom10

双通道输出:
  - Top 通道: 在 top25K 的 TemStaPro 前 30% 中按 7 服务综合分取 Top 80
  - Bottom 通道: 在 bottom25K 的 TemStaPro 前 30% 中安全过滤 + 抗氧化最差 10 条

用法:
    uv run python -m main.stages2.round03_heavy

输入:
    output2/round02_scoring/final/all_50k.csv

输出:
    output2/round03_heavy/
    ├── README.md              ← 双通道报告
    ├── run.log
    ├── scores/                ← 原始返回 (JSON)
    ├── final/
    │   ├── all_scored.csv     ← 全 50K 评分明细 (BepiPred3 仅 15K 有值)
    │   ├── top80.csv          ← Top 通道 Top 80
    │   ├── bottom10.csv       ← Bottom 通道阴性对照
    │   ├── trajectory.csv     ← 跨轮排名轨迹 (R1→R2→R3)
    │   └── danger_list.csv    ← 高危肽清单
    └── stats.json             ← 统计摘要
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient

from main.stages2.common import (
    OUTPUT_DIR, calc_safety_flag, describe, log, make_dir,
    read_csv, select_bottom_n, setup_stage, write_csv, write_json,
)

STAGE = "round03_heavy"
STAGE_DIR = OUTPUT_DIR / STAGE

# ── 批处理参数 ──
MAX_BATCH_SIZE = 200
PER_BATCH_TIMEOUT = 300

# ── TemStaPro 预筛比例 ──
# 每个通道取 TemStaPro 得分最高前 30% 送 BepiPred3
TEMSTAPRO_TOP_PCT = 0.30

# ── Round 3 新增服务配置 ──
# (服务名, 权重, 是否反向, 描述)
NEW_SERVICES = [
    ("bepipred3", 0.10, True, "B 细胞表位 (反向)"),
]
TEMSTAPRO_CFG = ("temstapro", 0.05, False, "热稳定性 (正向, 可选)")

# ── 全部服务权重 ──
# 不包含 TemStaPro 时的权重
BASE_WEIGHTS = {
    "anoxpepred":  0.50,
    "toxinpred3":  0.15,
    "algpred2":    0.10,
    "hemopi2":     0.10,
    "mhcflurry":   0.05,
    "bepipred3":   0.10,
}
# 包含 TemStaPro 时的权重
WITH_TEMSTAPRO_WEIGHTS = {
    "anoxpepred":  0.45,
    "toxinpred3":  0.13,
    "algpred2":    0.09,
    "hemopi2":     0.09,
    "mhcflurry":   0.05,
    "bepipred3":   0.10,
    "temstapro":   0.09,
}

# 反向服务: 分数越高越差, 需要取 1-score
REVERSE_SERVICES = {"toxinpred3", "algpred2", "hemopi2", "mhcflurry", "bepipred3"}

# 安全阈值 (用于主排名 caution/danger 标记)
SAFETY_THRESHOLDS = {
    "toxinpred3": {"caution": 0.60, "danger": 0.80},
    "algpred2":   {"caution": 0.50, "danger": 0.70},
    "hemopi2":    {"caution": 0.70, "danger": 0.85},
    "bepipred3":  {"caution": 0.60, "danger": 0.80},
}

# ── 双通道参数 ──
TOP_N = 80
BOTTOM_N = 10
ROUND2_INPUT = OUTPUT_DIR / "round02_scoring" / "final" / "all_50k.csv"


# ═══════════════════════════════════════════════════════════════════════
# 并发批处理
# ═══════════════════════════════════════════════════════════════════════

async def process_service(
    client: ServiceClient,
    service_name: str,
    chunks: list[list[dict]],
) -> dict[str, dict]:
    """对一个服务并发跑所有批次, 返回 {peptide_id: {score, label}}。"""
    sem = asyncio.Semaphore(2)
    all_results: dict[str, dict] = {}
    errors = 0
    total = sum(len(c) for c in chunks)

    async def process_chunk(chunk: list[dict]) -> None:
        nonlocal errors
        async with sem:
            try:
                result = await asyncio.wait_for(
                    client.predict_batch(service_name, chunk),
                    timeout=PER_BATCH_TIMEOUT,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        pid = r.get("peptide_id", "unknown")
                        all_results[pid] = {"score": r.get("score"), "label": r.get("label", "")}
                else:
                    errors += 1
                    for item in chunk:
                        all_results[item.get("peptide_id", "unknown")] = {
                            "score": None, "label": "SERVICE_ERROR"
                        }
            except asyncio.TimeoutError:
                errors += 1
                for item in chunk:
                    all_results[item.get("peptide_id", "unknown")] = {
                        "score": None, "label": "TIMEOUT"
                    }
            except Exception as e:
                errors += 1
                for item in chunk:
                    all_results[item.get("peptide_id", "unknown")] = {
                        "score": None, "label": f"ERROR:{str(e)[:60]}"
                    }

    tasks = [process_chunk(chunk) for chunk in chunks]
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        progress = min((i + batch_size) * MAX_BATCH_SIZE, total)
        log(f"  {service_name}: {progress:,}/{total:,} ({progress/total*100:.0f}%) | errors={errors}")

    log(f"  [{service_name}] {total:,} 完成, {errors} 批次错误")
    return all_results


# ═══════════════════════════════════════════════════════════════════════
# 加权综合分计算
# ═══════════════════════════════════════════════════════════════════════

def compute_r3_score(
    peptide: dict,
    all_weights: dict[str, float],
    new_results: dict[str, dict[str, dict]],
) -> tuple[float | None, list[str]]:
    """计算 Round 3 加权综合分。

    正向服务: score 直接 clamp(0,1)
    反向服务: 1 - clamp(score, 0, 1)
    综合分 = sum(normalized_i * weight_i) / sum(weight_i)
    """
    weighted_sum = 0.0
    total_weight = 0.0
    missing = []

    for svc_name, weight in all_weights.items():
        reverse = svc_name in REVERSE_SERVICES
        # 新服务的分优先取自 new_results, 已有服务的分从 CSV 读
        if svc_name in new_results:
            svc_data = new_results[svc_name].get(peptide["peptide_id"], {})
            raw_score = svc_data.get("score")
        else:
            raw_score = peptide.get(svc_name)
        if raw_score is None:
            missing.append(svc_name)
            continue
        normalized = max(0.0, min(1.0, raw_score))
        if reverse:
            normalized = 1.0 - normalized
        weighted_sum += normalized * weight
        total_weight += weight

    score = round(weighted_sum / total_weight, 4) if total_weight > 0 else None
    return score, missing


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def run():
    start_time = time.time()
    setup_stage(STAGE)
    log("=" * 60)
    log("Round 3: 重服务评分 — 50K 双通道 (TemStaPro 预筛版)")
    log("  顺序: TemStaPro 全部 50K → 各通道取 TemStaPro 前 30% → BepiPred3 候选子集")
    log("=" * 60)

    # ══════════════════════════════════════════════════════════════════
    # 加载 Round 2 全部 50K
    # ══════════════════════════════════════════════════════════════════
    if not ROUND2_INPUT.exists():
        log(f"输入不存在: {ROUND2_INPUT}")
        log("请先运行: uv run python -m main.stages2.round02_scoring")
        return

    all_peptides = read_csv(ROUND2_INPUT)
    for p in all_peptides:
        for k in ("anoxpepred", "toxinpred3", "algpred2", "hemopi2", "mhcflurry"):
            p[k] = float(p[k]) if p.get(k) else None
        p["weighted_score"] = float(p["weighted_score"]) if p.get("weighted_score") else None
        p["length"] = int(p["length"])

    # ── 按 channel 分离 ──
    top_peptides = [p for p in all_peptides if p.get("channel") == "top"]
    bottom_peptides = [p for p in all_peptides if p.get("channel") == "bottom"]
    log(f"\n输入: {len(all_peptides):,} 条")
    log(f"  Top 通道:    {len(top_peptides):,} 条 (anoxpepred 前 25K)")
    log(f"  Bottom 通道: {len(bottom_peptides):,} 条 (anoxpepred 后 25K)")

    if not top_peptides:
        log("Top 通道无数据, 终止")
        return
    if not bottom_peptides:
        log("Bottom 通道无数据, 只执行 Top 通道")

    # ── 分块 (全部 50K 统一送评, 减少 HTTP 开销) ──
    chunks = []
    for i in range(0, len(all_peptides), MAX_BATCH_SIZE):
        chunk = all_peptides[i:i + MAX_BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])
    log(f"分块: {len(chunks)} 批 (<= {MAX_BATCH_SIZE}/批)")

    # ══════════════════════════════════════════════════════════════════
    # 检查和启动服务 (直接用 stages3 async 接口)
    # ══════════════════════════════════════════════════════════════════
    from main.stages3.docker_utils import start_services, wait_for_services

    log("检查服务: bepipred3")

    # BepiPred3 (GPU service)
    start_services(["gpu"], ["bepipred3"])
    bepi_health = await wait_for_services(["bepipred3"], timeout=180.0)
    bepi_ok = bepi_health.get("bepipred3", {}).get("available", False)
    if not bepi_ok:
        log("BepiPred-3.0 不可用, 终止")
        return
    log("BepiPred-3.0 就绪")

    # TemStaPro (optional GPU service)
    temstapro_available = False
    log("检查服务: temstapro (可选)")
    start_services(["gpu"], ["temstapro"])
    ts_health = await wait_for_services(["temstapro"], timeout=60.0)
    temstapro_available = ts_health.get("temstapro", {}).get("available", False)
    log(f"TemStaPro: {'可用' if temstapro_available else '不可用, 跳过'}")

    # 确定服务列表和权重
    round3_services = list(NEW_SERVICES)
    if temstapro_available:
        round3_services.append(TEMSTAPRO_CFG)
        all_weights = dict(WITH_TEMSTAPRO_WEIGHTS)
    else:
        all_weights = dict(BASE_WEIGHTS)
    all_services_list = list(all_weights.keys())
    log(f"服务: {len(all_services_list)} 个 — {', '.join(all_services_list)}")

    # ══════════════════════════════════════════════════════════════════
    # 第 1 步: TemStaPro 跑全部 50K (快)
    # ══════════════════════════════════════════════════════════════════
    client = ServiceClient(timeout=300.0)
    new_results: dict[str, dict[str, dict]] = {}

    # TemStaPro 始终是第一个 (如果可用)
    first_service = TEMSTAPRO_CFG if temstapro_available else None
    if first_service:
        svc_name, weight, reverse, desc = first_service
        log(f"\n--- {svc_name} ({desc}) — 全部 50K ---")
        t0 = time.time()
        results = await process_service(client, svc_name, chunks)
        elapsed = time.time() - t0
        n_valid = sum(1 for v in results.values() if v["score"] is not None)
        rate = n_valid / elapsed if elapsed > 0 else 0
        log(f"[{svc_name}] {elapsed:.0f}s, {n_valid}/{len(results)} 有效 ({rate:.0f} seq/s)")
        new_results[svc_name] = results

    # ══════════════════════════════════════════════════════════════════
    # 第 2 步: 预筛选 — 每个通道取 TemStaPro 前 30%
    # ══════════════════════════════════════════════════════════════════
    ts_key = "temstapro"
    if temstapro_available and ts_key in new_results:
        ts_scores = new_results[ts_key]
        top_with_ts = [(p, ts_scores.get(p["peptide_id"], {}).get("score", 0) or 0)
                       for p in top_peptides]
        top_with_ts.sort(key=lambda x: x[1], reverse=True)
        top_subset = [p for p, _ in top_with_ts[:max(1, int(len(top_with_ts) * TEMSTAPRO_TOP_PCT))]]
        log(f"  Top 通道 TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}%: {len(top_subset):,}/{len(top_peptides):,}")

        bottom_with_ts = [(p, ts_scores.get(p["peptide_id"], {}).get("score", 0) or 0)
                          for p in bottom_peptides]
        bottom_with_ts.sort(key=lambda x: x[1], reverse=True)
        bottom_subset = [p for p, _ in bottom_with_ts[:max(1, int(len(bottom_with_ts) * TEMSTAPRO_TOP_PCT))]]
        log(f"  Bottom 通道 TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}%: {len(bottom_subset):,}/{len(bottom_peptides):,}")

        bepi_candidate_ids = {p["peptide_id"] for p in top_subset} | {p["peptide_id"] for p in bottom_subset}
        log(f"  BepiPred3 候选总数: {len(bepi_candidate_ids):,}")
    else:
        # TemStaPro 不可用时, 全部送 BepiPred3 (原始逻辑)
        bepi_candidate_ids = {p["peptide_id"] for p in all_peptides}
        top_subset = list(top_peptides)
        bottom_subset = list(bottom_peptides)
        log("  TemStaPro 不可用 → BepiPred3 跑全部 50K")

    # ── 为 BepiPred3 构建子集块 ──
    bepi_candidates = [p for p in all_peptides if p["peptide_id"] in bepi_candidate_ids]
    bepi_chunks = []
    for i in range(0, len(bepi_candidates), MAX_BATCH_SIZE):
        chunk = bepi_candidates[i:i + MAX_BATCH_SIZE]
        bepi_chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])
    log(f"  BepiPred3 分块: {len(bepi_chunks)} 批 (候选 {len(bepi_candidates):,})")

    # ══════════════════════════════════════════════════════════════════
    # 第 3 步: BepiPred3 跑候选子集
    # ══════════════════════════════════════════════════════════════════
    second_service = ("bepipred3", 0.10, True, "B 细胞表位 (反向)")
    svc_name, weight, reverse, desc = second_service
    log(f"\n--- {svc_name} ({desc}) — {len(bepi_candidates):,} 候选 ---")
    t0 = time.time()
    results = await process_service(client, svc_name, bepi_chunks)
    elapsed = time.time() - t0
    n_valid = sum(1 for v in results.values() if v["score"] is not None)
    rate = n_valid / elapsed if elapsed > 0 else 0
    log(f"[{svc_name}] {elapsed:.0f}s, {n_valid}/{len(results)} 有效 ({rate:.0f} seq/s)")
    new_results[svc_name] = results

    await client.close()

    # ── 保存原始返回 ──
    scores_dir = make_dir(STAGE_DIR, "scores")
    for svc_name in new_results:
        write_json(scores_dir / f"{svc_name}_results.json", new_results[svc_name])

    # ══════════════════════════════════════════════════════════════════
    # 重算综合分 (全部 50K)
    # ══════════════════════════════════════════════════════════════════
    log(f"\n重算综合分 ({len(all_services_list)} 服务)...")

    scored_all = []
    for pep in all_peptides:
        pid = pep["peptide_id"]
        row = dict(pep)
        for svc_name in new_results:
            svc_data = new_results[svc_name].get(pid, {})
            row[svc_name] = svc_data.get("score")
        score, missing = compute_r3_score(pep, all_weights, new_results)
        row["r3_weighted_score"] = score
        if missing:
            row["missing_services"] = ";".join(missing)
        row["safety_flag"] = calc_safety_flag(row, SAFETY_THRESHOLDS)
        scored_all.append(row)

    n_valid = sum(1 for p in scored_all if p["r3_weighted_score"] is not None)
    log(f"  完成: {n_valid:,}/{len(scored_all):,} 有效评分")

    # ══════════════════════════════════════════════════════════════════
    # Top 通道: 在有 BepiPred3 评分的候选池中按 7 服务综合分取前 80
    # ══════════════════════════════════════════════════════════════════
    top_candidate_ids = {p["peptide_id"] for p in top_subset}
    scored_top = [p for p in scored_all
                  if p.get("channel") == "top" and p["peptide_id"] in top_candidate_ids]
    scored_top.sort(key=lambda x: (x["r3_weighted_score"] or 0), reverse=True)
    n_top = min(TOP_N, len(scored_top))
    top80 = scored_top[:n_top]

    top_min_score = top80[-1]["r3_weighted_score"] if top80 else None
    log(f"\nTop 通道: {len(scored_top):,} → Top {n_top}")
    if top80:
        log(f"  Top 1:  {top80[0]['peptide_id']}  score={top80[0]['r3_weighted_score']:.4f}")
        log(f"  Top {n_top}: {top80[-1]['peptide_id']}  score={top_min_score:.4f}")

    # ══════════════════════════════════════════════════════════════════
    # Bottom 通道: 在有 BepiPred3 评分的候选池中安全过滤 → 抗氧化最差
    # ══════════════════════════════════════════════════════════════════
    bottom_candidate_ids = {p["peptide_id"] for p in bottom_subset}
    scored_bottom = [p for p in scored_all
                     if p.get("channel") == "bottom" and p["peptide_id"] in bottom_candidate_ids]
    bottom10 = select_bottom_n(scored_bottom, n=BOTTOM_N, score_key="anoxpepred")
    log(f"\nBottom 通道: {len(scored_bottom):,} → Bottom {len(bottom10)}")
    for i, p in enumerate(bottom10, 1):
        log(f"  #{i:2d} {p['peptide_id']:12s} | AnOxPePred={p.get('anoxpepred', 0):.4f}")

    # ══════════════════════════════════════════════════════════════════
    # 输出 CSV
    # ══════════════════════════════════════════════════════════════════
    final_dir = make_dir(STAGE_DIR, "final")

    base_fields = [
        "peptide_id", "sequence", "length", "source", "channel",
        "anoxpepred", "toxinpred3", "algpred2", "hemopi2", "mhcflurry",
    ]
    r3_fields = list(new_results.keys())
    score_fields = ["r3_weighted_score", "weighted_score", "safety_flag"]
    fieldnames = base_fields + r3_fields + score_fields

    all_path = final_dir / "all_scored.csv"
    write_csv(all_path, fieldnames, scored_all)
    log(f"\n全量:      {all_path} ({len(scored_all):,})")

    top_path = final_dir / "top80.csv"
    write_csv(top_path, fieldnames, top80)
    log(f"Top {n_top}:  {top_path}")

    bottom_path = final_dir / "bottom10.csv"
    write_csv(bottom_path, fieldnames, bottom10)
    log(f"Bottom {len(bottom10)}: {bottom_path}")

    danger_list = [p for p in scored_all if "danger" in p.get("safety_flag", "")]
    danger_path = final_dir / "danger_list.csv"
    write_csv(danger_path, fieldnames, danger_list)
    log(f"高危:      {danger_path} ({len(danger_list)} 条)")

    # ══════════════════════════════════════════════════════════════════
    # 跨轮排名轨迹 (Top 80)
    # ══════════════════════════════════════════════════════════════════
    log(f"\n计算跨轮排名轨迹 (Top 80)...")

    # R1 排名: 在 1M 全量中的位置
    r1_rank: dict[str, int] = {}
    r1_path = OUTPUT_DIR / "round01_lightweight" / "final" / "all_scored.csv"
    top80_ids = {p["peptide_id"] for p in top80}
    if r1_path.exists():
        with open(r1_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if row["peptide_id"] in top80_ids:
                    r1_rank[row["peptide_id"]] = i + 1

    # R2 排名: 在 top25K 中的位置 (按 5 服务综合分排序)
    r2_rank: dict[str, int] = {}
    r2_path = OUTPUT_DIR / "round02_scoring" / "final" / "top25k.csv"
    if r2_path.exists():
        with open(r2_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                r2_rank[row["peptide_id"]] = i + 1

    trajectory = []
    for i, p in enumerate(top80):
        pid = p["peptide_id"]
        score = p.get("r3_weighted_score")
        if score is None:
            continue
        trajectory.append({
            "peptide_id": pid,
            "sequence": p["sequence"],
            "rank_r1": r1_rank.get(pid),
            "rank_r2": r2_rank.get(pid),
            "rank_r3": i + 1,
            "score_r3": round(score, 4),
            "channel": "top",
        })

    traj_path = final_dir / "trajectory.csv"
    traj_fields = ["peptide_id", "sequence", "rank_r1", "rank_r2", "rank_r3", "score_r3", "channel"]
    write_csv(traj_path, traj_fields, trajectory)
    log(f"轨迹: {traj_path} ({len(trajectory)} 条)")

    # ══════════════════════════════════════════════════════════════════
    # 统计报告
    # ══════════════════════════════════════════════════════════════════
    total_elapsed = time.time() - start_time

    all_reports = []
    for svc_name in all_services_list + ["r3_weighted_score"]:
        vals = [p[svc_name] for p in scored_all if p.get(svc_name) is not None]
        if vals:
            all_reports.append(describe(svc_name, vals))
    full_distro = "\n".join(all_reports)

    n_safe = sum(1 for p in scored_all if p.get("safety_flag") == "safe")
    n_caution = sum(1 for p in scored_all if "caution" in p.get("safety_flag", ""))
    n_danger = len(danger_list)
    n_missing = sum(1 for p in scored_all if p.get("missing_services"))

    # Top 10
    top10_lines = "\n".join(
        f"| {p['peptide_id']} | {p['sequence'][:25]:25s} | {p['length']} | "
        f"{p['r3_weighted_score']:.4f} | {p.get('safety_flag','safe')} |"
        for p in top80[:10]
    )

    # Bottom 10 详情
    bottom_info_lines = ""
    if bottom10:
        bottom_info_lines = "\n".join(
            f"| {i+1} | {p['peptide_id']} | {p['sequence'][:20]:20s} | "
            f"{p.get('anoxpepred', 0):.4f} | {p.get('toxinpred3', 'N/A')} | "
            f"{p.get('hemopi2', 'N/A')} | {p.get('algpred2', 'N/A')} |"
            for i, p in enumerate(bottom10)
        )

    # 排名上升/下降 Top 10
    traj_with_change = [t for t in trajectory if t["rank_r1"] is not None]
    traj_with_change.sort(key=lambda t: (t.get("rank_r3", 999) or 999) - (t.get("rank_r1", 999) or 999))
    top_rise = traj_with_change[:10][::-1] if traj_with_change else []
    top_fall = traj_with_change[-10:] if len(traj_with_change) >= 10 else traj_with_change

    rise_lines = "\n".join(
        f"| {t['peptide_id']} | R1:{t['rank_r1']} -> R2:{t['rank_r2']} -> R3:{t['rank_r3']} | {t['score_r3']:.4f} |"
        for t in top_rise
    ) if top_rise else "无数据"

    fall_lines = "\n".join(
        f"| {t['peptide_id']} | R1:{t['rank_r1']} -> R2:{t['rank_r2']} -> R3:{t['rank_r3']} | {t['score_r3']:.4f} |"
        for t in reversed(top_fall)
    ) if top_fall else "无数据"

    stats = {
        "stage": STAGE, "timestamp": datetime.now().isoformat(),
        "elapsed_sec": round(total_elapsed, 1),
        "input": len(all_peptides),
        "top_channel": len(top_peptides), "bottom_channel": len(bottom_peptides),
        "services": list(all_weights.keys()), "weights": all_weights,
        "temstapro_used": temstapro_available,
        "temstapro_prefilter_pct": TEMSTAPRO_TOP_PCT,
        "bepipred3_candidates": len(bepi_candidate_ids),
        "top_n": n_top, "bottom_n": len(bottom10),
        "scoring": {
            "n_valid": len([p for p in scored_all if p["r3_weighted_score"] is not None]),
        },
        "safety": {"safe": n_safe, "caution": n_caution, "danger": n_danger, "missing": n_missing},
    }
    write_json(STAGE_DIR / "stats.json", stats)

    # ── README ──
    readme = f"""# Round 3: 重服务评分 — 50K 双通道 (TemStaPro 预筛版)

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {total_elapsed:.0f} 秒
**输入**: {len(all_peptides):,} 条肽 (top25K + bottom25K)
**输出目录**: output2/

## 评分服务 ({len(all_services_list)} 个)

| 服务 | 权重 | 方向 |
|------|------|------|
{"".join(f"| {svc} | {all_weights[svc]:.2f} | {'正向' if svc not in REVERSE_SERVICES else '反向'} |\\n" for svc in all_services_list)}

**TemStaPro**: {'已使用 (预筛+BepiPred3 候选缩减)' if temstapro_available else '未就绪, 跳过'}

## TemStaPro 预筛

为了提高效率, TemStaPro 先跑全部 50K, 然后每个通道取 TemStaPro 得分前 {TEMSTAPRO_TOP_PCT*100:.0f}%:
- Top 通道: {len(top_peptides):,} → TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% → {len(top_subset):,} 送 BepiPred3
- Bottom 通道: {len(bottom_peptides):,} → TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% → {len(bottom_subset):,} 送 BepiPred3
- BepiPred3 总候选: {len(bepi_candidate_ids):,}

## 双通道设计

| 通道 | TemStaPro 预筛来源 | 最终筛选逻辑 | 输出 |
|------|--------------------|-------------|------|
| Top | top25K → TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% ({len(top_subset):,}) | 按 7 服务综合分排序取前 {n_top} | top80.csv |
| Bottom | bottom25K → TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% ({len(bottom_subset):,}) | 安全维度正常 -> anoxpepred 升序取 {len(bottom10)} | bottom10.csv |

Bottom 通道安全阈值:
- ToxinPred3 < 0.60
- AlgPred2 < 0.50
- HemoPI2 < 0.70
- MHCflurry < 0.50
- BepiPred3 < 0.60

## 分数分布

```
{full_distro}
```

## 安全标记

| 级别 | 全部 50K | 占比 |
|------|----------|------|
| Safe | {n_safe:,} | {n_safe/max(len(scored_all),1)*100:.1f}% |
| Caution | {n_caution:,} | {n_caution/max(len(scored_all),1)*100:.1f}% |
| Danger | {n_danger:,} | {n_danger/max(len(scored_all),1)*100:.1f}% |
| Missing | {n_missing:,} | {n_missing/max(len(scored_all),1)*100:.1f}% |

## Top 10 (Top 通道按综合分)

| ID | 序列 | 长度 | 综合分 | 安全 |
|----|------|------|--------|------|
{top10_lines}

## Bottom 10 (安全但抗氧化最差)

| # | ID | 序列 | AnOxPePred | ToxinPred3 | HemoPI2 | AlgPred2 |
|---|-----|------|-----------|------------|---------|----------|
{bottom_info_lines}

## 排名上升 Top 10 (R1 -> R3)

| 肽 | 排名变化 | R3 综合分 |
|----|----------|-----------|
{rise_lines}

## 排名下降 Top 10 (R1 -> R3)

| 肽 | 排名变化 | R3 综合分 |
|----|----------|-----------|
{fall_lines}

## 输出文件

| 文件 | 说明 | 行数 |
|------|------|------|
| final/all_scored.csv | 全部 50K 评分明细 (BepiPred3 仅 {len(bepi_candidate_ids):,} 有值) | {len(scored_all)} |
| final/top80.csv | Top 通道前 {n_top} (来自 TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% 候选池) | {len(top80)} |
| final/bottom10.csv | Bottom 通道 {len(bottom10)} 条阴性对照 (来自 TemStaPro 前 {TEMSTAPRO_TOP_PCT*100:.0f}% 候选池) | {len(bottom10)} |
| final/danger_list.csv | 高危肽清单 | {len(danger_list)} |
| final/trajectory.csv | 跨轮排名轨迹 (R1->R2->R3) | {len(trajectory)} |
"""

    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"\n报告: {readme_path}")

    # ── 汇总 ──
    log(f"\n{'='*60}")
    log(f"Round 3 汇总")
    log(f"  输入: {len(all_peptides):,} 条 (top={len(top_peptides):,}, bottom={len(bottom_peptides):,})")
    if temstapro_available:
        log(f"  TemStaPro 预筛: 每个通道前 {TEMSTAPRO_TOP_PCT*100:.0f}% → BepiPred3 候选 {len(bepi_candidate_ids):,}")
    log(f"  Top: {n_top:,} | Bottom: {len(bottom10)}")
    log(f"  服务: {', '.join(all_services_list)}")
    log(f"  安全: {n_safe:,} safe / {n_caution:,} caution / {n_danger:,} danger")
    if temstapro_available:
        log(f"  TemStaPro: 已使用")
    log(f"  耗时: {total_elapsed:.0f}s")
    log(f"{'='*60}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
