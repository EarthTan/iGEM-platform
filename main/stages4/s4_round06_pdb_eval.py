"""
Round 6: PDB 评估 — SASA + Aggrescan3D。

对 Round 5 生成的 PDB 文件运行 SASA（溶剂可及性）和 Aggrescan3D（聚集倾向）。
不做加权混合，分别记录分数供 Round 7 使用。

用法:
    uv run python -m main.stages4.s4_round06_pdb_eval
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

OUTPUT4 = PROJECT_ROOT / "output4"
PDB_DIR = OUTPUT4 / "pdb"

SASA_CONCURRENCY = 10
AGGRESCAN_CONCURRENCY = 2


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


async def run():
    start_time = time.time()

    # ── 1. 连接数据库 ──
    db = PipelineDB()
    conn = db.connect()
    db.init_schema()

    # ── 2. 获取有 PDB 结果的 construct ──
    rows = conn.execute("""
        SELECT s.construct_id, s.pdb_path, c.channel, c.peptide_seq
        FROM structure_results s
        JOIN constructs c ON c.construct_id = s.construct_id
        WHERE s.pdb_path IS NOT NULL AND s.pdb_path != ''
        ORDER BY s.construct_id
    """).fetchall()

    if not rows:
        log("❌ 无 PDB 结果，请先运行 Round 5")
        return

    constructs = [
        {"construct_id": int(r[0]), "pdb_path": r[1], "channel": r[2], "peptide_seq": r[3]}
        for r in rows
    ]
    log(f"待评估: {len(constructs)} constructs")

    # ── 3. 检查已有结果 ──
    existing = db.row_count("pdb_eval")
    if existing > 0:
        log(f"已有评估结果: {existing} 条")

    # ── 4. 确保 Docker 服务就绪（已在 main() 中检查） ──
    log("SASA / Aggrescan3D 服务已就绪")

    # ── 5. SASA + Aggrescan3D 评分 ──
    client = ServiceClient(timeout=300.0)
    sem_sasa = asyncio.Semaphore(SASA_CONCURRENCY)
    sem_agg = asyncio.Semaphore(AGGRESCAN_CONCURRENCY)
    batch_size = 50

    chunks = [constructs[i:i + batch_size] for i in range(0, len(constructs), batch_size)]

    async def score_sasa(chunk: list[dict]) -> list[dict]:
        async with sem_sasa:
            items = []
            for c in chunk:
                pdb_content = ""
                pdb_path = c["pdb_path"]
                if pdb_path:
                    try:
                        pdb_content = Path(pdb_path).read_text()
                    except Exception:
                        pass
                items.append({
                    "peptide_id": str(c["construct_id"]),
                    "pdb_content": pdb_content,
                    "sequence": c.get("peptide_seq", ""),
                })
            result = await client.predict_pdb_batch("sasa", items)
            if result.get("success") and result.get("results"):
                return result["results"]
            return [{"construct_id": item["peptide_id"], "score": None} for item in items]

    async def score_agg(chunk: list[dict]) -> list[dict]:
        async with sem_agg:
            items = []
            for c in chunk:
                pdb_content = ""
                pdb_path = c["pdb_path"]
                if pdb_path:
                    try:
                        pdb_content = Path(pdb_path).read_text()
                    except Exception:
                        pass
                items.append({
                    "peptide_id": str(c["construct_id"]),
                    "pdb_content": pdb_content,
                })
            result = await client.predict_pdb_batch("aggrescan3d", items)
            if result.get("success") and result.get("results"):
                return result["results"]
            return [{"construct_id": item["peptide_id"], "score": None} for item in items]

    sasa_results, agg_results = await asyncio.gather(
        asyncio.gather(*[score_sasa(c) for c in chunks], return_exceptions=True),
        asyncio.gather(*[score_agg(c) for c in chunks], return_exceptions=True),
    )

    # 扁平化结果
    sasa_flat: dict[str, float | None] = {}
    for batch in sasa_results:
        if isinstance(batch, list):
            for r in batch:
                if isinstance(r, dict):
                    pid = r.get("peptide_id", r.get("construct_id", ""))
                    sasa_flat[pid] = r.get("score")

    agg_flat: dict[str, float | None] = {}
    for batch in agg_results:
        if isinstance(batch, list):
            for r in batch:
                if isinstance(r, dict):
                    pid = r.get("peptide_id", r.get("construct_id", ""))
                    agg_flat[pid] = r.get("score")

    # ── 6. 写入评估结果 ──
    records = []
    for c in constructs:
        cid = c["construct_id"]
        records.append({
            "construct_id": cid,
            "sasa_score": sasa_flat.get(str(cid)),
            "sasa_success": sasa_flat.get(str(cid)) is not None,
            "aggrescan3d_score": agg_flat.get(str(cid)),
            "aggrescan3d_success": agg_flat.get(str(cid)) is not None,
        })

    written = db.insert_pdb_eval(records)
    await client.close()

    # ── 7. 统计报告 ──
    log("\n分数分布:")
    for metric in ["sasa_score", "aggrescan3d_score"]:
        dist = db.compute_distribution("pdb_eval", metric)
        if dist:
            log(f"  {metric}: mean={dist['mean']:.4f}, min={dist['min']:.4f}, max={dist['max']:.4f}")

    total_elapsed = time.time() - start_time
    db.set_checkpoint("round6", "pdb_eval", "done",
                      total=len(constructs), processed=len(constructs))

    log(f"\n{'='*55}")
    log(f"  Round 6 完成!")
    log(f"  评估: {written}/{len(constructs)}")
    log(f"  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    log(f"{'='*55}")

    db.close()


def main():
    log("Round 6: PDB 评估 — SASA + Aggrescan3D")

    info = get_round_services("round6")
    log(f"依赖服务: {', '.join(info['services'])}")
    health = ensure_services(info["services"], info["profiles"], timeout=180.0)
    unavailable = [s for s, h in health.items() if not h["available"]]
    if unavailable:
        log(f"❌ 服务不可用: {unavailable}")
        sys.exit(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
