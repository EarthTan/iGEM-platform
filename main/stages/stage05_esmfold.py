"""
阶段五：ESMFold 3D 结构预测

读取阶段四的 top90 construct，调用 ESMFold 进行 3D 结构预测，
保存 PDB 文件并提取 pLDDT 置信度。

用法：
    .venv/bin/python -m main.stages.stage05_esmfold

输入：
    output/stage04_enumerate/final/stage5_input.json

输出：
    output/stage05_esmfold/
    ├── pdb/              ← PDB 文件（每个 construct 一个）
    ├── scores/           ← pLDDT 汇总
    ├── checkpoint.json   ← 进度检查点（崩溃后恢复）
    ├── final/            ← 汇总结果
    ├── README.md
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE = "stage05_esmfold"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient

LOG_FILE: Path | None = None

# 并发控制
CONCURRENCY = 3          # ESMFold 并发数
POLL_INTERVAL = 30.0     # 轮询间隔（秒）
TIMEOUT_PER_JOB = 7200.0  # 单任务超时（2h）


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def make_dir(name: str) -> Path:
    d = STAGE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(dir_path: Path, filename: str, data):
    with open(dir_path / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def run():
    global LOG_FILE
    start_time = time.time()
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = STAGE_DIR / "run.log"

    log("=" * 60)
    log("阶段五：ESMFold 3D 结构预测")
    log("=" * 60)

    # ── 读取阶段四输入 ──
    input_path = OUTPUT_DIR / "stage04_enumerate" / "final" / "stage5_input.json"
    if not input_path.exists():
        log(f"❌ 找不到输入: {input_path}")
        return

    with open(input_path) as f:
        stage5_input = json.load(f)

    constructs = stage5_input["constructs"]
    n_total = len(constructs)
    log(f"输入: {n_total} 个 construct")

    # 读取完整数据（含序列）
    fasta_path = OUTPUT_DIR / "stage04_enumerate" / "final" / f"top{n_total}.fasta"
    sequences: dict[str, str] = {}
    if fasta_path.exists():
        current_id = None
        current_seq: list[str] = []
        with open(fasta_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(">"):
                    if current_id and current_seq:
                        sequences[current_id] = "".join(current_seq)
                    current_id = line[1:].split(" | ")[0]  # construct_id
                    current_seq = []
                else:
                    current_seq.append(line)
            if current_id and current_seq:
                sequences[current_id] = "".join(current_seq)
        log(f"读取 {len(sequences)} 条序列")
    else:
        log(f"⚠ FASTA 文件不存在: {fasta_path}")
        # 从 JSON 中已有 construct 信息继续

    # ── 恢复检查点 ──
    pdb_dir = make_dir("pdb")
    scores_dir = make_dir("scores")
    final_dir = make_dir("final")
    checkpoint_path = STAGE_DIR / "checkpoint.json"

    completed_ids: set[str] = set()
    all_results: list[dict] = []
    checkpoint_data = {}

    if checkpoint_path.exists():
        with open(checkpoint_path) as f:
            checkpoint_data = json.load(f)
        completed_ids = set(checkpoint_data.get("completed_ids", []))
        all_results = checkpoint_data.get("results", [])
        log(f"恢复检查点: {len(completed_ids)}/{n_total} 已完成")

    # ── 过滤未完成的 construct ──
    pending = [c for c in constructs if c["construct_id"] not in completed_ids]
    if not pending:
        log("所有 construct 已完成！跳过预测。")
    else:
        log(f"待预测: {len(pending)}/{n_total} 个 construct")
        log(f"并发: {CONCURRENCY}, 预计 ~{len(pending) * 2 // CONCURRENCY} 分钟")

    # ══════════════════════════════════════════════════════════════════
    # ESMFold 预测
    # ══════════════════════════════════════════════════════════════════

    client = ServiceClient(timeout=30.0)
    sem = asyncio.Semaphore(CONCURRENCY)

    results_lock = asyncio.Lock()
    completed_count = len(completed_ids)

    async def predict_one(construct: dict) -> dict:
        nonlocal completed_count
        cid = construct["construct_id"]
        seq = construct.get("sequence") or sequences.get(cid, "")
        if not seq:
            return {
                "construct_id": cid,
                "success": False,
                "error": "No sequence available",
                "confidence": None,
            }

        async with sem:
            log(f"  ▶ {cid} ({construct['peptide_id']}, {construct['position']}, {len(seq)}aa)")
            t0 = time.time()

            result = await client.predict_structure_async(
                "esmfold",
                seq,
                peptide_id=cid,
                poll_interval=POLL_INTERVAL,
                timeout=TIMEOUT_PER_JOB,
            )

            elapsed = time.time() - t0
            success = result.get("success", False)
            confidence = result.get("confidence")  # normalized pLDDT (0-1)
            pdb_content = result.get("pdb_content", "")

            if success and pdb_content:
                # 保存 PDB
                pdb_path = pdb_dir / f"{cid}.pdb"
                with open(pdb_path, "w") as f:
                    f.write(pdb_content)
                plddt_str = f"{confidence:.4f}" if confidence is not None else "N/A"
                log(f"  ✓ {cid}  done in {elapsed:.0f}s, pLDDT={plddt_str}")
            else:
                err = result.get("error", "unknown")
                log(f"  ✗ {cid}  failed ({elapsed:.0f}s): {err}")

            entry = {
                "construct_id": cid,
                "peptide_id": construct["peptide_id"],
                "linker_id": construct["linker_id"],
                "position": construct["position"],
                "length": len(seq),
                "success": success,
                "plddt": round(confidence, 4) if confidence is not None else None,
                "pdb_file": f"{cid}.pdb" if success and pdb_content else None,
                "elapsed": round(elapsed, 1),
                "error": result.get("error") if not success else None,
            }

            async with results_lock:
                all_results.append(entry)
                completed_ids.add(cid)
                completed_count += 1

                # 每完成 5 个保存一次检查点
                if completed_count % 5 == 0:
                    save_checkpoint(checkpoint_path, completed_ids, all_results)
                    log(f"  📝 检查点已保存 ({completed_count}/{n_total})")

            return entry

    # 启动预测
    tasks = [predict_one(c) for c in pending]
    if tasks:
        await asyncio.gather(*tasks)
        save_checkpoint(checkpoint_path, completed_ids, all_results)

    await client.close()
    total_time = time.time() - start_time

    # ══════════════════════════════════════════════════════════════════
    # 汇总
    # ══════════════════════════════════════════════════════════════════

    log("\n" + "=" * 60)
    log("📊 汇总")

    n_success = sum(1 for r in all_results if r["success"])
    n_failed = sum(1 for r in all_results if not r["success"])
    plddt_values = [r["plddt"] for r in all_results if r["plddt"] is not None]

    log(f"  成功: {n_success}/{n_total}")
    log(f"  失败: {n_failed}/{n_total}")
    if plddt_values:
        log(f"  pLDDT: min={min(plddt_values):.4f}, max={max(plddt_values):.4f}, "
            f"mean={sum(plddt_values)/len(plddt_values):.4f}")

    # 保存评分汇总
    results_sorted = sorted(all_results, key=lambda r: r.get("plddt") or 0, reverse=True)
    for i, r in enumerate(results_sorted, 1):
        r["rank"] = i

    write_json(scores_dir, "all_plddt.json", results_sorted)

    # CSV 汇总
    import csv
    csv_path = scores_dir / "all_plddt.csv"
    with open(csv_path, "w", newline="") as f:
        if results_sorted:
            writer = csv.DictWriter(f, fieldnames=results_sorted[0].keys())
            writer.writeheader()
            writer.writerows(results_sorted)
    log(f"pLDDT 汇总: {csv_path}")

    # top 5 by pLDDT
    top5 = results_sorted[:5]
    log(f"\n  Top 5 by pLDDT:")
    for r in top5:
        log(f"    {r['rank']:2d}. {r['construct_id']:12s} | {r['peptide_id']:12s} | "
            f"{r['position']:5s} | pLDDT={r['plddt']:.4f}" if r['plddt'] else f"  N/A")

    # 写入 README
    write_readme(n_total, n_success, n_failed, plddt_values, results_sorted, total_time)
    write_status(n_total, n_success, plddt_values, total_time)

    log(f"\n✅ 阶段五完成！耗时: {total_time:.0f}s")


def save_checkpoint(path: Path, completed_ids: set[str], results: list[dict]):
    data = {
        "completed_ids": sorted(completed_ids),
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }
    write_json(path.parent, path.name, data)


def write_readme(n_total: int, n_success: int, n_failed: int,
                 plddt_values: list[float], results: list[dict], elapsed: float):
    top5_lines = "\n".join(
        f"| {r['rank']} | {r['construct_id']} | {r['peptide_id']} | "
        f"{r['linker_id']} | {r['position']} | {r['plddt']:.4f} |" if r['plddt'] else ""
        for r in results[:5]
    )

    plddt_stats = ""
    if plddt_values:
        import statistics
        plddt_stats = (f"min={min(plddt_values):.4f}, max={max(plddt_values):.4f}, "
                       f"mean={sum(plddt_values)/len(plddt_values):.4f}, "
                       f"median={statistics.median(plddt_values):.4f}")

    readme = f"""# 阶段五：ESMFold 3D 结构预测 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.0f} 秒

