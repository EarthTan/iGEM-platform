"""
阶段三：精确评分（TemStaPro 热稳定性 + 重排名）

读取阶段二的 top 80，调用 TemStaPro 补充热稳定性评分，
重新加权排名后输出 top K 条肽进入枚举阶段（K=3 → 54 construct）。

用法：
    .venv/bin/python -m main.stages.stage03_exactscore

输入：
    output/stage02_score/final/top80.csv

输出：
    output/stage03_exactscore/README.md     ← 完整报告
    output/stage03_exactscore/final/        ← top K 肽 + 枚举输入
    output/status/status_*.md              ← 状态快照
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE = "stage03_exactscore"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient

LOG_FILE: Path | None = None

# ── 评分配置（阶段二基础上 + TemStaPro 0.05）──
SCORING_SERVICES = [
    ("anoxpepred",  0.50, False, "抗氧化活性"),
    ("bepipred3",   0.20, False, "B 细胞表位"),
    ("plm4cpps",    0.15, False, "细胞穿膜"),
    ("mhcflurry",   0.10, True,  "MHC-I 亲和力（反向）"),
    ("graphcpp",    0.05, False, "细胞穿膜 (GNN)"),
    ("temstapro",   0.05, False, "热稳定性"),
]

# 枚举参数
TOP_K = 3                # 进入枚举的肽条数
NUM_LINKERS = 6          # 6 种 Linker
NUM_POSITIONS = 3        # 3 种位置方案
TARGET_CONSTRUCTS = TOP_K * NUM_LINKERS * NUM_POSITIONS  # 54


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
    log("阶段三：精确评分（TemStaPro 热稳定性 + 重排名）")
    log("=" * 60)

    # ── 读取阶段二的 top 80 ──
    input_path = OUTPUT_DIR / "stage02_score" / "final" / "top80.csv"
    if not input_path.exists():
        log(f"❌ 找不到输入文件: {input_path}")
        log("请先运行阶段二: python -m main.stages.stage02_score")
        return

    df = pd.read_csv(input_path)
    log(f"输入: {len(df)} 条肽 (来自阶段二 top80)")

    peptides = df.to_dict("records")
    client = ServiceClient(timeout=120.0)

    # ══════════════════════════════════════════════════════════════════
    # 调用 TemStaPro
    # ══════════════════════════════════════════════════════════════════

    log(f"\n📊 temstapro (热稳定性, 权重=0.05)")
    t0 = time.time()

    batch = [{"sequence": p["sequence"], "peptide_id": p.get("peptide_id", f"idx_{i}")}
             for i, p in enumerate(peptides)]
    result = await client.predict_batch("temstapro", batch)

    temstapro_scores: dict[str, float | None] = {}
    service_error = None

    if not result.get("success") or not result.get("results"):
        log(f"  ⚠ 调用失败: {result.get('error', '未知错误')}")
        service_error = result.get("error")
        # TemStaPro 失败时所有分数记为 None
        for p in peptides:
            temstapro_scores[p.get("peptide_id", "")] = None
    else:
        raw_scores: list[float] = []
        for r in result["results"]:
            pid = r.get("peptide_id", "unknown")
            s = r.get("score")
            temstapro_scores[pid] = s
            if s is not None:
                raw_scores.append(s)

        stats = (f"min={min(raw_scores):.3f}, max={max(raw_scores):.3f}, "
                 f"mean={sum(raw_scores)/len(raw_scores):.3f}") if raw_scores else "无有效分数"
        log(f"  耗时: {time.time()-t0:.1f}s, 有效: {len(raw_scores)}/{len(peptides)}, {stats}")

        svc_dir = make_dir("temstapro")
        write_json(svc_dir, "raw_result.json", result)

    # ══════════════════════════════════════════════════════════════════
    # 重算加权分（含 TemStaPro）
    # ══════════════════════════════════════════════════════════════════

    log("\n" + "=" * 60)
    log("🧮 重算加权综合分（含 TemStaPro）")
    log("=" * 60)

    results_rows: list[dict] = []
    for i, pep in enumerate(peptides):
        pid = pep.get("peptide_id", f"idx_{i}")
        row = {
            "peptide_id": pid,
            "sequence": pep["sequence"],
            "temstapro": temstapro_scores.get(pid),
        }

        weighted_sum = 0.0
        total_weight = 0.0
        missing_services = []

        for service_name, weight, reverse, desc in SCORING_SERVICES:
            # 从阶段二的 CSV 读取已有的分数
            if service_name == "temstapro":
                raw_score = temstapro_scores.get(pid)
            else:
                raw_score = pep.get(service_name)  # 来自 top80.csv 的列

            row[service_name] = raw_score

            if raw_score is None:
                missing_services.append(service_name)
                continue

            normalized = max(0.0, min(1.0, raw_score))
            if reverse:
                normalized = 1.0 - normalized

            weighted_sum += normalized * weight
            total_weight += weight

        if missing_services:
            row["missing_services"] = ";".join(missing_services)

        if total_weight > 0:
            row["weighted_score"] = round(weighted_sum / total_weight, 4)
        else:
            row["weighted_score"] = None

        # 对比：不含 TemStaPro 的旧分（来自阶段二）
        row["old_weighted_score"] = pep.get("weighted_score")

        results_rows.append(row)

    df_results = pd.DataFrame(results_rows)

    # 检查排名变化
    df_results = df_results.sort_values("weighted_score", ascending=False, na_position="last")
    df_results["rank"] = range(1, len(df_results) + 1)
    df_results["rank_change"] = df_results.apply(
        lambda r: r.get("old_rank", 0) - r["rank"]
        if pd.notna(r.get("old_rank")) else 0,
        axis=1,
    )

    # ── 保存完整评分 ──
    score_dir = make_dir("scores")
    df_results.to_csv(score_dir / "all_ranked.csv", index=False)
    write_json(score_dir, "all_ranked.json", results_rows)

    # ══════════════════════════════════════════════════════════════════
    # 输出 top K 肽 → 枚举
    # ══════════════════════════════════════════════════════════════════

    final_dir = make_dir("final")
    n_top = min(TOP_K, len(df_results))
    df_top_k = df_results.head(n_top).copy()
    df_top_k.to_csv(final_dir / f"top{TOP_K}.csv", index=False)
    log(f"Top {TOP_K} 肽已保存: {final_dir / f'top{TOP_K}.csv'}")

    # 保存枚举配置供 stage 4 使用
    enum_config = {
        "stage": "stage03_exactscore",
        "timestamp": datetime.now().isoformat(),
        "top_k": TOP_K,
        "num_linkers": NUM_LINKERS,
        "num_positions": NUM_POSITIONS,
        "target_constructs": TARGET_CONSTRUCTS,
        "peptides": [
            {"peptide_id": r["peptide_id"], "sequence": r["sequence"],
             "weighted_score": r["weighted_score"]}
            for r in df_top_k.to_dict("records")
        ],
    }
    write_json(final_dir, "enum_input.json", enum_config)
    log(f"枚举配置已保存: {final_dir / 'enum_input.json'}")

    # 服务可用性
    all_svc_names = [s[0] for s in SCORING_SERVICES]
    temstapro_ok = service_error is None
    available = [s for s in all_svc_names
                 if s != "temstapro" or temstapro_ok]
    unavailable = [s for s in all_svc_names if s not in available]

    total_time = time.time() - start_time

    # 排名变化摘要
    if "old_rank" in df_results.columns:
        n_changed = (df_results["rank_change"] != 0).sum()
        rank_summary = f"  {n_changed} 条肽排名发生变化"
    else:
        rank_summary = "  （无旧排名数据对比）"

    # top 5 摘要
    top5 = df_results.head(5)[["rank", "peptide_id", "weighted_score", "sequence"]].to_dict("records")
    score_stats = "\n".join(
        f"    {r['rank']}. {r['peptide_id']}  score={r['weighted_score']:.4f}  {r['sequence'][:30]}"
        for r in top5
    )

    log(f"\n📊 阶段三汇总")
    log(f"  输入: {len(peptides)} 条 (阶段二 top80)")
    log(f"  有效评分: {df_results['weighted_score'].notna().sum()} 条")
    log(f"  TemStaPro: {'✅' if temstapro_ok else '❌'}")
    log(f"  输出: {n_top} 条肽 → 枚举 ({TARGET_CONSTRUCTS} construct)")
    log(f"  {rank_summary}")
    log(f"  耗时: {total_time:.1f}s")
    log(f"\n  Top 5:\n{score_stats}")

    # ── 写 README ──
    write_readme(df_results, temstapro_ok, total_time)

    # ── 写 STATUS ──
    write_status(df_results, n_top, available, unavailable, temstapro_ok)

    await client.close()


def write_readme(df: pd.DataFrame, temstapro_ok: bool, elapsed: float):
    """写入阶段三完整报告。"""
    top5 = df.head(5)[["rank", "peptide_id", "weighted_score", "sequence"]].to_dict("records")
    top5_lines = "\n".join(
        f"| {r['rank']} | {r['peptide_id']} | {r['weighted_score']:.4f} | `{r['sequence'][:40]}` |"
        for r in top5
    )

    top3 = df.head(3)[["rank", "peptide_id", "weighted_score", "sequence"]].to_dict("records")
    top3_lines = "\n".join(
        f"| {r['rank']} | {r['peptide_id']} | {r['weighted_score']:.4f} | `{r['sequence'][:40]}` |"
        for r in top3
    )

    # TemStaPro 分数统计
    ts_scores = df["temstapro"].dropna()
    ts_stats = ""
    if len(ts_scores) > 0:
        ts_stats = (f"min={ts_scores.min():.3f}, max={ts_scores.max():.3f}, "
                    f"mean={ts_scores.mean():.3f}")

    readme = f"""# 阶段三：精确评分 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.1f} 秒

