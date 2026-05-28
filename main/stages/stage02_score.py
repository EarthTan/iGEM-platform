"""
阶段二：快速评分 + 排序

读取阶段一过滤后的肽，调用多个评分微服务，加权排名。

用法：
    .venv/bin/python -m main.stages.stage02_score

输入：
    output/stage01_filter/final/passed.csv

输出：
    output/stage02_score/README.md     ← 完整报告
    output/stage02_score/final/        ← 排名结果
    output/status/status_*.md          ← 状态快照
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
STAGE = "stage02_score"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient

LOG_FILE: Path | None = None


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


# ═══════════════════════════════════════════════════════════════════════════
# 评分配置
# ═══════════════════════════════════════════════════════════════════════════

# (服务名, 权重, 是否反向, 描述)
SCORING_SERVICES = [
    ("anoxpepred",  0.50, False, "抗氧化活性"),
    ("bepipred3",   0.20, False, "B 细胞表位"),
    ("plm4cpps",    0.15, False, "细胞穿膜"),
    ("mhcflurry",   0.10, True,  "MHC-I 亲和力（反向，越低越好）"),
    ("graphcpp",    0.05, False, "细胞穿膜 (GNN)"),
]


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

async def run():
    global LOG_FILE
    start_time = time.time()
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = STAGE_DIR / "run.log"

    log("=" * 60)
    log("阶段二：快速评分 + 排序")
    log("=" * 60)

    # ── 读取阶段一的输出 ──
    input_path = OUTPUT_DIR / "stage01_filter" / "final" / "passed.csv"
    if not input_path.exists():
        log(f"❌ 找不到输入文件: {input_path}")
        log("请先运行阶段一: python -m main.stages.stage01_filter")
        return

    df = pd.read_csv(input_path)
    log(f"输入: {len(df)} 条肽 (来自阶段一)")

    peptides = df.to_dict("records")
    client = ServiceClient(timeout=120.0)

    # ══════════════════════════════════════════════════════════════════
    # 调用所有评分服务
    # ══════════════════════════════════════════════════════════════════

    service_scores: dict[str, dict] = {}  # service_name → {peptide_id → score}
    service_errors: list[dict] = []

    for service_name, weight, reverse, desc in SCORING_SERVICES:
        log(f"\n📊 {service_name} ({desc}, 权重={weight})")
        t0 = time.time()

        batch = [{"sequence": p["sequence"], "peptide_id": p.get("peptide_id", f"idx_{i}")}
                 for i, p in enumerate(peptides)]
        result = await client.predict_batch(service_name, batch)

        if not result.get("success") or not result.get("results"):
            log(f"  ⚠ 调用失败: {result.get('error', '未知错误')}")
            service_errors.append({"service": service_name, "error": result.get("error")})
            # 服务失败时，该服务的所有分数记为 None（不参与评分）
            service_scores[service_name] = {}
            continue

        scores: dict[str, float | None] = {}
        raw_scores: list[float] = []
        for r in result["results"]:
            pid = r.get("peptide_id", "unknown")
            s = r.get("score")
            scores[pid] = s
            if s is not None:
                raw_scores.append(s)

        service_scores[service_name] = scores
        stats = f"min={min(raw_scores):.3f}, max={max(raw_scores):.3f}, mean={sum(raw_scores)/len(raw_scores):.3f}" if raw_scores else "无有效分数"
        log(f"  耗时: {time.time()-t0:.1f}s, 有效: {len(raw_scores)}/{len(scores)}, {stats}")

        # 保存原始返回值
        svc_dir = make_dir(service_name)
        write_json(svc_dir, "raw_result.json", result)

    # ══════════════════════════════════════════════════════════════════
    # 加权评分
    # ══════════════════════════════════════════════════════════════════

    log("\n" + "=" * 60)
    log("🧮 计算加权综合分")
    log("=" * 60)

    results_rows: list[dict] = []
    for i, pep in enumerate(peptides):
        pid = pep.get("peptide_id", f"idx_{i}")
        row = {"peptide_id": pid, "sequence": pep["sequence"],
               **{svc: None for svc, _, _, _ in SCORING_SERVICES}}

        weighted_sum = 0.0
        total_weight = 0.0
        missing_services = []

        for service_name, weight, reverse, desc in SCORING_SERVICES:
            svc_scores = service_scores.get(service_name, {})
            raw_score = svc_scores.get(pid)
            row[service_name] = raw_score

            if raw_score is None:
                missing_services.append(service_name)
                continue

            # 归一化到 0-1（大部分服务已经在这个范围）
            normalized = max(0.0, min(1.0, raw_score))

            # 反向服务：分数越低越好
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

        results_rows.append(row)

    df_results = pd.DataFrame(results_rows)
    df_results = df_results.sort_values("weighted_score", ascending=False, na_position="last")
    df_results["rank"] = range(1, len(df_results) + 1)

    # ── 保存完整评分结果 ──
    score_dir = make_dir("scores")
    df_results.to_csv(score_dir / "all_ranked.csv", index=False)
    write_json(score_dir, "all_ranked.json", results_rows)

    # ══════════════════════════════════════════════════════════════════
    # 输出
    # ══════════════════════════════════════════════════════════════════

    final_dir = make_dir("final")

    # 取前 80 条（默认上限）
    top_n = min(80, len(df_results))
    df_top = df_results.head(top_n).copy()
    df_top.to_csv(final_dir / "top80.csv", index=False)

    # 如果不足 80 条，全部输出
    df_results.to_csv(final_dir / "all_ranked.csv", index=False)

    # 服务状态汇总
    available = [s for s, _, _, _ in SCORING_SERVICES
                 if service_scores.get(s) and any(v is not None for v in service_scores[s].values())]
    unavailable = [s for s, _, _, _ in SCORING_SERVICES
                   if s not in available]

    total_time = time.time() - start_time

    # 分数分布摘要
    scores_valid = df_results["weighted_score"].dropna()
    score_stats = ""
    if len(scores_valid) > 0:
        top5 = df_results.head(5)[["rank", "peptide_id", "weighted_score", "sequence"]].to_dict("records")
        score_stats = "\n".join(
            f"    {r['rank']}. {r['peptide_id']}  score={r['weighted_score']:.4f}  {r['sequence'][:30]}"
            for r in top5
        )

    log(f"\n📊 阶段二汇总")
    log(f"  输入: {len(peptides)} 条")
    log(f"  有效评分: {len(scores_valid)} 条")
    log(f"  可用服务: {len(available)}/{len(SCORING_SERVICES)}")
    if unavailable:
        log(f"  不可用: {unavailable}")
    log(f"  输出: {top_n} 条排名 (top80.csv)")
    log(f"  耗时: {total_time:.1f}s")
    log(f"\n  排名前 5:\n{score_stats}")

    # ── 写 README ──
    write_readme(df_results, available, unavailable, service_errors, total_time)

    # ── 写 STATUS ──
    write_status(len(df_results), len(peptides), top_n, available, unavailable)

    await client.close()


def write_readme(df: pd.DataFrame, available: list, unavailable: list,
                 errors: list[dict], elapsed: float):
    """写入阶段二的完整报告。"""
    top5 = df.head(5)[["rank", "peptide_id", "weighted_score", "sequence"]].to_dict("records")
    top5_lines = "\n".join(
        f"| {r['rank']} | {r['peptide_id']} | {r['weighted_score']:.4f} | `{r['sequence'][:40]}` |"
        for r in top5
    )

    # 分数统计
    valid = df["weighted_score"].dropna()
    distro = ""
    if len(valid) > 0:
        bins = [(0.8, 1.0), (0.6, 0.8), (0.4, 0.6), (0.2, 0.4), (0.0, 0.2)]
        parts = []
        for lo, hi in bins:
            cnt = ((valid >= lo) & (valid < hi)).sum()
            parts.append(f"  {lo:.1f}-{hi:.1f}: {cnt} 条")
        distro = "\n".join(parts)

    readme = f"""# 阶段二：评分 + 排名 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.1f} 秒

