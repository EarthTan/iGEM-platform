"""
阶段六：PDB 评估（SASA + Aggrescan3D）

读取阶段五的 PDB 文件，运行两个 PDB 级评估服务：
  1. SASA — 溶剂可及表面积，评估功能肽暴露度
  2. Aggrescan3D — 聚集倾向分析

综合评分给出最终 construct 排名。

用法：
    .venv/bin/python -m main.stages.stage06_pdb_eval

输入：
    output/stage05_esmfold/pdb/        ← PDB 文件
    output/stage04_enumerate/final/top90.csv  ← construct 元数据

输出：
    output/stage06_pdb_eval/
    ├── sasa/             ← SASA 逐条结果
    ├── aggrescan3d/      ← Aggrescan3D 逐条结果
    ├── final/            ← 综合排名
    ├── checkpoint.json   ← 进度检查点
    ├── README.md
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE = "stage06_pdb_eval"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient

LOG_FILE: Path | None = None

# 并发
SASA_CONCURRENCY = 10
A3D_CONCURRENCY = 2
A3D_TIMEOUT = 1800  # 30 min per PDB

# 综合评分权重（SASA 暴露度 0.4 + 原有 composite 0.6）
WEIGHT_COMPOSITE = 0.60
WEIGHT_SASA = 0.40


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
    log("阶段六：PDB 评估（SASA + Aggrescan3D）")
    log("=" * 60)

    # ── 读取 construct 元数据 ──
    meta_path = OUTPUT_DIR / "stage04_enumerate" / "final" / "top90.csv"
    if not meta_path.exists():
        log(f"❌ 找不到元数据: {meta_path}")
        return
    df_meta = pd.read_csv(meta_path)
    log(f"读取 {len(df_meta)} 条 construct 元数据")

    # ── 读取 PDB 文件 ──
    pdb_dir = OUTPUT_DIR / "stage05_esmfold" / "pdb"
    if not pdb_dir.exists():
        log(f"❌ 找不到 PDB 目录: {pdb_dir}")
        return

    pdb_files = sorted(pdb_dir.glob("*.pdb"))
    log(f"PDB 文件: {len(pdb_files)} 个")

    # 建立 construct_id → 元数据 索引
    meta_index = {}
    for _, row in df_meta.iterrows():
        cid = row["construct_id"]
        meta_index[cid] = {
            "peptide_id": row["peptide_id"],
            "peptide_sequence": row["peptide_sequence"],
            "linker_id": row["linker_id"],
            "position": row["position"],
            "composite_score": row["composite_score"],
            "length": row["length"],
        }

    # 建立 construct_id → PDB 内容 索引
    pdb_index: dict[str, str] = {}
    for pdb_path in pdb_files:
        cid = pdb_path.stem
        pdb_index[cid] = pdb_path.read_text()

    # 所有待处理的 construct_id
    all_cids = sorted(set(meta_index.keys()) & set(pdb_index.keys()))
    log(f"待处理: {len(all_cids)} 个 construct（有元数据且有 PDB）")

    # ══════════════════════════════════════════════════════════════════
    # 检查点
    # ══════════════════════════════════════════════════════════════════
    checkpoint_path = STAGE_DIR / "checkpoint.json"
    done_sasa: set[str] = set()
    done_a3d: set[str] = set()
    sasa_results: list[dict] = []
    a3d_results: list[dict] = []

    if checkpoint_path.exists():
        cp = json.loads(checkpoint_path.read_text())
        done_sasa = set(cp.get("done_sasa", []))
        done_a3d = set(cp.get("done_a3d", []))
        sasa_results = cp.get("sasa_results", [])
        a3d_results = cp.get("a3d_results", [])
        log(f"检查点: SASA {len(done_sasa)}/{len(all_cids)}, Aggrescan3D {len(done_a3d)}/{len(all_cids)} 已完成")

    client = ServiceClient(timeout=30.0)

    # ══════════════════════════════════════════════════════════════════
    # SASA 评分
    # ══════════════════════════════════════════════════════════════════
    pending_sasa = [c for c in all_cids if c not in done_sasa]
    if pending_sasa:
        log(f"\n📊 SASA（暴露度）— {len(pending_sasa)} 个待处理")
        t0 = time.time()

        # 批量提交
        batch = []
        for cid in pending_sasa:
            meta = meta_index[cid]
            batch.append({
                "pdb_content": pdb_index[cid],
                "peptide_id": cid,
                "sequence": meta["peptide_sequence"],
                "chain_id": "A",
            })

        result = await client.predict_pdb_batch("sasa", batch)
        if result.get("success") and result.get("results"):
            raw_scores = []
            for r in result["results"]:
                cid = r.get("peptide_id", "unknown")
                score = r.get("score")
                label = r.get("label")
                sasa_results.append({
                    "construct_id": cid,
                    "sasa_score": score,
                    "sasa_label": label,
                    "sasa_details": r.get("details", {}),
                })
                done_sasa.add(cid)
                if score is not None:
                    raw_scores.append(score)

            stats = (f"min={min(raw_scores):.3f}, max={max(raw_scores):.3f}, "
                     f"mean={sum(raw_scores)/len(raw_scores):.3f}") if raw_scores else "无"
            log(f"  SASA 耗时: {time.time()-t0:.1f}s, 有效: {len(raw_scores)}/{len(pending_sasa)}, {stats}")

            # 保存原始结果
            sasa_dir = make_dir("sasa")
            write_json(sasa_dir, "all_results.json", sasa_results)
            save_checkpoint(checkpoint_path, done_sasa, done_a3d, sasa_results, a3d_results)
        else:
            log(f"  ⚠ SASA 失败: {result.get('error', '未知')}")
    else:
        log(f"\nSASA 全部已完成 ({len(done_sasa)})")

    # ══════════════════════════════════════════════════════════════════
    # Aggrescan3D 评分
    # ══════════════════════════════════════════════════════════════════
    pending_a3d = [c for c in all_cids if c not in done_a3d]
    if pending_a3d:
        log(f"\n📊 Aggrescan3D（聚集风险）— {len(pending_a3d)} 个待处理")
        log(f"  并发 {A3D_CONCURRENCY}, 预计 ~{len(pending_a3d) * 5 // A3D_CONCURRENCY} 分钟")
        t0 = time.time()

        sem = asyncio.Semaphore(A3D_CONCURRENCY)
        lock = asyncio.Lock()

        async def predict_a3d(cid: str) -> dict:
            meta = meta_index[cid]
            async with sem:
                log(f"  ▶ {cid} ({meta['peptide_id']}, {meta['position']})")
                t1 = time.time()
                result = await client.predict_pdb_single(
                    "aggrescan3d",
                    pdb_index[cid],
                    peptide_id=cid,
                    chain_id="A",
                )
                elapsed = time.time() - t1
                success = result.get("success", False)
                score = None
                label = None
                if success and result.get("result"):
                    score = result["result"].get("score")
                    label = result["result"].get("label")
                    log(f"  ✓ {cid}  done in {elapsed:.0f}s, risk={score}")
                else:
                    log(f"  ✗ {cid}  failed ({elapsed:.0f}s): {result.get('error', '未知')}")

                entry = {
                    "construct_id": cid,
                    "aggrescan3d_score": score,
                    "aggrescan3d_label": label,
                    "elapsed": round(elapsed, 1),
                    "success": success,
                }

                async with lock:
                    a3d_results.append(entry)
                    done_a3d.add(cid)
                    if len(done_a3d) % 5 == 0:
                        save_checkpoint(checkpoint_path, done_sasa, done_a3d,
                                        sasa_results, a3d_results)
                        log(f"  📝 检查点 ({len(done_a3d)}/{len(all_cids)})")
                return entry

        tasks = [predict_a3d(cid) for cid in pending_a3d]
        await asyncio.gather(*tasks)
        save_checkpoint(checkpoint_path, done_sasa, done_a3d, sasa_results, a3d_results)

        a3d_dir = make_dir("aggrescan3d")
        write_json(a3d_dir, "all_results.json", a3d_results)

        elapsed_a3d = time.time() - t0
        log(f"  Aggrescan3D 总耗时: {elapsed_a3d:.0f}s")
    else:
        log(f"\nAggrescan3D 全部已完成 ({len(done_a3d)})")

    await client.close()
    total_time = time.time() - start_time

    # ══════════════════════════════════════════════════════════════════
    # 综合排名
    # ══════════════════════════════════════════════════════════════════
    log("\n" + "=" * 60)
    log("📊 综合排名")
    log("=" * 60)

    # 构建分数索引
    sasa_index = {r["construct_id"]: r for r in sasa_results}
    a3d_index = {r["construct_id"]: r for r in a3d_results}

    combined: list[dict] = []
    for cid in all_cids:
        meta = meta_index[cid]
        s = sasa_index.get(cid, {})
        a = a3d_index.get(cid, {})

        sasa_score = s.get("sasa_score")
        a3d_score = a.get("aggrescan3d_score")
        composite = meta["composite_score"]

        # 综合分 = composite * 0.6 + SASA * 0.4（SASA 有效时）
        # Aggrescan3D 作为参考不参与加权
        final_score = None
        if sasa_score is not None and composite is not None:
            final_score = round(
                composite * WEIGHT_COMPOSITE + sasa_score * WEIGHT_SASA, 4
            )

        combined.append({
            "construct_id": cid,
            "peptide_id": meta["peptide_id"],
            "linker_id": meta["linker_id"],
            "position": meta["position"],
            "length": meta["length"],
            "composite_score": composite,
            "sasa_score": sasa_score,
            "sasa_label": s.get("sasa_label"),
            "aggrescan3d_score": a3d_score,
            "aggrescan3d_label": a.get("aggrescan3d_label"),
            "final_score": final_score,
        })

    df = pd.DataFrame(combined)
    df = df.sort_values("final_score", ascending=False, na_position="last")
    df["rank"] = range(1, len(df) + 1)

    final_dir = make_dir("final")
    df.to_csv(final_dir / "all_ranked.csv", index=False)
    log(f"综合排名已保存: {final_dir / 'all_ranked.csv'}")

    # Top 10
    top10 = df.head(10)
    log(f"\n  Top 10:")
    for _, r in top10.iterrows():
        log(f"    {r['rank']:2d}. {r['construct_id']:12s} | {r['peptide_id']:12s} | "
            f"{r['position']:5s} | composite={r['composite_score']:.4f} | "
            f"SASA={r['sasa_score']:.4f} | final={r['final_score']:.4f}")

    # 统计
    n_sasa_ok = sum(1 for r in combined if r["sasa_score"] is not None)
    n_a3d_ok = sum(1 for r in combined if r["aggrescan3d_score"] is not None)

    # SASA 分布
    sasa_vals = [r["sasa_score"] for r in combined if r["sasa_score"] is not None]
    a3d_vals = [r["aggrescan3d_score"] for r in combined if r["aggrescan3d_score"] is not None]

    sasa_stats = ""
    if sasa_vals:
        buried = sum(1 for r in combined if r["sasa_label"] == "buried")
        partial = sum(1 for r in combined if r["sasa_label"] == "partial")
        exposed = sum(1 for r in combined if r["sasa_label"] == "exposed")
        sasa_stats = (
            f"min={min(sasa_vals):.3f}, max={max(sasa_vals):.3f}, "
            f"mean={sum(sasa_vals)/len(sasa_vals):.3f}"
            f"  | buried={buried}, partial={partial}, exposed={exposed}"
        )

    a3d_stats = ""
    if a3d_vals:
        a3d_stats = (
            f"min={min(a3d_vals):.3f}, max={max(a3d_vals):.3f}, "
            f"mean={sum(a3d_vals)/len(a3d_vals):.3f}"
        )

    log(f"\n  SASA: {n_sasa_ok}/{len(all_cids)} 有效 — {sasa_stats}")
    log(f"  Aggrescan3D: {n_a3d_ok}/{len(all_cids)} 有效 — {a3d_stats}")

    write_readme(all_cids, sasa_stats, a3d_stats, top10, n_sasa_ok, n_a3d_ok, total_time)
    write_status(all_cids, n_sasa_ok, n_a3d_ok, sasa_vals, a3d_vals, total_time)

    log(f"\n✅ 阶段六完成！耗时: {total_time:.0f}s")


def save_checkpoint(path: Path, done_sasa: set[str], done_a3d: set[str],
                    sasa_results: list[dict], a3d_results: list[dict]):
    data = {
        "done_sasa": sorted(done_sasa),
        "done_a3d": sorted(done_a3d),
        "sasa_results": sasa_results,
        "a3d_results": a3d_results,
        "timestamp": datetime.now().isoformat(),
    }
    write_json(path.parent, path.name, data)


def write_readme(all_cids, sasa_stats, a3d_stats, top10, n_sasa, n_a3d, elapsed):
    top10_lines = "\n".join(
        f"| {r['rank']} | {r['construct_id']} | {r['peptide_id']} | "
        f"{r['position']} | {r['composite_score']:.4f} | "
        f"{r['sasa_score'] or 'N/A':} | {r['final_score'] or 'N/A'} |"
        for _, r in top10.iterrows()
    )

    readme = f"""# 阶段六：PDB 评估 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.0f} 秒

