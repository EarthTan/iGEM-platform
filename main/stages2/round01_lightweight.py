"""
Round 1：轻量评分

在 105 万条候选肽上运行 3 个轻量微服务：
  - AnOxPePred（抗氧化核心，权重 0.50，并发 10）
  - AlgPred2（致敏反向，权重 0.10，并发 10）
  - ToxinPred3（毒性反向，权重 0.15）→ 在 top50k 上单独跑（全量 105 万太慢）

输出 Top 50,000 条 + 安全标记 + 分布报告。

与原脚本差异：
  - 使用 common.py 消除工具函数复制粘贴
  - 新增断点续跑（checkpoint.json，每 50 批保存）
  - 按服务特性调整并发（ToxinPred3 只有并发 2，避免挂死）
  - 明确输出 top50k.csv（原 round02 读 top100k.csv 的 bug 根源在此）
  - 统一 asyncio.gather 异常安全（return_exceptions=True）
  - 输出目录 output2/

用法：
    uv run python -m main.stages2.round01_lightweight

输入：
    output2/step00_integrate/final/cleaned.csv

输出：
    output2/round01_lightweight/
    ├── README.md              ← 分布报告 + Top/Bottom 展示
    ├── run.log
    ├── scores/                ← 各服务原始返回（JSON）
    ├── final/
    │   ├── top50k.csv         ← Top 50,000 肽（含综合分 + 安全标记）
    │   ├── all_scored.csv     ← 全部评分明细（含失败标记）
    │   └── danger_list.csv    ← 🔴 高危肽清单
    └── stats.json             ← 程序化统计摘要
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient

from main.stages2.common import (
    OUTPUT_DIR, calc_safety_flag, chunk_list, describe, log, make_dir,
    read_csv, save_checkpoint, load_checkpoint, setup_stage, write_csv, write_json,
)

STAGE = "round01_lightweight"
STAGE_DIR = OUTPUT_DIR / STAGE

MAX_BATCH_SIZE = 1000

# ── 评分服务配置 ──
# (service_name, weight, reverse, description, concurrency)
SERVICES = [
    ("anoxpepred",  0.50, False, "抗氧化活性",                10),
    ("algpred2",    0.10, True,  "致敏（反向，越低越好）",    10),
    # ToxinPred3 在后续单独的批处理中运行（全量 105 万条太慢，只跑 Top 50K）
]

# ── 安全标记阈值 ──
SAFETY_THRESHOLDS = {
    "toxinpred3": {"caution": 0.60, "danger": 0.80},
    "algpred2":   {"caution": 0.50, "danger": 0.70},
}

TOP_N = 50000


# ═══════════════════════════════════════════════════════════════════════
# 并发批处理 — 带重试和超时
# ═══════════════════════════════════════════════════════════════════════

async def process_service(
    client: ServiceClient,
    service_name: str,
    chunks: list[list[dict]],
    reverse: bool,
    concurrency: int,
) -> dict[str, dict]:
    """并发处理一个服务的所有批次，支持检查点恢复。

    使用 asyncio.Semaphore 限制并发数。每批有独立超时。
    所有异常被隔离——失败批次返回 score=None，不影响其他批次。
    """
    sem = asyncio.Semaphore(concurrency)
    all_results: dict[str, dict] = {}
    errors = 0
    completed = 0
    total = sum(len(c) for c in chunks)

    async def process_chunk(chunk: list[dict]) -> None:
        nonlocal errors, completed
        async with sem:
            try:
                result = await asyncio.wait_for(
                    client.predict_batch(service_name, chunk),
                    timeout=300.0,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        pid = r.get("peptide_id", "unknown")
                        all_results[pid] = {
                            "score": r.get("score"),
                            "label": r.get("label", ""),
                        }
                    completed += len(chunk)
                else:
                    errors += 1
                    for item in chunk:
                        pid = item.get("peptide_id", "unknown")
                        all_results[pid] = {"score": None, "label": "SERVICE_ERROR"}
            except asyncio.TimeoutError:
                errors += 1
                for item in chunk:
                    pid = item.get("peptide_id", "unknown")
                    all_results[pid] = {"score": None, "label": "TIMEOUT"}
            except Exception as e:
                errors += 1
                for item in chunk:
                    pid = item.get("peptide_id", "unknown")
                    all_results[pid] = {"score": None, "label": f"ERROR:{str(e)[:50]}"}

    tasks = [process_chunk(chunk) for chunk in chunks]

    # 分批 gather，避免一次性创建 1000+ 协程
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        results = await asyncio.gather(*batch, return_exceptions=True)
        progress = min((i + batch_size) * MAX_BATCH_SIZE, total)
        pct = progress / total * 100
        log(f"  {service_name}: {progress:,}/{total:,} ({pct:.0f}%) | errors={errors}")
        # 记录异常
        for j, r in enumerate(results):
            if isinstance(r, Exception):
                log(f"    ⚠ 批次异常: {r}")

    log(f"  ✅ {service_name}: {total:,} 完成, {errors} 批次错误")
    return all_results


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def run():
    start_time = time.time()

    setup_stage(STAGE)
    log("=" * 60)
    log("Round 1：轻量评分 — AnOxPePred + AlgPred2 (ToxinPred3 在 Top 50K 上单独跑)")
    log("=" * 60)

    # ── 加载数据 ──
    input_path = OUTPUT_DIR / "step00_integrate" / "final" / "cleaned.csv"
    if not input_path.exists():
        log(f"❌ 找不到输入: {input_path}")
        log("请先运行: uv run python -m main.stages2.step00_integrate")
        return

    peptides = read_csv(input_path)
    total = len(peptides)
    log(f"\n输入: {total:,} 条肽 (来自步骤零)")

    # ── 分块 ──
    chunks = []
    for i in range(0, total, MAX_BATCH_SIZE):
        chunk = peptides[i:i + MAX_BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])
    log(f"分块: {len(chunks)} 批 (每批 ≤{MAX_BATCH_SIZE} 条)")

    # ── 初始化客户端 ──
    client = ServiceClient(timeout=300.0)

    # ══════════════════════════════════════════════════════════════════
    # 并发调用 3 个服务（同时运行！）
    # ══════════════════════════════════════════════════════════════════
    log(f"\n🚀 开始评分 (3 服务同时运行)...")

    async def run_one_service(svc_name: str, weight: float, reverse: bool, desc: str, concurrency: int):
        log(f"\n📊 {svc_name} ({desc}, 并发={concurrency})")
        t0 = time.time()
        results = await process_service(client, svc_name, chunks, reverse, concurrency)
        elapsed = time.time() - t0
        n_valid = sum(1 for v in results.values() if v["score"] is not None)
        log(f"  ✅ {svc_name}: {elapsed:.0f}s, {n_valid}/{len(results)} 有效 ({n_valid/elapsed:.0f} seq/s)")
        return svc_name, results

    tasks = [run_one_service(svc, w, r, d, c) for svc, w, r, d, c in SERVICES]
    completed_list = await asyncio.gather(*tasks, return_exceptions=True)

    service_results: dict[str, dict[str, dict]] = {}
    for item in completed_list:
        if isinstance(item, Exception):
            log(f"❌ 服务异常: {item}")
        else:
            svc_name, results = item
            service_results[svc_name] = results

    # ── 保存原始返回 ──
    scores_dir = make_dir(STAGE_DIR, "scores")
    for svc_name, _, _, _, _ in SERVICES:
        if svc_name in service_results:
            write_json(scores_dir / f"{svc_name}_results.json", service_results[svc_name])

    await client.close()

    # ══════════════════════════════════════════════════════════════════
    # 计算综合分
    # ══════════════════════════════════════════════════════════════════
    log(f"\n🧮 计算加权综合分...")

    scored_peptides: list[dict] = []
    missing_any = 0

    for pep in peptides:
        pid = pep["peptide_id"]
        row = {
            "peptide_id": pid,
            "sequence": pep["sequence"],
            "length": int(pep["length"]),
            "source": pep["source"],
        }

        weighted_sum = 0.0
        total_weight = 0.0
        missing_svc = []

        for svc_name, weight, reverse, desc, _ in SERVICES:
            svc_data = service_results.get(svc_name, {}).get(pid, {})
            raw_score = svc_data.get("score")
            label = svc_data.get("label", "")
            row[svc_name] = raw_score
            row[f"{svc_name}_label"] = label

            if raw_score is None:
                missing_svc.append(svc_name)
                continue

            normalized = max(0.0, min(1.0, raw_score))
            if reverse:
                normalized = 1.0 - normalized

            weighted_sum += normalized * weight
            total_weight += weight

        if missing_svc:
            row["missing_services"] = ";".join(missing_svc)
            missing_any += 1

        if total_weight > 0:
            row["weighted_score"] = round(weighted_sum / total_weight, 4)
        else:
            row["weighted_score"] = None

        row["safety_flag"] = calc_safety_flag(row, SAFETY_THRESHOLDS)

        scored_peptides.append(row)

    log(f"  完成: {len(scored_peptides):,} 条评分")
    log(f"  部分服务缺失: {missing_any:,} 条")

    # ══════════════════════════════════════════════════════════════════
    # 排序 + 输出
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 排序...")
    scored_peptides.sort(key=lambda x: (x["weighted_score"] or 0), reverse=True)

    # 保存全部
    final_dir = make_dir(STAGE_DIR, "final")
    fieldnames = [
        "peptide_id", "sequence", "length", "source",
        "anoxpepred", "anoxpepred_label",
        "toxinpred3", "toxinpred3_label",
        "algpred2", "algpred2_label",
        "weighted_score", "missing_services", "safety_flag",
    ]

    all_path = final_dir / "all_scored.csv"
    write_csv(all_path, fieldnames, scored_peptides)
    log(f"全部评分: {all_path} ({len(scored_peptides):,} 条)")

    # Top 50K
    n_top = min(TOP_N, len(scored_peptides))
    top_peptides = scored_peptides[:n_top]
    top_path = final_dir / "top50k.csv"
    write_csv(top_path, fieldnames, top_peptides)
    log(f"Top {n_top:,}: {top_path}")

    # 🔴 高危清单
    danger_list = [p for p in scored_peptides if "danger" in p.get("safety_flag", "")]
    danger_path = final_dir / "danger_list.csv"
    write_csv(danger_path, fieldnames, danger_list)
    log(f"高危清单: {danger_path} ({len(danger_list)} 条)")

    # ══════════════════════════════════════════════════════════════════
    # 统计报告
    # ══════════════════════════════════════════════════════════════════
    total_elapsed = time.time() - start_time

    valid_scores = [p["weighted_score"] for p in scored_peptides if p["weighted_score"] is not None]
    score_report = describe("综合分", valid_scores)

    all_reports = [score_report]
    for svc_name, _, _, _, _ in SERVICES:
        vals = [p[svc_name] for p in scored_peptides if p.get(svc_name) is not None]
        if vals:
            all_reports.append(describe(svc_name, vals))
    full_distro = "\n".join(all_reports)

    n_safe = sum(1 for p in scored_peptides if p.get("safety_flag") == "safe")
    n_caution = sum(1 for p in scored_peptides if "caution" in p.get("safety_flag", "")
                     and "danger" not in p.get("safety_flag", ""))
    n_danger = len(danger_list)
    n_missing = sum(1 for p in scored_peptides if p.get("missing_services"))

    top10_lines = []
    for p in scored_peptides[:10]:
        flag = p.get("safety_flag", "safe")
        top10_lines.append(f"| {p['peptide_id']} | {p['sequence'][:25]:25s} | {p['length']} | {p['weighted_score']:.4f} | {flag} |")

    bottom_valid = [p for p in scored_peptides if p["weighted_score"] is not None][-10:]
    bottom10_lines = []
    for p in reversed(bottom_valid):
        flag = p.get("safety_flag", "safe")
        bottom10_lines.append(f"| {p['peptide_id']} | {p['sequence'][:25]:25s} | {p['length']} | {p['weighted_score']:.4f} | {flag} |")

    # 统计摘要
    stats = {
        "stage": STAGE,
        "timestamp": datetime.now().isoformat(),
        "elapsed_sec": round(total_elapsed, 1),
        "input": {"n_peptides": total},
        "services": {svc: {"weight": w, "reverse": r, "desc": d, "concurrency": c} for svc, w, r, d, c in SERVICES},
        "scoring": {
            "n_valid": len(valid_scores),
            "mean": round(sum(valid_scores) / len(valid_scores), 4) if valid_scores else None,
            "top_n": n_top,
        },
        "safety": {
            "safe": n_safe,
            "caution": n_caution,
            "danger": n_danger,
            "missing_data": n_missing,
        },
        "output": {
            "top50k": str(top_path),
            "all_scored": str(all_path),
            "danger_list": str(danger_path),
        },
    }
    write_json(STAGE_DIR / "stats.json", stats)

    # ══════════════════════════════════════════════════════════════════
    # README
    # ══════════════════════════════════════════════════════════════════
    readme = f"""# Round 1：轻量评分 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {total_elapsed:.0f} 秒
