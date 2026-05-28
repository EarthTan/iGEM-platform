"""
阶段一：硬过滤（多轮筛选）

加载抗氧化肽数据，依次经过 ToxinPred3（毒性）、AlgPred2（致敏性）、
HemoPI2（溶血性）三轮硬过滤，任一超标直接淘汰。

用法：
    .venv/bin/python -m main.stages.stage01_filter

输出：
    output/stage01_filter/README.md   ← 完整报告
    output/stage01_filter/final/      ← 过滤结果
    output/STATUS.md                  ← pipeline 状态锚点
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── 路径 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"

sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient

STAGE = "stage01_filter"
STAGE_DIR = OUTPUT_DIR / STAGE


# ═══════════════════════════════════════════════════════════════════════════
# 第一步：加载并合并数据
# ═══════════════════════════════════════════════════════════════════════════

def load_and_merge_data() -> pd.DataFrame:
    """
    加载 function.csv + function_3.csv，聚焦抗氧化肽，去重并清洗。
    """
    # 加载 function.csv
    df1 = pd.read_csv(DATA_DIR / "function.csv")
    df1 = df1[df1["is_antioxidant"] == 1].copy()
    df1["source"] = "function.csv"
    log(f"function.csv  抗氧化肽: {len(df1)} 条")

    # 加载 function_3.csv（全部是抗氧化肽）
    df2 = pd.read_csv(DATA_DIR / "function_3.csv")
    df2["source"] = "function_3.csv"
    log(f"function_3.csv          : {len(df2)} 条")

    # 合并：以 function.csv 为主，追加 function_3 中不重复的
    existing = set(df1["sequence"].str.upper().str.strip())
    df2_new = df2[~df2["sequence"].str.upper().str.strip().isin(existing)].copy()
    log(f"function_3 新增不重复  : {len(df2_new)} 条")

    combined = pd.concat([df1, df2_new], ignore_index=True)
    log(f"合并后总计              : {len(combined)} 条")

    # 清洗：只保留标准氨基酸序列
    import re
    std_aa = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$", re.IGNORECASE)
    valid = combined["sequence"].str.upper().str.strip().str.match(std_aa)
    n_invalid = (~valid).sum()
    combined = combined[valid].copy()
    log(f"非标准氨基酸序列已排除  : {n_invalid} 条")
    log(f"最终可用                : {len(combined)} 条")

    return combined


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════

LOG_FILE: Path | None = None


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def make_round_dir(name: str) -> Path:
    d = STAGE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_results(dir_path: Path, results: list[dict]):
    """将微服务返回的完整结果写入 JSON。"""
    with open(dir_path / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def write_csv(dir_path: Path, df: pd.DataFrame, filename: str):
    path = dir_path / filename
    df.to_csv(path, index=False)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# 单轮过滤
# ═══════════════════════════════════════════════════════════════════════════

MAX_BATCH_SIZE = 1000


async def run_filter_round(
    client: ServiceClient,
    service_name: str,
    peptides: list[dict],
    threshold: float,
    higher_is_toxic: bool = True,
) -> tuple[list[dict], list[dict]]:
    """
    调用微服务进行单轮过滤。

    自动按 MAX_BATCH_SIZE 拆分成多个请求，聚合结果。
    higher_is_toxic=True  : score >= threshold → 淘汰
    higher_is_toxic=False : score <= threshold → 淘汰

    返回 (passed, failed) ，每个元素是 {peptide_id, sequence, score, label}。
    """
    passed: list[dict] = []
    failed: list[dict] = []
    chunk_errors = 0

    # 拆分成 ≤1000 条的块
    for start_idx in range(0, len(peptides), MAX_BATCH_SIZE):
        chunk = peptides[start_idx:start_idx + MAX_BATCH_SIZE]
        batch = [{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk]

        result = await client.predict_batch(service_name, batch)

        if not result.get("success") or not result.get("results"):
            chunk_errors += 1
            log(f"  ⚠ {service_name} 批次 {start_idx//MAX_BATCH_SIZE + 1} 失败: {result.get('error', '未知错误')}")
            # 安全优先：失败的批次全部淘汰
            for p in chunk:
                failed.append({"peptide_id": p["peptide_id"], "sequence": p["sequence"],
                               "score": None, "label": "SERVICE_UNAVAILABLE"})
            continue

        for r in result["results"]:
            pid = r.get("peptide_id", "unknown")
            seq = r.get("sequence", "")
            score = r.get("score")
            label = r.get("label", "")

            entry = {"peptide_id": pid, "sequence": seq, "score": score, "label": label}

            if score is None:
                failed.append(entry)
            elif higher_is_toxic and score >= threshold:
                failed.append(entry)
            elif not higher_is_toxic and score <= threshold:
                failed.append(entry)
            else:
                passed.append(entry)

    if chunk_errors:
        log(f"  ⚠ {service_name}: {chunk_errors} 个批次失败，这些批次全部淘汰")
    return passed, failed


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

async def run():
    global LOG_FILE
    start_time = time.time()

    # ── 创建输出目录 ──
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = STAGE_DIR / "run.log"
    log("=" * 60)
    log("阶段一：硬过滤 — 多轮筛选")
    log("=" * 60)

    # ── 加载数据 ──
    log("\n📦 加载数据...")
    df = load_and_merge_data()
    df["peptide_id"] = [f"pep_{i:05d}" for i in range(len(df))]
    total_start = len(df)

    # 转换为列表，方便传给 client
    all_peptides = df.to_dict("records")

    # ── 初始化微服务客户端 ──
    client = ServiceClient(timeout=120.0)

    # ══════════════════════════════════════════════════════════════════
    # 轮次 1: ToxinPred3 — 毒性过滤
    # ══════════════════════════════════════════════════════════════════
    log("\n🔴 轮次 1: ToxinPred3 — 毒性过滤 (阈值 ≥0.38)")
    round1_dir = make_round_dir("round1_toxinpred3")
    t0 = time.time()

    passed_1, failed_1 = await run_filter_round(
        client, "toxinpred3", all_peptides, threshold=0.38, higher_is_toxic=True
    )

    write_results(round1_dir, {"passed": passed_1, "failed": failed_1})
    passed_ids_1 = {p["peptide_id"] for p in passed_1}
    df_passed_1 = df[df["peptide_id"].isin(passed_ids_1)].copy()
    df_failed_1 = df[~df["peptide_id"].isin(passed_ids_1)].copy()
    write_csv(round1_dir, df_passed_1, "passed.csv")
    write_csv(round1_dir, df_failed_1, "failed.csv")

    log(f"  提交: {len(all_peptides)} 条")
    log(f"  通过: {len(passed_1)} 条")
    log(f"  淘汰: {len(failed_1)} 条 (毒性)")
    log(f"  耗时: {time.time() - t0:.1f} 秒")

    # ══════════════════════════════════════════════════════════════════
    # 轮次 2: AlgPred2 — 致敏性过滤
    # ══════════════════════════════════════════════════════════════════
    log("\n🟠 轮次 2: AlgPred2 — 致敏性过滤 (阈值 ≥0.30)")
    round2_dir = make_round_dir("round2_algpred2")
    t0 = time.time()

    passed_2, failed_2 = await run_filter_round(
        client, "algpred2", passed_1, threshold=0.30, higher_is_toxic=True
    )

    write_results(round2_dir, {"passed": passed_2, "failed": failed_2})
    passed_ids_2 = {p["peptide_id"] for p in passed_2}
    df_passed_2 = df[df["peptide_id"].isin(passed_ids_2)].copy()
    df_failed_2 = df[~df["peptide_id"].isin(passed_ids_2)].copy()
    write_csv(round2_dir, df_passed_2, "passed.csv")
    write_csv(round2_dir, df_failed_2, "failed.csv")

    log(f"  提交: {len(passed_1)} 条")
    log(f"  通过: {len(passed_2)} 条")
    log(f"  淘汰: {len(failed_2)} 条 (致敏)")
    log(f"  耗时: {time.time() - t0:.1f} 秒")

    # ══════════════════════════════════════════════════════════════════
    # 轮次 3: HemoPI2 — 溶血性过滤
    # ══════════════════════════════════════════════════════════════════
    log("\n🟡 轮次 3: HemoPI2 — 溶血性过滤 (阈值 ≥0.55)")
    round3_dir = make_round_dir("round3_hemopi2")
    t0 = time.time()

    passed_3, failed_3 = await run_filter_round(
        client, "hemopi2", passed_2, threshold=0.55, higher_is_toxic=True
    )

    write_results(round3_dir, {"passed": passed_3, "failed": failed_3})
    passed_ids_3 = {p["peptide_id"] for p in passed_3}
    df_final = df[df["peptide_id"].isin(passed_ids_3)].copy()
    df_eliminated = df[~df["peptide_id"].isin(passed_ids_3)].copy()
    write_csv(round3_dir, df_final, "passed.csv")
    write_csv(round3_dir, df_eliminated, "failed.csv")

    log(f"  提交: {len(passed_2)} 条")
    log(f"  通过: {len(passed_3)} 条")
    log(f"  淘汰: {len(failed_3)} 条 (溶血)")
    log(f"  耗时: {time.time() - t0:.1f} 秒")

    # ══════════════════════════════════════════════════════════════════
    # 最终汇总
    # ══════════════════════════════════════════════════════════════════
    final_dir = STAGE_DIR / "final"
    final_dir.mkdir(exist_ok=True)
    write_csv(final_dir, df_final, "passed.csv")
    write_csv(final_dir, df_eliminated, "eliminated.csv")

    total_time = time.time() - start_time
    log("\n" + "=" * 60)
    log("📊 阶段一汇总")
    log("=" * 60)
    log(f"初始肽数       : {total_start} 条")
    log(f"ToxinPred3 淘汰: {len(failed_1)} 条 (毒性)")
    log(f"AlgPred2 淘汰  : {len(failed_2)} 条 (致敏)")
    log(f"HemoPI2 淘汰   : {len(failed_3)} 条 (溶血)")
    log(f"最终通过       : {len(df_final)} 条")
    log(f"总淘汰率       : {(1 - len(df_final)/total_start)*100:.1f}%")
    log(f"总耗时         : {total_time:.1f} 秒")

    # ── 写 README.md ──
    write_readme(STAGE_DIR, total_start, len(df_final),
                 len(failed_1), len(failed_2), len(failed_3),
                 total_time)

    # ── 写 STATUS.md ──
    write_status(len(df_final), total_start, STAGE_DIR,
                 n_toxic=len(failed_1), n_allergen=len(failed_2), n_hemolytic=len(failed_3))
    await client.close()


def write_readme(stage_dir: Path, total: int, final: int,
                 n_toxic: int, n_allergen: int, n_hemolytic: int,
                 elapsed: float):
    """写入阶段一的完整报告。"""
    readme = f"""# 阶段一：硬过滤 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.1f} 秒