## 评估服务

| 服务 | 作用 | 评分方向 | 有效数 |
|------|------|----------|--------|
| SASA | 功能肽表面暴露度 | 越高越好 | {n_sasa}/{len(all_cids)} |
| Aggrescan3D | 全长结构聚集风险 | 越低越好 | {n_a3d}/{len(all_cids)} |

## 综合评分

| 维度 | 权重 |
|------|------|
| 原有综合分（肽 + SoDoPE） | {WEIGHT_COMPOSITE} |
| SASA 暴露度 | {WEIGHT_SASA} |

Aggrescan3D 作为参考信息不参与加权。

## SASA 统计

{sasa_stats}

## Aggrescan3D 统计

{a3d_stats}

## Top 10 综合排名

| 排名 | Construct | 肽 | 位置 | Composite | SASA | 最终分 |
|------|-----------|-----|------|-----------|------|--------|
{top10_lines}

## 输出

- `sasa/all_results.json` — SASA 原始结果
- `aggrescan3d/all_results.json` — Aggrescan3D 原始结果（如已运行）
- `final/all_ranked.csv` — 综合排名

## Pipeline 状态

全部六阶段完成。最终 construct 列表可用于实验验证。
"""
    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {readme_path}")


def write_status(all_cids, n_sasa, n_a3d, sasa_vals, a3d_vals, elapsed):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    sasa_mean = f"{sum(sasa_vals)/len(sasa_vals):.4f}" if sasa_vals else "N/A"
    a3d_mean = f"{sum(a3d_vals)/len(a3d_vals):.4f}" if a3d_vals else "N/A"

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
| 5 | 3D 预测 (ESMFold) | ✅ 完成 | 90 → **90** PDB |
| 6 | PDB 评估 | {'✅' if n_sasa == len(all_cids) else '⏳ 进行中'} | 90 construct → **全部完成** |

## 阶段六：PDB 评估

**SASA**: {n_sasa}/{len(all_cids)} 有效, mean={sasa_mean}
**Aggrescan3D**: {n_a3d}/{len(all_cids)} 有效, mean={a3d_mean}
**耗时**: {elapsed:.0f}s

## 最终输出

- `stage06_pdb_eval/final/all_ranked.csv` — 综合排名
- `stage06_pdb_eval/sasa/` — SASA 逐条结果
- `stage06_pdb_eval/aggrescan3d/` — Aggrescan3D 结果（如已运行）
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