## 输入

- **construct**: {n_total} 条（来自阶段四）
- **服务**: ESMFold (ESM-2 3B)

## 结果

| 指标 | 值 |
|------|-----|
| 成功 | {n_success}/{n_total} |
| 失败 | {n_failed}/{n_total} |
| pLDDT | {plddt_stats} |

## pLDDT 分布

| 区间 | 意义 |
|------|------|
| > 0.90 | 高置信度 |
| 0.70 – 0.90 | 可信 |
| 0.50 – 0.70 | 低置信度 |
| < 0.50 | 不可靠 |

## Top 5 by pLDDT

| 排名 | Construct | 肽 | Linker | 位置 | pLDDT |
|------|-----------|-----|--------|------|-------|
{top5_lines}

## 输出

- `pdb/*.pdb` — {n_success} 个 PDB 文件
- `scores/all_plddt.csv` — pLDDT 评分汇总
- `final/` — 后续阶段输入

## 下一步

阶段六（PDB 评估 — SASA + Aggrescan3D）
"""
    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {readme_path}")


def write_status(n_total: int, n_success: int, plddt_values: list[float], elapsed: float):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    plddt_mean = f"{sum(plddt_values)/len(plddt_values):.4f}" if plddt_values else "N/A"

    status = f"""# 🧬 Pipeline 状态