## 数据源

- function.csv（抗氧化子集）+ function_3.csv
- 合并去重后共 {total} 条标准氨基酸序列

## 过滤流程

| 轮次 | 服务 | 阈值 | 淘汰数 | 剩余 |
|------|------|------|--------|------|
| 输入 | — | — | — | {total} |
| 1 | ToxinPred3（毒性） | ≥0.38 | {n_toxic} | {total - n_toxic} |
| 2 | AlgPred2（致敏） | ≥0.30 | {n_allergen} | {total - n_toxic - n_allergen} |
| 3 | HemoPI2（溶血） | ≥0.55 | {n_hemolytic} | {final} |

## 结果

- **通过**: {final} 条（{final/total*100:.1f}%）
- **淘汰**: {total - final} 条（{(total-final)/total*100:.1f}%）
- **输出**: `final/passed.csv`

## 淘汰明细

- 毒性: `round1_toxinpred3/failed.csv`
- 致敏: `round2_algpred2/failed.csv`
- 溶血: `round3_hemopi2/failed.csv`
"""
    with open(stage_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {stage_dir / 'README.md'}")


def write_status(passed: int, total: int, stage_dir: Path,
                 n_toxic: int = 0, n_allergen: int = 0, n_hemolytic: int = 0):
    """写入 pipeline 全局状态锚点（时间戳 + 最新指针）。"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    status = f"""# 🧬 Pipeline 状态

