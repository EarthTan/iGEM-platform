"""
Round 2 恢复脚本：仅重新运行 ToxinPred3

ToxinPred3 服务在第一轮 Round 2 执行途中崩溃，导致全部 100K 评分缺失。
此脚本仅运行 ToxinPred3，然后合并已有的 HemoPI2 + MHCflurry 分数，
重新计算综合分并输出。

用法：
    uv run python -m main.stages2.round02_recover_toxinpred3

输入：
    output/round01_lightweight/final/top100k.csv         ← 原始 Round 1 数据
    output/round02_scoring/scores/hemopi2_results.json   ← 已有
    output/round02_scoring/scores/mhcflurry_results.json ← 已有

输出：
    覆盖 output/round02_scoring/final/ 下的文件
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE = "round02_scoring"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient

LOG_FILE: Path | None = None
MAX_BATCH_SIZE = 1000
CONCURRENT_CHUNKS = 2  # ToxinPred3 sklearn 单线程，高并发导致 socket 堆积

# ── 完整 5 服务权重 ──
ALL_WEIGHTS = {
    "anoxpepred":  0.50,
    "toxinpred3":  0.15,
    "algpred2":    0.10,
    "hemopi2":     0.10,
    "mhcflurry":   0.05,
}

SAFETY_THRESHOLDS = {
    "toxinpred3": {"caution": 0.60, "danger": 0.80},
    "algpred2":   {"caution": 0.50, "danger": 0.70},
    "hemopi2":    {"caution": 0.70, "danger": 0.85},
}

TOP_N = 10000
REVERSE_SERVICES = {"toxinpred3", "algpred2", "hemopi2", "mhcflurry"}
ROUND1_INPUT = OUTPUT_DIR / "round01_lightweight" / "final" / "top100k.csv"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def write_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_and_exit(msg: str):
    log(msg)
    sys.exit(1)


async def run():
    global LOG_FILE
    start_time = time.time()
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = STAGE_DIR / "run.log"

    log("=" * 60)
    log("Round 2 恢复：仅重新运行 ToxinPred3")
    log("=" * 60)

    # 加载 Round 1 Top 100K
    if not ROUND1_INPUT.exists():
        log_and_exit(f"输入不存在: {ROUND1_INPUT}")
    peptides: list[dict] = []
    with open(ROUND1_INPUT, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["anoxpepred"] = float(row["anoxpepred"]) if row.get("anoxpepred") else None
            row["algpred2"] = float(row["algpred2"]) if row.get("algpred2") else None
            row["length"] = int(row["length"])
            peptides.append(row)
    total = len(peptides)
    log(f"输入: {total:,} 条 (Round 1 Top 100K)")

    # 分块
    chunks: list[list[dict]] = []
    for i in range(0, total, MAX_BATCH_SIZE):
        chunk = peptides[i:i + MAX_BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])

    # 仅运行 ToxinPred3
    client = ServiceClient(timeout=300.0)
    sem = asyncio.Semaphore(CONCURRENT_CHUNKS)
    all_results: dict[str, dict] = {}
    errors = 0

    log(f"\n重新运行 ToxinPred3 ({len(chunks)} 批)...")

    async def process_chunk(chunk: list[dict]) -> None:
        nonlocal errors
        async with sem:
            result = await client.predict_batch("toxinpred3", chunk)
            if result.get("success") and result.get("results"):
                for r in result["results"]:
                    pid = r.get("peptide_id", "unknown")
                    all_results[pid] = {"score": r.get("score"), "label": r.get("label", "")}
            else:
                errors += 1
                for item in chunk:
                    pid = item.get("peptide_id", "unknown")
                    all_results[pid] = {"score": None, "label": "SERVICE_ERROR"}

    tasks = [process_chunk(chunk) for chunk in chunks]
    report_every = 50
    for i in range(0, len(tasks), report_every):
        batch = tasks[i:i + report_every]
        await asyncio.gather(*batch)
        progress = min((i + report_every) * MAX_BATCH_SIZE, total)
        elapsed = time.time() - start_time
        rate = progress / elapsed if elapsed > 0 else 0
        remain = (total - progress) / rate if rate > 0 else 0
        log(f"  progress: {progress:,}/{total:,} ({progress/total*100:.0f}%) | errors={errors} | "
            f"{rate:.0f} seq/s | ETA {remain/60:.0f}min")

    await client.close()

    n_valid = sum(1 for v in all_results.values() if v["score"] is not None)
    log(f"  ✅ ToxinPred3: {total:,} 完成, {errors} 批次错误, {n_valid} 有效")

    # 加载已有 HemoPI2 和 MHCflurry 结果
    hemopi2_results = {}
    mhcflurry_results = {}
    for svc_name, target in [("hemopi2", hemopi2_results), ("mhcflurry", mhcflurry_results)]:
        p = STAGE_DIR / "scores" / f"{svc_name}_results.json"
        if p.exists():
            with open(p) as f:
                target.update(json.load(f))
            log(f"已加载 {svc_name}: {len(target)} 条")

    # 保存 ToxinPred3 结果
    scores_dir = STAGE_DIR / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    write_json(scores_dir / "toxinpred3_results.json", all_results)

    # 合并所有 5 服务分数，重算综合分
    log(f"\n合并分数 & 重算综合分...")
    scored_peptides: list[dict] = []
    for pep in peptides:
        pid = pep["peptide_id"]
        row = {"peptide_id": pid, "sequence": pep["sequence"],
               "length": pep["length"], "source": pep["source"],
               "anoxpepred": pep.get("anoxpepred"), "algpred2": pep.get("algpred2"),
               "toxinpred3": None, "hemopi2": None, "mhcflurry": None}

        # 从已有结果中填充分数
        for svc_name, result_dict in [("toxinpred3", all_results),
                                       ("hemopi2", hemopi2_results),
                                       ("mhcflurry", mhcflurry_results)]:
            svc_data = result_dict.get(pid, {})
            row[svc_name] = svc_data.get("score")

        weighted_sum = 0.0
        total_weight = 0.0
        missing_svc = []

        for svc_name, weight in ALL_WEIGHTS.items():
            raw_score = row.get(svc_name)
            if raw_score is None:
                missing_svc.append(svc_name)
                continue
            normalized = max(0.0, min(1.0, raw_score))
            if svc_name in REVERSE_SERVICES:
                normalized = 1.0 - normalized
            weighted_sum += normalized * weight
            total_weight += weight

        if missing_svc:
            row["missing_services"] = ";".join(missing_svc)

        row["weighted_score"] = round(weighted_sum / total_weight, 4) if total_weight > 0 else None

        # 安全标记
        flags = []
        for svc_name, cfg in SAFETY_THRESHOLDS.items():
            score = row.get(svc_name)
            if score is None:
                continue
            if score >= cfg["danger"]:
                flags.append(f"{svc_name}:danger({score:.3f})")
            elif score >= cfg["caution"]:
                flags.append(f"{svc_name}:caution({score:.3f})")
        row["safety_flag"] = ";".join(flags) if flags else "safe"

        scored_peptides.append(row)

    n_valid = sum(1 for p in scored_peptides if p["weighted_score"] is not None)
    log(f"完成: {n_valid:,}/{len(scored_peptides):,} 有效评分")

    # 排序 + 输出
    log(f"排序...")
    scored_peptides.sort(key=lambda x: (x["weighted_score"] or 0), reverse=True)

    fieldnames = ["peptide_id", "sequence", "length", "source",
                  "anoxpepred", "toxinpred3", "algpred2", "hemopi2", "mhcflurry",
                  "weighted_score", "safety_flag"]

    final_dir = STAGE_DIR / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    # 全部
    with open(final_dir / "all_scored.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(scored_peptides)

    # Top 10K
    n_top = min(TOP_N, len(scored_peptides))
    with open(final_dir / "top10k.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(scored_peptides[:n_top])
    log(f"Top {n_top:,}: {final_dir / 'top10k.csv'}")

    # 高危
    danger_list = [p for p in scored_peptides if "danger" in p.get("safety_flag", "")]
    with open(final_dir / "danger_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(danger_list)
    log(f"高危清单: {final_dir / 'danger_list.csv'} ({len(danger_list)} 条)")

    # 汇总
    total_elapsed = time.time() - start_time
    valid_scores = [p["weighted_score"] for p in scored_peptides if p["weighted_score"] is not None]
    n_safe = sum(1 for p in scored_peptides if p.get("safety_flag") == "safe")
    n_caution = sum(1 for p in scored_peptides if "caution" in p.get("safety_flag", ""))
    n_danger = len(danger_list)

    log(f"\n{'='*60}")
    log(f"Round 2 恢复完成")
    log(f"  ToxinPred3: {n_valid:,} 有效（之前全失败）")
    log(f"  Top: {n_top:,}")
    if valid_scores:
        log(f"  综合分: mean={sum(valid_scores)/len(valid_scores):.4f}, max={max(valid_scores):.4f}")
    log(f"  安全: {n_safe:,} / {n_caution:,} / {n_danger:,}")
    log(f"  耗时: {total_elapsed:.0f}s")
    log(f"{'='*60}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
