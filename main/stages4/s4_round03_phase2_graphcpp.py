"""
Round 3 Phase 2: 在 Round 4 候选上补跑 GraphCPP。

从 round3_ranking 取 Top N，运行 GraphCPP 评分后写回 round3_scores，
然后重算含 GraphCPP 的加权排名。

用法:
    uv run python -m main.stages4.s4_round03_phase2_graphcpp
    uv run python -m main.stages4.s4_round03_phase2_graphcpp --limit 4324
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
from main.stages4.s4_analytics import compute_variance_weights, apply_weights_and_rank

BATCH_SIZE = 200
CONCURRENCY = 2


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def run(limit: int = 4324, top_pct: float = 5.0):
    start_time = time.time()
    db = PipelineDB()
    conn = db.connect()

    # ── 1. 获取待评分的候选 ──
    # 从 round3_ranking 按 composite_score 降序取 top
    candidates = conn.execute("""
        SELECT r.candidate_id, c.sequence FROM round3_ranking r
        JOIN candidates c ON c.candidate_id = r.candidate_id
        ORDER BY r.composite_score DESC
        LIMIT ?
    """, [limit]).fetchall()

    total = len(candidates)
    log(f"Phase 2 输入: {total:,} 条")

    if not total:
        log("❌ 无候选")
        return

    # ── 2. 检查 graphcpp 列是否存在 ──
    try:
        conn.execute("ALTER TABLE round3_scores ADD COLUMN graphcpp_score FLOAT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE round3_scores ADD COLUMN graphcpp_success BOOLEAN")
    except Exception:
        pass

    # ── 3. GraphCPP 评分（渐进写入）──
    client = ServiceClient(timeout=1800.0)
    sem = asyncio.Semaphore(CONCURRENCY)
    chunks = [candidates[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

    async def score_one(chunk: list) -> int:
        async with sem:
            items = [
                {"peptide_id": str(r[0]), "sequence": r[1]}
                for r in chunk
            ]
            result = await client.predict_batch("graphcpp", items)
            scores: dict[str, float | None] = {}
            if result.get("success") and result.get("results"):
                for r_item in result["results"]:
                    scores[r_item["peptide_id"]] = r_item.get("score")
            for item in items:
                if item["peptide_id"] not in scores:
                    scores[item["peptide_id"]] = None

        # 立即写回 round3_scores
        records = []
        for r in chunk:
            cid = int(r[0])
            cid_str = str(cid)
            score = scores.get(cid_str)
            records.append({
                "candidate_id": cid,
                "graphcpp_score": score,
                "graphcpp_success": score is not None,
            })
        db.update_round3_graphcpp(records)
        progress[0] += len(records)
        return len(records)

    log(f"GraphCPP 评分开始（每次 {BATCH_SIZE} 条，并发 {CONCURRENCY}）...")
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
            await asyncio.sleep(30)

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
    log(f"GraphCPP 评分完成: {total_written:,}/{total:,} 条 ✅")

    # ── 4. 重算加权排名（含 GraphCPP）──
    log("\n重算含 GraphCPP 的方差权重...")
    score_columns = ["bepipred3_score", "temstapro_score", "sodope_score",
                     "plm4cpps_score", "graphcpp_score"]

    weights_result = compute_variance_weights(
        db, table="round3_scores",
        score_columns=score_columns,
        stage_name="round3",
        manual_coefficients={"bepipred3_score": 1.0, "temstapro_score": 1.0,
                             "sodope_score": 1.0, "plm4cpps_score": 1.0,
                             "graphcpp_score": 1.0, "_reason": "Phase 2: +GraphCPP"},
    )

    log("重算排名...")
    ranking = apply_weights_and_rank(
        db, table="round3_scores",
        score_columns=score_columns,
        weights=weights_result["final_weights"],
        stage_name="round3",
        rank_table="round3_ranking",
    )

    top_n = max(1, int(len(ranking) * top_pct / 100))
    total_elapsed = time.time() - start_time
    db.set_checkpoint("round3", "phase2", "done",
                      total=total, processed=total)

    log(f"\n{'='*55}")
    log(f"  Phase 2 完成!")
    log(f"  GraphCPP 评分: {total_written:,}")
    log(f"  排名:          {len(ranking):,}")
    log(f"  → Round 4:    {top_n:,}")
    log(f"  总耗时:        {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    log(f"{'='*55}")

    db.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round 3 Phase 2: GraphCPP 补充评分")
    parser.add_argument("--limit", type=int, default=4324,
                        help="取排名前 N 条 (default: 4324)")
    parser.add_argument("--top-pct", type=float, default=5.0,
                        help="重新排名后取前百分之几")
    args = parser.parse_args()

    log(f"Round 3 Phase 2: GraphCPP 补充评分")
    log(f"输入: Top {args.limit:,} of round3_ranking")

    asyncio.run(run(limit=args.limit, top_pct=args.top_pct))


if __name__ == "__main__":
    main()
