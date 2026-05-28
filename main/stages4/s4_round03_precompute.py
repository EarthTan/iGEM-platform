"""
Round 3 预打分脚本 — 利用 Round 2 运行中的 GPU 空闲提前为 deep scoring 打分。

无需等 Round 2 完成，直接从 round1_channels 读取候选列表，
逐服务顺序打分（避免 GPU 显存竞争），写入 round3_scores 表。

写入幂等：insert_round3_scores() 使用 ON CONFLICT DO UPDATE，
多轮写入不同服务的分数不会互相覆盖。

重要: 预打分写入独立的 precompute.db（非 pipeline.db），
避免与 Round 2 的 DuckDB 写锁冲突。Round 2 完成后需要运行
merge_precompute.py 将分数合并回 pipeline.db。

用法:
    # 全量预打分（默认 5 个服务）
    uv run python -m main.stages4.s4_round03_precompute

    # 指定服务子集（断点续跑或部分重打）
    uv run python -m main.stages4.s4_round03_precompute \\
        --services sodope,plm4cpps

    # 恢复模式：跳过已完成的服务
    uv run python -m main.stages4.s4_round03_precompute \\
        --services sodope,bepipred3,temstapro --resume

    # 查看 checkpoint 状态
    uv run python -m main.stages4.s4_round03_precompute --status

合并回 pipeline.db:
    uv run python -m main.stages4.s4_round03_precompute --merge
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
from main.client import ServiceClient
from main.stages4.s4_db import PipelineDB, DEFAULT_DB_PATH

# ── 服务顺序（CPU 先出结果，GPU 由轻到重避免显存竞争） ──
SERVICE_ORDER = ["sodope", "plm4cpps", "graphcpp", "bepipred3", "temstapro"]

# ── 参数（与 s4_round03_deep_scoring.py 保持一致） ──
BATCH_SIZE = 1_000
CONCURRENCY = 5
TIMEOUT = 300.0
CHECKPOINT_PREFIX = "round3_precompute"
PROGRESS_INTERVAL = 10  # 每 N 批报告一次

# ── 预打分独立数据库 ──
PRECOMPUTE_DB = str(Path(DEFAULT_DB_PATH).with_name("precompute.db"))


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def print_status(pdb_path: str = PRECOMPUTE_DB):
    """打印各服务预打分 checkpoint 状态。"""
    pdb = PipelineDB(pdb_path)
    pdb.connect()
    log("预打分状态:")
    for svc in SERVICE_ORDER:
        cp = pdb.get_checkpoint(f"{CHECKPOINT_PREFIX}_{svc}")
        if cp:
            log(f"  {svc}: {cp['status']} (processed={cp['processed_items']:,})")
        else:
            log(f"  {svc}: 未开始")
    scored = pdb.get_last_processed_id("round3_scores")
    log(f"round3_scores 总计: {scored:,} 条")
    pdb.close()


def merge_scores():
    """将 precompute.db 中的 round3_scores 合并回 pipeline.db。"""
    log(f"合并预打分到 {DEFAULT_DB_PATH}...")
    main_db = PipelineDB()
    main_db.connect()
    main_db.init_schema()

    pre_db = duckdb.connect(PRECOMPUTE_DB, read_only=True)

    # 检查 precompute.db 是否有数据
    count = pre_db.execute("SELECT COUNT(*) FROM round3_scores").fetchone()[0]
    if count == 0:
        log("precompute.db 无数据，跳过合并")
        pre_db.close()
        main_db.close()
        return

    log(f"precompute.db 有 {count:,} 条 round3_scores")

    # 逐批合并（使用 INSERT OR REPLACE 语义）
    rows = pre_db.execute("""
        SELECT candidate_id, bepipred3_score, bepipred3_success,
               temstapro_score, temstapro_success,
               sodope_score, sodope_success,
               plm4cpps_score, plm4cpps_success,
               graphcpp_score, graphcpp_success
        FROM round3_scores
        ORDER BY candidate_id
    """).fetchall()
    pre_db.close()

    BATCH = 10_000
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        values = ",".join(
            f"({r[0]},{'NULL' if r[1] is None else r[1]},"
            f"{'true' if r[2] else 'false'},"
            f"{'NULL' if r[3] is None else r[3]},"
            f"{'true' if r[4] else 'false'},"
            f"{'NULL' if r[5] is None else r[5]},"
            f"{'true' if r[6] else 'false'},"
            f"{'NULL' if r[7] is None else r[7]},"
            f"{'true' if r[8] else 'false'},"
            f"{'NULL' if r[9] is None else r[9]},"
            f"{'true' if r[10] else 'false'})"
            for r in batch
        )
        main_db._conn.execute(f"""
            INSERT INTO round3_scores
                (candidate_id, bepipred3_score, bepipred3_success,
                 temstapro_score, temstapro_success,
                 sodope_score, sodope_success,
                 plm4cpps_score, plm4cpps_success,
                 graphcpp_score, graphcpp_success)
            VALUES {values}
            ON CONFLICT (candidate_id) DO UPDATE SET
                bepipred3_score   = EXCLUDED.bepipred3_score,
                bepipred3_success = EXCLUDED.bepipred3_success,
                temstapro_score   = EXCLUDED.temstapro_score,
                temstapro_success = EXCLUDED.temstapro_success,
                sodope_score      = EXCLUDED.sodope_score,
                sodope_success    = EXCLUDED.sodope_success,
                plm4cpps_score    = EXCLUDED.plm4cpps_score,
                plm4cpps_success  = EXCLUDED.plm4cpps_success,
                graphcpp_score    = EXCLUDED.graphcpp_score,
                graphcpp_success  = EXCLUDED.graphcpp_success
        """)

    merged = main_db.get_last_processed_id("round3_scores")
    log(f"合并完成: {merged:,} 条")
    main_db.close()


async def run(
    services_subset: list[str] | None = None,
    resume: bool = False,
    pdb_path: str = PRECOMPUTE_DB,
    snapshot_path: str | None = None,
):
    # ── 0. 打开预打分数据库（与 pipeline.db 分离，避免写锁冲突） ──
    log(f"预打分数据库: {pdb_path}")

    # 创建预打分 DB schema（不含 FK 约束 — candidates 在 pipeline.db 中）
    pdb = PipelineDB(pdb_path)
    conn = pdb.connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS round3_scores (
            candidate_id        BIGINT PRIMARY KEY,
            bepipred3_score     FLOAT,
            bepipred3_success   BOOLEAN,
            temstapro_score     FLOAT,
            temstapro_success   BOOLEAN,
            sodope_score        FLOAT,
            sodope_success      BOOLEAN,
            plm4cpps_score      FLOAT,
            plm4cpps_success    BOOLEAN,
            graphcpp_score      FLOAT,
            graphcpp_success    BOOLEAN,
            scored_at           TIMESTAMP DEFAULT now()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoint (
            round           VARCHAR NOT NULL,
            step            VARCHAR NOT NULL,
            status          VARCHAR NOT NULL DEFAULT 'pending',
            total_items     BIGINT  DEFAULT 0,
            processed_items BIGINT  DEFAULT 0,
            error_message   VARCHAR,
            updated_at      TIMESTAMP DEFAULT now(),
            started_at      TIMESTAMP DEFAULT now(),
            PRIMARY KEY (round, step)
        )
    """)

    # ── 1. 从 pipeline.db 读取候选 ──
    # DuckDB 不允许另一进程持有写锁时建立只读连接，因此需要 snapshot。
    # 如未提供 snapshot_path，先尝试直接连接（非 Round 2 运行时可用）。
    src = snapshot_path or str(DEFAULT_DB_PATH)
    log(f"从 {src} 读取 round1_channels...")
    reader = duckdb.connect(src, read_only=True)
    rows = reader.execute("""
        SELECT c.candidate_id, c.sequence, c.length, ch.anoxpepred_score, ch.rank_in_channel
        FROM round1_channels ch
        JOIN candidates c ON c.candidate_id = ch.candidate_id
        WHERE ch.channel IN ('top', 'bottom')
        ORDER BY ch.rank_in_channel
    """).fetchall()
    all_candidates = [
        {"candidate_id": int(r[0]), "sequence": r[1], "length": r[2],
         "anoxpepred_score": float(r[3]) if r[3] else None,
         "rank_in_channel": int(r[4]) if r[4] else None}
        for r in rows
    ]
    reader.close()

    if not all_candidates:
        log("❌ 无候选，请先运行 Round 1")
        return

    total = len(all_candidates)
    log(f"读取候选: {total:,} 条 (top 通道: {sum(1 for c in all_candidates if c['rank_in_channel'] is not None)})")

    # ── 2. 确定要打分的服务列表 ──
    services = services_subset or list(SERVICE_ORDER)

    # ── 3. 逐服务顺序打分 ──
    for svc in services:
        if svc not in SERVICE_ORDER:
            log(f"⚠️ 跳过未知服务: {svc} (有效: {SERVICE_ORDER})")
            continue

        # 检查 checkpoint（断点续跑）
        cp = pdb.get_checkpoint(f"{CHECKPOINT_PREFIX}_{svc}")
        if resume and cp and cp["status"] == "done":
            log(f"  {svc}: ✅ 已跳过（上次已完成 {cp['processed_items']:,} 条）")
            continue

        log(f"\n▶ 开始 {svc} 预打分 ({total:,} 条)...")
        svc_start = time.time()
        pdb.set_checkpoint(CHECKPOINT_PREFIX, svc, "running",
                           total=total, processed=0)

        client = ServiceClient(timeout=TIMEOUT)
        sem = asyncio.Semaphore(CONCURRENCY)
        chunks = [all_candidates[i:i + BATCH_SIZE]
                  for i in range(0, total, BATCH_SIZE)]

        async def score_chunk(chunk: list[dict]) -> list[dict]:
            async with sem:
                items = [
                    {"peptide_id": str(c["candidate_id"]), "sequence": c["sequence"]}
                    for c in chunk
                ]
                result = await client.predict_batch(svc, items)
                if result.get("success") and result.get("results"):
                    return result["results"]
                return [{"peptide_id": item["peptide_id"], "score": None}
                        for item in items]

        # ── 分批打分 + 写入 ──
        for chunk_idx, chunk in enumerate(chunks):
            results = await score_chunk(chunk)

            # 构建 round3_scores record（只设当前服务的分数，其他留 NULL）
            records = []
            for c in chunk:
                cid_str = str(c["candidate_id"])
                record: dict = {"candidate_id": c["candidate_id"]}
                matching = [r for r in results if r["peptide_id"] == cid_str]
                score = matching[0].get("score") if matching else None
                record[f"{svc}_score"] = score
                record[f"{svc}_success"] = score is not None
                for other in SERVICE_ORDER:
                    if other == svc:
                        continue
                    record[f"{other}_score"] = None
                    record[f"{other}_success"] = False
                records.append(record)

            pdb.insert_round3_scores(records)
            pdb.set_checkpoint(CHECKPOINT_PREFIX, svc, "running",
                               total=total, processed=(chunk_idx + 1) * BATCH_SIZE)

            if (chunk_idx + 1) % PROGRESS_INTERVAL == 0 or chunk_idx == len(chunks) - 1:
                p_processed = min((chunk_idx + 1) * BATCH_SIZE, total)
                pct = p_processed / total * 100
                elapsed = time.time() - svc_start
                rate = p_processed / elapsed if elapsed > 0 else 0
                log(f"  {svc}: {p_processed:,}/{total:,} ({pct:.1f}%) | "
                    f"{rate:.0f} seq/s | {elapsed:.0f}s")

        await client.close()

        pdb.set_checkpoint(CHECKPOINT_PREFIX, svc, "done",
                           total=total, processed=total)
        svc_elapsed = time.time() - svc_start
        log(f"  {svc}: ✅ 完成 ({svc_elapsed:.0f}s)")

    # ── 4. 汇总 ──
    log("\n" + "=" * 55)
    log("  预打分汇总:")
    for svc in SERVICE_ORDER:
        cp = pdb.get_checkpoint(f"{CHECKPOINT_PREFIX}_{svc}")
        status = "✅" if cp and cp["status"] == "done" else "⏳"
        p_s = f"{cp['processed_items']:,}" if cp else "0"
        log(f"  {status} {svc}: {p_s}")
    scored = pdb.get_last_processed_id("round3_scores")
    log(f"  round3_scores 总计: {scored:,} 条")
    log(f"\n  Round 2 完成后运行合并:")
    log(f"    uv run python -m main.stages4.s4_round03_precompute --merge")
    log("=" * 55)

    pdb.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round 3 预打分（GPU 空闲时提前计算）")
    parser.add_argument("--services", type=str, default=None,
                        help="服务列表，逗号分隔 (default: 全部)")
    parser.add_argument("--resume", action="store_true",
                        help="恢复模式：跳过已完成的服务")
    parser.add_argument("--status", action="store_true",
                        help="仅显示预打分状态")
    parser.add_argument("--merge", action="store_true",
                        help="将 precompute.db 合并回 pipeline.db")
    parser.add_argument("--snapshot", type=str, default=None,
                        help="pipeline.db snapshot 路径 (当 Round 2 持有写锁时)")
    args = parser.parse_args()

    if args.merge:
        merge_scores()
        return

    if args.status:
        print_status()
        return

    services = args.services.split(",") if args.services else None

    log(f"Round 3 预打分")
    log(f"服务顺序: {services or SERVICE_ORDER}")
    log(f"恢复模式: {'开' if args.resume else '关'}")

    asyncio.run(run(services, resume=args.resume, snapshot_path=args.snapshot))


if __name__ == "__main__":
    main()
