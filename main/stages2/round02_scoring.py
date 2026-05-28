"""
Round 2：抗氧化分选 + 追加评分

按纯抗氧化性（AnOxPePred 原始分）从 105 万条中选出：
  - Top 25K（抗氧化活性最好的）
  - Bottom 25K（抗氧化活性最差的，作阴性对照）

然后在 50K 上跑 3 个安全微服务：
  - ToxinPred3（毒性反向，并发 2）
  - HemoPI2（溶血反向，并发 10）
  - MHCflurry（MHC-I 反向，并发 10）

保留 channel 标签（top/bottom）贯穿后续所有 round。

用法：
    uv run python -m main.stages2.round02_scoring

输入：
    output2/round01_lightweight/final/all_scored.csv  （105 万条全部评分）

输出：
    output2/round02_scoring/
    ├── README.md
    ├── run.log
    ├── scores/
    ├── final/
    │   ├── all_50k.csv       ← 全部 50K（top25K + bottom25K，含各服务分 + channel）
    │   ├── top25k.csv
    │   └── bottom25k.csv
    └── stats.json
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
    OUTPUT_DIR, calc_safety_flag, log, make_dir, read_csv, setup_stage, write_csv, write_json,
)

STAGE = "round02_scoring"
STAGE_DIR = OUTPUT_DIR / STAGE

MAX_BATCH_SIZE = 1000

# ── Round 2 追加服务（ToxinPred3 未在 Round 1 跑全量，在此对 50K 跑）──
# (service_name, description, concurrency)
SERVICES = [
    ("toxinpred3", "毒性（反向，越低越好）", 2),  # sklearn 单线程
    ("hemopi2",    "溶血（反向，越低越好）", 10),
    ("mhcflurry",  "MHC-I 结合（反向，越低越好）", 10),
]

TOP_N = 25000
BOTTOM_N = 25000

# ── 完整 5 服务权重（给后续 round 用，Round 2 本身不排序）──
ALL_WEIGHTS = {
    "anoxpepred":  0.50,
    "toxinpred3":  0.15,
    "algpred2":    0.10,
    "hemopi2":     0.10,
    "mhcflurry":   0.05,
}

ROUND1_INPUT = OUTPUT_DIR / "round01_lightweight" / "final" / "all_scored.csv"


def load_and_select_peptides() -> list[dict]:
    """加载全部评分，按 anoxpepred 排序，取 top25K + bottom25K。"""
    peptides = read_csv(ROUND1_INPUT)
    total = len(peptides)
    log(f"加载 Round 1 全部评分: {total:,} 条")

    # 数值转换
    for p in peptides:
        p["anoxpepred"] = float(p["anoxpepred"]) if p.get("anoxpepred") else None
        p["algpred2"] = float(p["algpred2"]) if p.get("algpred2") else None
        p["length"] = int(p["length"])

    # 筛选有有效 anoxpepred 分数的
    valid = [p for p in peptides if p["anoxpepred"] is not None]
    log(f"有效 anoxpepred 分数: {len(valid):,}/{total:,}")

    # 按 anoxpepred 降序排序
    valid.sort(key=lambda x: x["anoxpepred"], reverse=True)

    # 取 top 25K
    top = valid[:TOP_N]
    for p in top:
        p["channel"] = "top"

    # 取 bottom 25K（从末尾取）
    bottom = valid[-BOTTOM_N:]
    for p in bottom:
        p["channel"] = "bottom"

    selected = top + bottom
    log(f"Top 25K (anoxpepred >= {top[-1]['anoxpepred']:.4f})")
    log(f"Bottom 25K (anoxpepred <= {bottom[0]['anoxpepred']:.4f})")

    return selected


async def process_service(
    client: ServiceClient,
    service_name: str,
    chunks: list[list[dict]],
    concurrency: int = 10,
) -> dict[str, dict]:
    sem = asyncio.Semaphore(concurrency)
    all_results: dict[str, dict] = {}
    errors = 0
    total = sum(len(c) for c in chunks)

    async def process_chunk(chunk: list[dict]) -> None:
        nonlocal errors
        async with sem:
            try:
                result = await asyncio.wait_for(
                    client.predict_batch(service_name, chunk),
                    timeout=300.0,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        pid = r.get("peptide_id", "unknown")
                        all_results[pid] = {"score": r.get("score"), "label": r.get("label", "")}
                else:
                    errors += 1
                    for item in chunk:
                        pid = item.get("peptide_id", "unknown")
                        all_results[pid] = {"score": None, "label": "SERVICE_ERROR"}
            except asyncio.TimeoutError:
                errors += 1
                for item in chunk:
                    all_results[item.get("peptide_id", "unknown")] = {"score": None, "label": "TIMEOUT"}
            except Exception as e:
                errors += 1
                for item in chunk:
                    all_results[item.get("peptide_id", "unknown")] = {"score": None, "label": f"ERROR:{str(e)[:50]}"}

    tasks = [process_chunk(chunk) for chunk in chunks]
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        progress = min((i + batch_size) * MAX_BATCH_SIZE, total)
        log(f"  {service_name}: {progress:,}/{total:,} ({progress/total*100:.0f}%) | errors={errors}")

    log(f"  ✅ {service_name}: {total:,} 完成, {errors} 批次错误")
    return all_results


async def run():
    start_time = time.time()
    setup_stage(STAGE)
    log("=" * 60)
    log("Round 2：抗氧化分选 + ToxinPred3 / HemoPI2 / MHCflurry")
    log("=" * 60)

    # ── 加载 + 分选 ──
    if not ROUND1_INPUT.exists():
        log(f"❌ 输入不存在: {ROUND1_INPUT}")
        return

    peptides = load_and_select_peptides()
    total = len(peptides)
    log(f"分选结果: {total:,} 条 (top={sum(1 for p in peptides if p['channel']=='top')}, "
        f"bottom={sum(1 for p in peptides if p['channel']=='bottom')})")

    # ── 分块 ──
    chunks = []
    for i in range(0, total, MAX_BATCH_SIZE):
        chunk = peptides[i:i + MAX_BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])
    log(f"分块: {len(chunks)} 批 (≤{MAX_BATCH_SIZE}/批)")

    # ── 并发调用 3 个服务 ──
    client = ServiceClient(timeout=300.0)
    svc_names = [s[0] for s in SERVICES]
    log(f"\n开始评分: {', '.join(svc_names)}")

    async def run_one(svc_name: str, desc: str, concurrency: int):
        log(f"\n{svc_name} ({desc}, 并发={concurrency})")
        t0 = time.time()
        results = await process_service(client, svc_name, chunks, concurrency)
        elapsed = time.time() - t0
        n_valid = sum(1 for v in results.values() if v["score"] is not None)
        rate = n_valid / elapsed if elapsed > 0 else 0
        log(f"  ✅ {svc_name}: {elapsed:.0f}s, {n_valid}/{len(results)} 有效 ({rate:.0f} seq/s)")
        return svc_name, results

    tasks = [run_one(svc, d, c) for svc, d, c in SERVICES]
    completed_list = await asyncio.gather(*tasks, return_exceptions=True)
    await client.close()

    service_results: dict[str, dict[str, dict]] = {}
    for item in completed_list:
        if isinstance(item, Exception):
            log(f"❌ 服务异常: {item}")
        else:
            svc_name, results = item
            service_results[svc_name] = results

    # ── 保存原始返回 ──
    scores_dir = make_dir(STAGE_DIR, "scores")
    for svc_name in service_results:
        write_json(scores_dir / f"{svc_name}_results.json", service_results[svc_name])

    # ── 合并分数（保持 channel 标签）──
    log(f"\n合并分数...")

    fieldnames = [
        "peptide_id", "sequence", "length", "source", "channel",
        "anoxpepred", "anoxpepred_label",
        "algpred2", "algpred2_label",
        "toxinpred3", "toxinpred3_label",
        "hemopi2", "hemopi2_label",
        "mhcflurry", "mhcflurry_label",
        "weighted_score", "safety_flag",
    ]

    for pep in peptides:
        pid = pep["peptide_id"]
        for svc_name in [s[0] for s in SERVICES]:
            svc_data = service_results.get(svc_name, {}).get(pid, {})
            pep[svc_name] = svc_data.get("score")
            pep[f"{svc_name}_label"] = svc_data.get("label", "")

    # ── 计算加权综合分（固定公式，供下游全部使用）──
    # weighted_score = Σ(normalized_i × weight_i) / Σ(weight_i)
    #   正向 (anoxpepred): normalized = clamp(raw, 0, 1)
    #   反向 (toxinpred3, algpred2, hemopi2, mhcflurry): normalized = 1 - clamp(raw, 0, 1)
    REVERSE_SERVICES = {"toxinpred3", "algpred2", "hemopi2", "mhcflurry"}
    log(f"\n计算加权综合分...")

    for pep in peptides:
        weighted_sum = 0.0
        total_weight = 0.0
        for svc_name, weight in ALL_WEIGHTS.items():
            raw = pep.get(svc_name)
            if raw is None:
                continue
            normalized = max(0.0, min(1.0, raw))
            if svc_name in REVERSE_SERVICES:
                normalized = 1.0 - normalized
            weighted_sum += normalized * weight
            total_weight += weight

        pep["weighted_score"] = round(weighted_sum / total_weight, 4) if total_weight > 0 else None

    # ── 安全标记 ──
    # 阈值固定记录在此，供所有下游 round 使用
    safety_thresholds = {
        "toxinpred3": {"caution": 0.60, "danger": 0.80},
        "algpred2":   {"caution": 0.50, "danger": 0.70},
        "hemopi2":    {"caution": 0.70, "danger": 0.85},
    }
    for pep in peptides:
        pep["safety_flag"] = calc_safety_flag(pep, safety_thresholds)

    # ── 输出 ──
    final_dir = make_dir(STAGE_DIR, "final")

    all_path = final_dir / "all_50k.csv"
    write_csv(all_path, fieldnames, peptides)
    log(f"全部 50K: {all_path}")

    top_peptides = [p for p in peptides if p["channel"] == "top"]
    bottom_peptides = [p for p in peptides if p["channel"] == "bottom"]

    top_path = final_dir / "top25k.csv"
    write_csv(top_path, fieldnames, top_peptides)
    log(f"Top 25K: {top_path}")

    bottom_path = final_dir / "bottom25k.csv"
    write_csv(bottom_path, fieldnames, bottom_peptides)
    log(f"Bottom 25K: {bottom_path}")

    # ── 统计 ──
    total_elapsed = time.time() - start_time

    top_anox = [p["anoxpepred"] for p in top_peptides if p["anoxpepred"] is not None]
    bot_anox = [p["anoxpepred"] for p in bottom_peptides if p["anoxpepred"] is not None]

    stats = {
        "stage": STAGE,
        "timestamp": datetime.now().isoformat(),
        "elapsed_sec": round(total_elapsed, 1),
        "selection": {
            "source": str(ROUND1_INPUT),
            "criterion": "anoxpepred (pure antioxidant, descending)",
            "top_n": TOP_N,
            "bottom_n": BOTTOM_N,
            "top_anoxpepred_range": f"{top_anox[-1]:.4f} ~ {top_anox[0]:.4f}" if top_anox else "N/A",
            "bottom_anoxpepred_range": f"{bot_anox[-1]:.4f} ~ {bot_anox[0]:.4f}" if bot_anox else "N/A",
        },
        "services": {s[0]: {"desc": s[1], "concurrency": s[2]} for s in SERVICES},
        "output": {
            "all_50k": str(all_path),
            "top25k": str(top_path),
            "bottom25k": str(bottom_path),
        },
    }
    write_json(STAGE_DIR / "stats.json", stats)

    # ── README ──
    n_top_valid = sum(1 for p in top_peptides if p.get("toxinpred3") is not None)
    n_bot_valid = sum(1 for p in bottom_peptides if p.get("toxinpred3") is not None)

    readme = f"""# Round 2：抗氧化分选 + 追加评分 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {total_elapsed:.0f} 秒