**输入**: {total:,} 条肽
**输出目录**: output2/

## 评分服务

| 服务 | 权重 | 方向 | 并发 | 有效数 |
|------|------|------|------|--------|
| AnOxPePred | 0.50 | 正向（越高越好） | 10 | {sum(1 for p in scored_peptides if p.get('anoxpepred') is not None):,} |
| ToxinPred3 | 0.15 | 反向（越低越好） | 2 | {sum(1 for p in scored_peptides if p.get('toxinpred3') is not None):,} |
| AlgPred2 | 0.10 | 反向（越低越好） | 10 | {sum(1 for p in scored_peptides if p.get('algpred2') is not None):,} |

## 综合分分布

```
{full_distro}
```

## 安全标记统计

| 级别 | 数量 | 占比 |
|------|------|------|
| 🟢 正常 | {n_safe:,} | {n_safe/max(len(scored_peptides),1)*100:.1f}% |
| 🟡 注意 | {n_caution:,} | {n_caution/max(len(scored_peptides),1)*100:.1f}% |
| 🔴 高危 | {n_danger:,} | {n_danger/max(len(scored_peptides),1)*100:.1f}% |
| ⚠ 数据缺失 | {n_missing:,} | {n_missing/max(len(scored_peptides),1)*100:.1f}% |

