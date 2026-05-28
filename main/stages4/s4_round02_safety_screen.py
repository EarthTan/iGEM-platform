"""
Round 2: 安全筛检 — 纯硬阈值，无加权。

对 Top + Bottom 通道的候选运行三个安全服务:
  - ToxinPred3: 毒性预测 (CPU, ≥0.38 淘汰)
  - HemoPI2: 溶血性预测 (CPU, ≥0.55 淘汰)
  - MHCflurry: MHC-I 结合预测 (GPU, ≥0.5 淘汰)

每个安全属性独立淘汰。未通过任意安全项即淘汰，不参与后续轮次。

用法:
    uv run python -m main.stages4.s4_round02_safety_screen
    uv run python -m main.stages4.s4_round02_safety_screen \
        --toxin-threshold 0.38 --hemo-threshold 0.55 --mhc-threshold 0.5
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient
from main.stages4.s4_db import PipelineDB
from main.stages4.s4_docker_utils import ensure_services
from main.stages4.s4_service_map import get_round_services

# ── 默认阈值 ──
TOXIN_THRESHOLD = 0.38   # ToxinPred3 ≥ 0.38 → 有毒
HEMO_THRESHOLD = 0.55    # HemoPI2 ≥ 0.55 → 溶血
MHC_THRESHOLD = 0.5      # MHCflurry ≥ 0.5 → 强免疫原性

# ── ToxinPred3 多实例（跨进程安全，每个容器独立 2 并发） ──
TOXIN_INSTANCES = ["toxinpred3", "toxinpred3-2", "toxinpred3-3"]


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)



async def run(toxin_threshold: float, hemo_threshold: float, mhc_threshold: float):
    start_time = time.time()

    # ── 1. 连接数据库 ──
    db = PipelineDB()
    conn = db.connect()
    db.init_schema()

    # ── 2. 获取待处理的候选（Top + Bottom 通道） ──
    top_candidates = db.get_channel_candidates("top")
    bottom_candidates = db.get_channel_candidates("bottom")
    all_candidates = top_candidates + bottom_candidates

    log(f"Top 通道: {len(top_candidates):,} 条")
    log(f"Bottom 通道: {len(bottom_candidates):,} 条")
    log(f"待筛检总计: {len(all_candidates):,} 条")

    # 检查是否已有评分
    last_id = db.get_last_processed_id("round2_scores")
    if last_id > 0:
        log(f"已评分 (round2_scores): {last_id:,} 条")

    # ── 3. 并发评分三个安全服务 ──
    client = ServiceClient(timeout=180.0)
    sem_hemo = asyncio.Semaphore(10)   # HemoPI2 轻量，高并发
    sem_mhc = asyncio.Semaphore(10)    # MHCflurry 可高并发

    log("\n开始安全服务评分（HemoPI2/MHCflurry → 立即写入，ToxinPred3 后续再打）...")

    hemo_chunks = [all_candidates[i:i + 100] for i in range(0, len(all_candidates), 100)]
    mhc_chunks = [all_candidates[i:i + 100] for i in range(0, len(all_candidates), 100)]

    async def score_hemo(chunk: list[dict]) -> dict[int, float | None]:
        async with sem_hemo:
            items = [{"peptide_id": str(c["candidate_id"]), "sequence": c["sequence"]} for c in chunk]
            result = await client.predict_batch("hemopi2", items)
            if result.get("success") and result.get("results"):
                return {int(r["peptide_id"]): r.get("score") for r in result["results"]}
            return {c["candidate_id"]: None for c in chunk}

    async def score_mhc(chunk: list[dict]) -> dict[int, float | None]:
        async with sem_mhc:
            items = [{"peptide_id": str(c["candidate_id"]), "sequence": c["sequence"]} for c in chunk]
            result = await client.predict_batch("mhcflurry", items)
            if result.get("success") and result.get("results"):
                return {int(r["peptide_id"]): r.get("score") for r in result["results"]}
            return {c["candidate_id"]: None for c in chunk}

    # HemoPI2/MHCflurry 轻量快速，优先出结果
    hemo_results, mhc_results = await asyncio.gather(
        asyncio.gather(*[score_hemo(c) for c in hemo_chunks], return_exceptions=True),
        asyncio.gather(*[score_mhc(c) for c in mhc_chunks], return_exceptions=True),
    )

    hemo_map: dict[int, float | None] = {}
    for r in hemo_results:
        if isinstance(r, dict):
            hemo_map.update(r)
    mhc_map: dict[int, float | None] = {}
    for r in mhc_results:
        if isinstance(r, dict):
            mhc_map.update(r)
    log(f"  HemoPI2: {len(hemo_map):,} 条 ✅")
    log(f"  MHCflurry: {len(mhc_map):,} 条 ✅")

    # ── HemoPI2/MHCflurry 先写入 DB（毒性的等 ToxinPred3 慢慢补） ──
    # 关键: 先持久化快数据。即使后续崩溃，至少 hemo/mhc 不丢。
    hemo_mhc_records = [
        {
            "candidate_id": c["candidate_id"],
            "toxinpred3_score": None,
            "toxinpred3_success": False,
            "hemopi2_score": hemo_map.get(c["candidate_id"]),
            "hemopi2_success": hemo_map.get(c["candidate_id"]) is not None,
            "mhcflurry_score": mhc_map.get(c["candidate_id"]),
            "mhcflurry_success": mhc_map.get(c["candidate_id"]) is not None,
        }
        for c in all_candidates
    ]
    db.insert_round2_scores(hemo_mhc_records)
    log(f"  HemoPI2/MHCflurry 已写入 DB ✅")

    # ── ToxinPred3 多实例并行，每批打分→立即写入 DB ──
    # 关键设计: 永不全部收集在内存。每 200 条写一次 ON CONFLICT UPDATE。
    # 任意时刻崩溃最多丢失 200 条（当前 mini-batch）。
    TOXIN_PER_INSTANCE_CONCURRENCY = 2
    TOXIN_MINI_BATCH = 200
    n_instances = len(TOXIN_INSTANCES)
    total_t = len(all_candidates)
    per_instance = total_t // n_instances
    instance_batches = []
    for idx, inst in enumerate(TOXIN_INSTANCES):
        start = idx * per_instance
        end = start + per_instance if idx < n_instances - 1 else total_t
        instance_batches.append((inst, all_candidates[start:end]))

    async def run_instance(inst: str, batch: list[dict],
                           progress: dict[str, int]) -> None:
        """单个 ToxinPred3 实例：每批打分后立即写入 DB。"""
        sem = asyncio.Semaphore(TOXIN_PER_INSTANCE_CONCURRENCY)

        async def score_one(c: dict) -> tuple[int, float | None]:
            async with sem:
                result = await client.predict_single(inst, c["sequence"])
                return (c["candidate_id"],
                        result.get("score") if result.get("success") else None)

        for i in range(0, len(batch), TOXIN_MINI_BATCH):
            mini = batch[i:i + TOXIN_MINI_BATCH]
            scored = await asyncio.gather(
                *[score_one(c) for c in mini], return_exceptions=True
            )

            # 立即写入 — ON CONFLICT DO UPDATE 保证幂等
            records = []
            for c, s in zip(mini, scored):
                if isinstance(s, Exception):
                    continue  # 留 NULL，不阻塞流程
                cid, score = s
                records.append({
                    "candidate_id": cid,
                    "toxinpred3_score": score,
                    "toxinpred3_success": score is not None,
                    "hemopi2_score": hemo_map.get(cid),
                    "hemopi2_success": hemo_map.get(cid) is not None,
                    "mhcflurry_score": mhc_map.get(cid),
                    "mhcflurry_success": mhc_map.get(cid) is not None,
                })
                progress["done"] += 1

            if records:
                db.insert_round2_scores(records)

    async def run_toxin_scoring() -> None:
        """启动多实例并行打分（已在 DB 中有初始 hemo/mhc 行）。"""
        progress = {"done": 0}
        tasks = [run_instance(inst, batch, progress)
                 for inst, batch in instance_batches]

        async def report_progress():
            while True:
                done = progress["done"]
                if done >= total_t:
                    break
                pct = done / total_t * 100
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                log(f"  ToxinPred3: {done:,}/{total_t:,} ({pct:.1f}%) | "
                    f"{rate:.0f} seq/s | 耗时: {elapsed:.0f}s")
                await asyncio.sleep(30)

        monitor = asyncio.create_task(report_progress())
        await asyncio.gather(*tasks, return_exceptions=True)
        monitor.cancel()
        log(f"  ToxinPred3: {progress['done']:,}/{total_t:,} 条 ✅")

    log(f"\n  ToxinPred3 开始多实例并行（每 {TOXIN_MINI_BATCH} 条写一次 DB）...")
    await run_toxin_scoring()

    await client.close()

    # ── 5. 应用安全硬阈值 ──
    log(f"\n应用安全硬阈值...")
    log(f"  ToxinPred3 ≥ {toxin_threshold} → 淘汰")
    log(f"  HemoPI2 ≥ {hemo_threshold} → 淘汰")
    log(f"  MHCflurry ≥ {mhc_threshold} → 淘汰")

    result = db.apply_safety_thresholds(
        toxin_threshold=toxin_threshold,
        hemo_threshold=hemo_threshold,
        mhc_threshold=mhc_threshold,
    )

    log(f"\n  安全筛检结果:")
    log(f"    ToxinPred3 淘汰: {result['details']['toxin']:,}")
    log(f"    HemoPI2 淘汰:    {result['details']['hemo']:,}")
    log(f"    MHCflurry 淘汰:  {result['details']['mhc']:,}")
    log(f"    合计淘汰:        {result['excluded']:,}")
    log(f"    安全通过:        {result['passed']:,}")

    total_elapsed = time.time() - start_time
    db.set_checkpoint("round2", "safety", "done",
                      total=len(all_candidates), processed=len(all_candidates))

    log(f"\n{'='*55}")
    log(f"  Round 2 完成!")
    log(f"  输入:          {len(all_candidates):>10,}")
    log(f"  安全通过:      {result['passed']:>10,}")
    log(f"  淘汰:          {result['excluded']:>10,}")
    log(f"  总耗时:        {total_elapsed:>8.0f}s")
    log(f"{'='*55}")

    db.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round 2: 安全筛检（硬阈值）")
    parser.add_argument("--toxin-threshold", type=float, default=TOXIN_THRESHOLD)
    parser.add_argument("--hemo-threshold", type=float, default=HEMO_THRESHOLD)
    parser.add_argument("--mhc-threshold", type=float, default=MHC_THRESHOLD)
    args = parser.parse_args()

    info = get_round_services("round2")
    all_services = info["services"] + [s for s in TOXIN_INSTANCES if s not in info["services"]]
    log(f"Round 2: {info['desc']}")
    log(f"依赖服务: {', '.join(all_services)} (ToxinPred3 × {len(TOXIN_INSTANCES)} 实例)")

    health = ensure_services(all_services, info["profiles"], timeout=180.0)
    unavailable = [s for s, h in health.items() if not h["available"]]
    if unavailable:
        log(f"❌ 服务不可用，终止: {unavailable}")
        sys.exit(1)
    log("✅ 所有服务就绪\n")

    asyncio.run(run(args.toxin_threshold, args.hemo_threshold, args.mhc_threshold))


if __name__ == "__main__":
    main()
