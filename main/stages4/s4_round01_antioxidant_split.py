"""
Round 1: 抗氧化单指标分选。

抗氧化分数在此轮次进行唯一一次使用：
1. 对所有 19.9M 候选运行 AnOxPePred（抗氧化）+ AlgPred2（致敏性硬阈值）
2. 按纯 AnOxPePred 排序
3. 分双通道：Top X%（抗氧化最好） + Bottom Y%（抗氧化最差，阴性对照）

用法:
    uv run python -m main.stages4.s4_round01_antioxidant_split
    uv run python -m main.stages4.s4_round01_antioxidant_split --top-pct 10 --bottom-pct 1
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

# ── 配置 ──
DB_BATCH = 100_000           # 每次从 DB 读取的行数
HTTP_BATCH = 1_000           # 每次 HTTP 请求发送的序列数
CONCURRENCY = 10             # 每个服务的并发 HTTP 请求数
PROGRESS_INTERVAL = 1        # 每轮打印一次进度
ALGPRED2_THRESHOLD = 0.30    # AlgPred2 硬阈值（≥此值淘汰）


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def run(top_pct: float, bottom_pct: float, sample: int = 0):
    start_time = time.time()

    # ── 1. 连接数据库 ──
    db = PipelineDB()
    conn = db.connect()
    db.init_schema()

    total = db.row_count("candidates")
    if sample and sample < total:
        total = sample
        log(f"🧪 采样模式: {sample:,} 条")
    last_id = db.get_last_processed_id("round1_scores")

    log(f"候选池: {total:,} 条")
    log(f"已评分 (round1_scores): {last_id:,} 条")

    if last_id >= total:
        log("✅ Round 1 评分已完成，跳过评分阶段")
    else:
        log(f"待评分: {total - last_id:,} 条\n")

        client = ServiceClient(timeout=120.0)
        sem = asyncio.Semaphore(CONCURRENCY)

        processed = last_id
        round_num = 0
        total_errors = 0

        while processed < total:
            batch_end = min(processed + DB_BATCH, total)
            rows = conn.execute("""
                SELECT candidate_id, sequence
                FROM candidates
                WHERE candidate_id > ? AND candidate_id <= ?
                ORDER BY candidate_id
            """, [processed, batch_end]).fetchall()

            if not rows:
                break

            candidate_ids = [r[0] for r in rows]
            batch_items = [
                {"peptide_id": str(cid), "sequence": r[1]}
                for cid, r in zip(candidate_ids, rows)
            ]

            chunks = [batch_items[i:i + HTTP_BATCH] for i in range(0, len(batch_items), HTTP_BATCH)]

            async def score_one(chunk: list[dict], svc: str) -> list[dict]:
                async with sem:
                    result = await client.predict_batch(svc, chunk)
                    if result.get("success") and result.get("results"):
                        return result["results"]
                    return [{"peptide_id": item["peptide_id"], "score": None} for item in chunk]

            async def score_service(svc: str) -> dict[str, float]:
                tasks = [score_one(chunk, svc) for chunk in chunks]
                results = await asyncio.gather(*tasks)
                flat: dict[str, float] = {}
                for batch_res in results:
                    for r in batch_res:
                        flat[r["peptide_id"]] = r.get("score")
                return flat

            anox_map, alg_map = await asyncio.gather(
                score_service("anoxpepred"),
                score_service("algpred2"),
            )

            score_records = [
                {
                    "candidate_id": cid,
                    "anoxpepred_score": anox_map.get(str(cid)),
                    "anoxpepred_success": anox_map.get(str(cid)) is not None,
                    "algpred2_score": alg_map.get(str(cid)),
                    "algpred2_success": alg_map.get(str(cid)) is not None,
                }
                for cid in candidate_ids
            ]
            db.insert_round1_scores(score_records)

            processed += len(rows)
            round_num += 1
            round_errors = sum(
                1 for r in score_records
                if r["anoxpepred_score"] is None or r["algpred2_score"] is None
            )
            total_errors += round_errors

            db.set_checkpoint("round1", "scoring", "running",
                              total=total, processed=processed)

            if round_num % PROGRESS_INTERVAL == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                pct = processed / total * 100
                log(f"进度: {processed:,}/{total:,} ({pct:.1f}%) | "
                    f"{rate:.0f} seq/s | 错误: {total_errors:,} | "
                    f"耗时: {elapsed:.0f}s")

        await client.close()
        log(f"\n评分完成: {processed:,} 条, 错误: {total_errors:,}")

    # ── 双通道分选 ──
    log("\n执行双通道分选...")
    result = db.assign_channels(top_pct=top_pct, bottom_pct=bottom_pct)
    log(f"  Top 通道: {result['top']:,} 条 (AnOxPePred {result['top_anoxpepred_range']})")
    log(f"  Bottom 通道: {result['bottom']:,} 条 (AnOxPePred {result['bottom_anoxpepred_range']})")
    log(f"  AlgPred2 ≥ {ALGPRED2_THRESHOLD} 淘汰: {result['excluded_algpred2']:,}")

    total_elapsed = time.time() - start_time
    db.set_checkpoint("round1", "split", "done",
                      total=total, processed=total)

    log(f"\n{'='*55}")
    log(f"  Round 1 完成!")
    log(f"  候选池:        {total:>10,}")
    log(f"  Top 通道:      {result['top']:>10,}")
    log(f"  Bottom 通道:   {result['bottom']:>10,}")
    log(f"  淘汰 (AlgPred2): {result['excluded_algpred2']:>10,}")
    log(f"  总耗时:        {total_elapsed:>8.0f}s ({total_elapsed/60:.1f} min)")
    log(f"{'='*55}")

    db.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round 1: 抗氧化单指标分选")
    parser.add_argument("--top-pct", type=float, default=10.0,
                        help="Top 通道取前百分之几 (default: 10.0)")
    parser.add_argument("--bottom-pct", type=float, default=1.0,
                        help="Bottom 通道取后百分之几 (default: 1.0)")
    parser.add_argument("--sample", type=int, default=0,
                        help="采样模式：只处理前 N 条 (default: 全量)")
    args = parser.parse_args()

    info = get_round_services("round1")
    log(f"Round 1: {info['desc']}")
    log(f"依赖服务: {', '.join(info['services'])}")

    health = ensure_services(info["services"], info["profiles"], timeout=180.0)
    unavailable = [s for s, h in health.items() if not h["available"]]
    if unavailable:
        log(f"❌ 服务不可用，终止: {unavailable}")
        sys.exit(1)
    log("✅ 所有服务就绪\n")

    asyncio.run(run(args.top_pct, args.bottom_pct, sample=args.sample))


if __name__ == "__main__":
    main()