## 评分服务

| 服务 | 权重 | 方向 | 状态 |
|------|------|------|------|
| AnOxPePred | 0.50 | 正向（越高越好） | {"✅" if "anoxpepred" in available else "❌"} |
| BepiPred-3.0 | 0.20 | 正向 | {"✅" if "bepipred3" in available else "❌"} |
| pLM4CPPs | 0.15 | 正向 | {"✅" if "plm4cpps" in available else "❌"} |
| MHCflurry | 0.10 | 反向（越低越好） | {"✅" if "mhcflurry" in available else "❌"} |
| GraphCPP | 0.05 | 正向 | {"✅" if "graphcpp" in available else "❌"} |

## 评分分布

加权综合分分布（{len(valid)} 条有效）：
{distro}

## TOP 5

| 排名 | ID | 综合分 | 序列 |
|------|-----|--------|------|
{top5_lines}

## 服务错误

{"无" if not errors else "\n".join(f"- {e['service']}: {e.get('error', '未知')}" for e in errors)}

## 输出

- `scores/all_ranked.csv` — 全部肽的评分明细
- `final/top80.csv` — 前 80 条（输入阶段三）
- `final/all_ranked.csv` — 全部排名
"""

    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {readme_path}")


def write_status(total_ranked: int, input_count: int, top_n: int,
                 available: list, unavailable: list):
    """写入 pipeline 状态快照。"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    status = f"""# 🧬 Pipeline 状态

**更新**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**焦点**: 抗氧化肽

---

## 全局进度

| # | 阶段 | 状态 | 输入 → 输出 |
|---|------|------|-------------|
| 1 | 硬过滤 | ✅ 完成 | 1843 → **107** 条 |
| 2 | 评分 + 排序 | ✅ 完成 | {input_count} → **{top_n}** 条 |
| 3 | 精确评分 | ⏳ 待开始 | — |
| 4 | 枚举 | ⏳ 待开始 | — |
| 5 | 3D 预测 | ⏳ 待开始 | — |
| 6 | PDB 评估 | ⏳ 待开始 | — |

## 阶段二：评分 + 排名

**可用服务**: {', '.join(available)}
{"**不可用**: " + ', '.join(unavailable) if unavailable else ""}
**输出**: `stage02_score/final/top80.csv`（前 80 条）

详见: `stage02_score/README.md`

## 配置

- 评分权重: AnOxPePred 0.50 / BepiPred-3.0 0.20 / pLM4CPPs 0.15 / MHCflurry 0.10(反向) / GraphCPP 0.05
- TIPred: 已移除（酪氨酸酶抑制非抗氧化必备属性）
- SoDoPE: 移到阶段六（construct 级别溶解度）

## 下一步

阶段三（精确评分），输入: `stage02_score/final/top80.csv`
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