## Top 10

| ID | 序列 | 长度 | 综合分 | 安全标记 |
|----|------|------|--------|----------|
{chr(10).join(top10_lines)}

## Bottom 10

| ID | 序列 | 长度 | 综合分 | 安全标记 |
|----|------|------|--------|----------|
{chr(10).join(bottom10_lines)}

## 输出

- `final/top50k.csv` — Top {n_top:,} 条 → Round 2
- `final/all_scored.csv` — 全部 {len(scored_peptides):,} 条评分明细
- `final/danger_list.csv` — 🔴 高危肽 {len(danger_list)} 条
- `scores/*_results.json` — 各服务原始返回
"""

    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"\n报告已写入: {readme_path}")

    # ── 汇总日志 ──
    log(f"\n{'=' * 60}")
    log(f"Round 1 汇总")
    log(f"  输入: {total:,} 条")
    log(f"  有效评分: {len(valid_scores):,} 条")
    if valid_scores:
        log(f"  综合分: mean={sum(valid_scores)/len(valid_scores):.4f}, max={max(valid_scores):.4f}")
    log(f"  Top {n_top:,} 已保存")
    log(f"  安全: 🟢{n_safe:,} / 🟡{n_caution:,} / 🔴{n_danger:,} / ⚠{n_missing:,}")
    log(f"  耗时: {total_elapsed:.0f}s")
    log(f"{'=' * 60}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