**更新**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**焦点**: 抗氧化肽
**分支**: deploy

---

## 全局进度

| # | 阶段 | 状态 | 输入 → 输出 | 耗时 |
|---|------|------|-------------|------|
| 1 | 硬过滤 | ✅ 完成 | {total} → **{passed}** 条 | 见详情 |
| 2 | 快速评分 + 排序 | ⏳ 待开始 | — | — |
| 3 | 精确评分 | ⏳ 待开始 | — | — |
| 4 | 枚举 | ⏳ 待开始 | — | — |
| 5 | 3D 结构预测 | ⏳ 待开始 | — | — |
| 6 | PDB 评估 + 报告 | ⏳ 待开始 | — | — |

## 阶段一：硬过滤

```
初始: {total} 条
  ├─ ToxinPred3 (≥0.38) → 淘汰 {n_toxic} 条 (毒性)
  ├─ AlgPred2   (≥0.30) → 淘汰 {n_allergen} 条 (致敏)
  └─ HemoPI2    (≥0.55) → 淘汰 {n_hemolytic} 条 (溶血)
最终: {passed} 条
```

详见: `{stage_dir.relative_to(OUTPUT_DIR)}/README.md`

## 配置

- 数据源: function.csv + function_3.csv（抗氧化）
- 序列清洗: 仅保留标准 20 种氨基酸
- 硬过滤阈值: ToxinPred3 ≥0.38 / AlgPred2 ≥0.30 / HemoPI2 ≥0.55
- 失败策略: 微服务不可用时安全优先（淘汰整批）

## 下一步

阶段二（快速评分 + 排序），输入: `{stage_dir.relative_to(OUTPUT_DIR)}/final/passed.csv`
"""
    with open(status_path, "w", encoding="utf-8") as f:
        f.write(status)

    # 同时更新最新指针
    latest_path = OUTPUT_DIR / "STATUS.md"
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(status)

    log(f"状态已写入: {status_path}")


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