## 更新内容

在阶段二（5 服务评分）基础上，补充 TemStaPro 热稳定性评分（权重 0.05），
重算加权综合分并重新排名。

## 评分服务（含阶段二已有）

| 服务 | 权重 | 方向 | 状态 |
|------|------|------|------|
| AnOxPePred | 0.50 | 正向 | ✅ |
| BepiPred-3.0 | 0.20 | 正向 | ✅ |
| pLM4CPPs | 0.15 | 正向 | ✅ |
| MHCflurry | 0.10 | 反向 | ✅ |
| GraphCPP | 0.05 | 正向 | ✅ |
| **TemStaPro** | **0.05** | **正向** | **{"✅" if temstapro_ok else "❌"}** |

## TemStaPro 分数

{ts_stats if ts_stats else "N/A"}

## 最终 Top 5

| 排名 | ID | 综合分 | 序列 |
|------|-----|--------|------|
{top5_lines}

## 进入枚举的 Top {min(3, len(df))}

| 排名 | ID | 综合分 | 序列 |
|------|-----|--------|------|
{top3_lines}

将依次与 6 种 Linker × 3 种位置方案组合 → 共 {TOP_K * NUM_LINKERS * NUM_POSITIONS} 个 construct。

## 输出

- `scores/all_ranked.csv` — 全部重排名结果（含 TemStaPro）
- `final/top{TOP_K}.csv` — Top {TOP_K} 肽（进入枚举）
- `final/enum_input.json` — 枚举阶段输入配置
- `temstapro/raw_result.json` — TemStaPro 原始返回
"""

    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {readme_path}")


def write_status(df: pd.DataFrame, n_top: int,
                 available: list, unavailable: list, temstapro_ok: bool):
    """写入 pipeline 状态快照。"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    top3 = df.head(3)[["rank", "peptide_id", "weighted_score"]].to_dict("records")
    top3_lines = "\n".join(
        f"  {r['rank']}. {r['peptide_id']}  score={r['weighted_score']:.4f}"
        for r in top3
    )

    status = f"""# 🧬 Pipeline 状态

**更新**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**焦点**: 抗氧化肽

---

## 全局进度

| # | 阶段 | 状态 | 输入 → 输出 |
|---|------|------|-------------|
| 1 | 硬过滤 | ✅ 完成 | 1843 → **107** 条 |
| 2 | 快速评分 + 排序 | ✅ 完成 | 107 → **80** 条 |
| 3 | 精确评分 | ✅ 完成 | 80 → **{n_top}** 条 |
| 4 | 枚举 | ⏳ 待开始 | {n_top} → **{n_top * NUM_LINKERS * NUM_POSITIONS}** construct |
| 5 | 3D 预测 | ⏳ 待开始 | — |
| 6 | PDB 评估 | ⏳ 待开始 | — |

## 阶段三：精确评分

**TemStaPro**: {'✅' if temstapro_ok else '❌'}
**可用服务**: {', '.join(available)}
{"**不可用**: " + ', '.join(unavailable) if unavailable else ""}

**综合排名 top 3（进入枚举）**:
{top3_lines}

枚举参数: K={TOP_K}, Linker={NUM_LINKERS}, 位置={NUM_POSITIONS} → {n_top * NUM_LINKERS * NUM_POSITIONS} construct

**输出**: `stage03_exactscore/final/top{TOP_K}.csv`

详见: `stage03_exactscore/README.md`

## 下一步

阶段四（枚举），输入: `stage03_exactscore/final/enum_input.json`
目标: {n_top * NUM_LINKERS * NUM_POSITIONS} 个 construct
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