**更新**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**焦点**: 抗氧化肽

---

## 全局进度

| # | 阶段 | 状态 | 输入 → 输出 |
|---|------|------|-------------|
| 1 | 硬过滤 | ✅ 完成 | 1843 → **107** 条 |
| 2 | 快速评分 + 排序 | ✅ 完成 | 107 → **80** 条 |
| 3 | 精确评分 | ⏹ 跳过 | — |
| 4 | 枚举 + Construct FASTA 评分 | ✅ 完成 | 20 肽 → 90 construct |
| 5 | 3D 预测 (ESMFold) | ✅ 完成 | {n_total} → **{n_success}** PDB |
| 6 | PDB 评估 | ⏳ 待开始 | — |

## 阶段五：ESMFold 3D 结构预测

**成功**: {n_success}/{n_total}
**平均 pLDDT**: {plddt_mean}
**耗时**: {elapsed:.0f}s

**输出**: `stage05_esmfold/pdb/*.pdb`

详见: `stage05_esmfold/README.md`

## 下一步

阶段六（PDB 评估 — SASA + Aggrescan3D）
"""
    with open(status_path, "w", encoding="utf-8") as f:
        f.write(status)

    latest = OUTPUT_DIR / "STATUS.md"
    with open(latest, "w", encoding="utf-8") as f:
        f.write(status)

    log(f"状态已写入: {status_path}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
