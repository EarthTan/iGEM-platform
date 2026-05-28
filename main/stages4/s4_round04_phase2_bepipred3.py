"""
Round 4 Phase 2: BepiPred3 补充评分。

读取 Phase 1 过滤后的 constructs（round4_phase1_passed），
运行 BepiPred3（GPU, ~0.4 seq/s on 400aa），
写入 construct_scores.bepipred3_score。

用法:
    uv run python -m main.stages4.s4_round04_phase2_bepipred3
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
from main.stages4.s4_docker_utils import check_docker_daemon, start_services, wait_for_services

BATCH_SIZE = 50
CONCURRENCY = 2
TIMEOUT = 600.0


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def run():
    start_time = time.time()
    db = PipelineDB()
    conn = db.connect()

    # ── 1. 获取 Phase 1 通过的 constructs ──
    rows = conn.execute("""
        SELECT pp.construct_id, c.full_sequence
        FROM round4_phase1_passed pp
        JOIN constructs c ON c.construct_id = pp.construct_id
        ORDER BY pp.rank
    """).fetchall()

    total = len(rows)
    log(f"Phase 2 输入: {total:,} constructs")

    if not total:
        log("❌ 无 construct，请先运行 Phase 1")
        return

    # ── 2. 确保 bepipred3 列存在 ──
    for col in ["bepipred3_score FLOAT", "bepipred3_success BOOLEAN"]:
        try:
            conn.execute(f"ALTER TABLE construct_scores ADD COLUMN {col}")
        except Exception:
            pass

    # ── 3. 确保服务就绪（async 原生，不嵌套 asyncio.run）──
    check_docker_daemon()
    start_services(["gpu"], ["bepipred3"])
    health = await wait_for_services(["bepipred3"], timeout=180.0)
    if not health.get("bepipred3", {}).get("available"):
        log("❌ BepiPred3 不可用")
        return
    log("✅ BepiPred3 就绪")

    # ── 4. BepiPred3 评分（渐进写入）──
    # BepiPred3 单 worker GPU 服务，请求排队处理
    client = ServiceClient(timeout=TIMEOUT)
    sem = asyncio.Semaphore(CONCURRENCY)
    chunks = [rows[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

    async def score_one(chunk: list) -> int:
        async with sem:
            items = [
                {"peptide_id": str(r[0]), "sequence": r[1]}
                for r in chunk
            ]
            result = await client.predict_batch("bepipred3", items)
            scores: dict[str, float | None] = {}
            if result.get("success") and result.get("results"):
                for r_item in result["results"]:
                    scores[r_item["peptide_id"]] = r_item.get("score")
            for item in items:
                if item["peptide_id"] not in scores:
                    scores[item["peptide_id"]] = None

        records = []
        for r in chunk:
            cid = int(r[0])
            cid_str = str(cid)
            s = scores.get(cid_str)
            records.append({
                "construct_id": cid,
                "bepipred3_score": s,
                "bepipred3_success": s is not None,
            })
        db.update_construct_bepipred3(records)
        progress[0] += len(records)
        return len(records)

    log(f"BepiPred3 评分开始（每次 {BATCH_SIZE} 条，并发 {CONCURRENCY}）...")
    progress: list[int] = [0]

    async def report():
        while progress[0] < total:
            done = progress[0]
            if done > 0:
                elapsed = time.time() - start_time
                rate = done / elapsed
                eta = (total - done) / rate if rate > 0 else 0
                log(f"  进度: {done:,}/{total:,} ({done/total*100:.1f}%) | "
                    f"{rate:.1f} seq/s | ETA: {eta/60:.1f} min")
            await asyncio.sleep(60)

    monitor = asyncio.create_task(report())

    results = await asyncio.gather(
        *[score_one(c) for c in chunks],
        return_exceptions=True,
    )
    monitor.cancel()

    total_written = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log(f"  ⚠️ chunk {i} 失败: {r}")
        else:
            total_written += r

    await client.close()

    total_elapsed = time.time() - start_time
    db.set_checkpoint("round4", "phase2", "done",
                      total=total, processed=total_written)

    log(f"\n{'='*55}")
    log(f"  Round 4 Phase 2 完成!")
    log(f"  输入:     {total:>10,}")
    log(f"  评分完成: {total_written:>10,}")
    log(f"  总耗时:   {total_elapsed:>8.0f}s ({total_elapsed/60:.1f} min)")
    log(f"{'='*55}")

    db.close()


def main():
    log("Round 4 Phase 2: BepiPred3 补充评分")
    asyncio.run(run())


if __name__ == "__main__":
    main()
