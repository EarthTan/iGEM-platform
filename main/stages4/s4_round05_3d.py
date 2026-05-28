"""
Round 5: 3D 结构预测 — OmegaFold。

从 constructs 表按通道取 Top N：
  Top 通道 150（SodoPE+TemStaPro 综合分最高）
  Bottom 通道 100（SodoPE+TemStaPro 综合分最高）
合并后按综合分排序，逐个跑 OmegaFold 3D 预测。

OmegaFold 阻塞事件循环，只能串行推理（Semaphore=1）。
总耗时 ~250×90s ≈ 6.25h，在 8h 限制内。

用法:
    uv run python -m main.stages4.s4_round05_3d
    uv run python -m main.stages4.s4_round05_3d --top-n 150 --bottom-n 100
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from main.client import ServiceClient
from main.config import service_url
from main.stages4.s4_db import PipelineDB
from main.stages4.s4_docker_utils import ensure_services
from main.stages4.s4_service_map import get_round_services

OUTPUT4 = PROJECT_ROOT / "output4"
PDB_DIR = OUTPUT4 / "pdb"
PDB_DIR.mkdir(parents=True, exist_ok=True)

# ── 并发控制 ──
CONCURRENCY = 1
PREDICT_TIMEOUT = 14400
PROGRESS_INTERVAL = 5


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_top_constructs(
    conn, channel: str, limit: int,
) -> list[dict]:
    """按通道取 SoDoPE+TemStaPro 综合分最高的 constructs。"""
    rows = conn.execute("""
        SELECT c.construct_id, c.full_sequence, c.channel,
               ROUND((cs.sodope_score + cs.temstapro_score) / 2.0, 4) as combined
        FROM constructs c
        JOIN construct_scores cs ON cs.construct_id = c.construct_id
        WHERE c.channel = ?
        ORDER BY combined DESC
        LIMIT ?
    """, [channel, limit]).fetchall()
    return [
        {"construct_id": int(r[0]), "sequence": r[1],
         "channel": r[2], "combined": r[3]}
        for r in rows
    ]


async def predict_omegafold(
    client: httpx.AsyncClient,
    sequence: str,
    construct_id: int,
    sem: asyncio.Semaphore,
) -> dict:
    """调用 OmegaFold 预测单个 construct 的 3D 结构。"""
    async with sem:
        url = f"{service_url('omegafold')}/predict/batch"
        payload = {
            "sequences": [{"peptide_id": f"con_{construct_id:04d}", "sequence": sequence}],
        }
        try:
            resp = await client.post(url, json=payload, timeout=PREDICT_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    r = results[0]
                    return {
                        "construct_id": construct_id,
                        "success": True,
                        "pdb": r.get("pdb_content", ""),
                        "confidence": r.get("confidence"),
                        "error": None,
                    }
                return {
                    "construct_id": construct_id, "success": False,
                    "pdb": "", "confidence": None,
                    "error": "Empty results",
                }
            else:
                return {
                    "construct_id": construct_id,
                    "success": False,
                    "pdb": "", "confidence": None,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            return {
                "construct_id": construct_id,
                "success": False,
                "pdb": "", "confidence": None,
                "error": str(e),
            }


async def run(top_n: int, bottom_n: int):
    start_time = time.time()

    # ── 1. 连接数据库 ──
    db = PipelineDB()
    conn = db.connect()
    db.init_schema()

    # ── 2. 按通道取 Top constructs ──
    all_constructs = []
    if top_n > 0:
        all_constructs.extend(get_top_constructs(conn, "top", top_n))
    if bottom_n > 0:
        all_constructs.extend(get_top_constructs(conn, "bottom", bottom_n))

    if not all_constructs:
        log("❌ 无 construct 可预测")
        return

    # 按综合分排序（跨通道统一排名）
    all_constructs.sort(key=lambda x: x["combined"], reverse=True)
    log(f"待预测: {len(all_constructs)} constructs "
        f"(Top {top_n}: {sum(1 for c in all_constructs if c['channel']=='top')}, "
        f"Bottom {bottom_n}: {sum(1 for c in all_constructs if c['channel']=='bottom')})")
    log(f"  综合分范围: {all_constructs[0]['combined']:.4f} ~ {all_constructs[-1]['combined']:.4f}")

    # ── 3. 检查已完成 ──
    done_count = db.row_count("structure_results")
    if done_count > 0:
        log(f"已有结构结果: {done_count}，跳过已完成")
        remaining = [c for c in all_constructs
                     if not conn.execute(
                         "SELECT 1 FROM structure_results WHERE construct_id=?",
                         [c["construct_id"]]
                     ).fetchone()]
        log(f"待预测: {len(remaining)}")
        all_constructs = remaining

    if not all_constructs:
        log("✅ 所有 construct 已完成 3D 预测")
        return

    total = len(all_constructs)
    total_est = total * 90  # 预估总耗时
    log(f"预估耗时: {total} × 90s ≈ {total_est:.0f}s ({total_est/3600:.1f}h)")

    # ── 4. 确保 OmegaFold 就绪 ──
    log("✅ OmegaFold 已就绪")

    # ── 5. 预测循环 ──
    sem = asyncio.Semaphore(CONCURRENCY)
    success_count = 0
    fail_count = 0

    async with httpx.AsyncClient(timeout=PREDICT_TIMEOUT) as client:
        for idx, c in enumerate(all_constructs):
            cid = c["construct_id"]
            seq = c["sequence"]
            chan = c["channel"]
            comb = c["combined"]

            log(f"[{idx + 1}/{total}] con_{cid:04d} ({len(seq)} aa, "
                f"{chan}, combined={comb:.4f}) ...")

            result = await predict_omegafold(client, seq, cid, sem)

            if result["success"] and result["pdb"]:
                construct_dir = PDB_DIR / f"con_{cid:04d}"
                construct_dir.mkdir(parents=True, exist_ok=True)
                pdb_path = construct_dir / "omegafold.pdb"
                with open(pdb_path, "w") as f:
                    f.write(result["pdb"])

                plddt_val = result.get("confidence")
                db.write_structure_result(
                    construct_id=cid,
                    service="omegafold",
                    pdb_path=str(pdb_path),
                    plddt=plddt_val,
                )

                plddt_str = f"{plddt_val:.4f}" if plddt_val is not None else "N/A"
                log(f"  ✅ pLDDT={plddt_str}")
                success_count += 1
            else:
                log(f"  ❌ 失败: {result['error'][:100]}")
                db.write_structure_result(
                    construct_id=cid,
                    service="omegafold",
                    pdb_path="",
                    plddt=None,
                )
                fail_count += 1

            # 进度报告
            if (idx + 1) % PROGRESS_INTERVAL == 0:
                elapsed = time.time() - start_time
                rate = (idx + 1) / elapsed if elapsed > 0 else 0
                eta = (total - idx - 1) / rate if rate > 0 else 0
                log(f"  进度: {idx + 1}/{total} | "
                    f"{rate*60:.1f} cons/min | "
                    f"已用: {elapsed/60:.1f} min | "
                    f"ETA: {eta/60:.1f} min")

    total_elapsed = time.time() - start_time
    log(f"\n{'='*55}")
    log(f"  Round 5 完成!")
    log(f"  成功: {success_count}/{total}")
    if fail_count:
        log(f"  失败: {fail_count}")
    log(f"  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    log(f"{'='*55}")

    db.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round 5: 3D 结构预测 — OmegaFold")
    parser.add_argument("--top-n", type=int, default=150,
                        help="Top 通道取前 N 个 constructs (default: 150)")
    parser.add_argument("--bottom-n", type=int, default=100,
                        help="Bottom 通道取前 N 个 constructs (default: 100)")
    args = parser.parse_args()

    total = args.top_n + args.bottom_n
    log(f"Round 5: 3D 结构预测 — OmegaFold")
    log(f"Top {args.top_n} + Bottom {args.bottom_n} = {total} constructs")

    if total > 400:
        log(f"⚠️ {total} 个 constructs 预估耗时 > 10h，建议减少数量")
    else:
        log(f"预估耗时: {total} × 90s ≈ {total * 90 / 3600:.1f}h")

    info = get_round_services("round5")
    log(f"依赖服务: {', '.join(info['services'])}")
    health = ensure_services(info["services"], info["profiles"], timeout=300.0)
    unavailable = [s for s, h in health.items() if not h["available"]]
    if unavailable:
        log(f"❌ OmegaFold 不可用: {unavailable}")
        sys.exit(1)

    asyncio.run(run(top_n=args.top_n, bottom_n=args.bottom_n))


if __name__ == "__main__":
    main()