**输入**: Round 1 全部 1,055,116 条评分
**输出目录**: output2/

## 分选策略

按纯抗氧化活性（AnOxPePred 原始分）从高到低排序：
- **Top 25K**: 抗氧化活性最好的 25,000 条
- **Bottom 25K**: 抗氧化活性最差的 25,000 条（阴性对照）

> ⚠️ 注意：Round 1 的加权综合分（混合了 AlgPred2）被丢弃，
> 改用纯 anoxpepred 分进行分选。

## 追加评分

在 50K 上跑 3 个安全服务：

| 服务 | 用途 | 并发 | Top 25K 有效 | Bottom 25K 有效 |
|------|------|------|-------------|----------------|
| ToxinPred3 | 毒性反向 | 2 | {n_top_valid:,} | {n_bot_valid:,} |
| HemoPI2 | 溶血反向 | 10 | | |
| MHCflurry | MHC-I 反向 | 10 | | |

## 抗氧化活性范围

- **Top 25K**: {top_anox[0]:.4f} ~ {top_anox[-1]:.4f}
- **Bottom 25K**: {bot_anox[-1]:.4f} ~ {bot_anox[0]:.4f}

## 输出

| 文件 | 说明 |
|------|------|
| `final/all_50k.csv` | 全部 50K（top25K + bottom25K，含 channel 标签）→ Round 3 输入 |
| `final/top25k.csv` | 仅 Top 25K |
| `final/bottom25k.csv` | 仅 Bottom 25K |
| `scores/*_results.json` | 各服务原始返回 |
"""
    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"\n报告: {readme_path}")

    log(f"\n{'=' * 60}")
    log(f"Round 2 汇总")
    log(f"  Top 25K: anoxpepred {top_anox[0]:.4f} ~ {top_anox[-1]:.4f}")
    log(f"  Bottom 25K: anoxpepred {bot_anox[-1]:.4f} ~ {bot_anox[0]:.4f}")
    log(f"  追加服务: {', '.join(svc_names)}")
    log(f"  耗时: {total_elapsed:.0f}s")
    log(f"{'=' * 60}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
