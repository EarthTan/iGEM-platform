"""
Round 6：PDB 评估 — SASA + Aggrescan3D + 最终综评

对 Round 5 生成的全部 construct PDB 运行 SASA + Aggrescan3D，计算 Round 6 最终评分。
输出 sasa_ranking.csv（修复原脚本文件名不一致的 bug）。

与原脚本差异：
  - 输出到 output2/
  - 使用 common.py 共享工具
  - 修复输出文件名：sasa_ranking.csv（原 round07 读 final_ranked_sasa.csv 的 bug）
  - 使用 docker_utils 确保 SASA + Aggrescan3D 就绪

用法：
    uv run python -m main.stages2.round06_pdb_eval

输入：
    output2/round05_3d/final/round6_input.json
    output2/round05_3d/constructs/con_XXXX/

输出：
    output2/round06_pdb_eval/
    ├── final/sasa_ranking.csv          ← 全部 construct 排名（修复后的文件名）
    ├── final/score_distribution.json
    ├── raw/sasa_results.json
    ├── raw/aggrescan3d_results.json
    ├── README.md
    └── run.log
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient
from main.stages2.common import (
    OUTPUT_DIR, describe_dict, log, make_dir, read_json, setup_stage, write_json,
)

STAGE = "round06_pdb_eval"
STAGE_DIR = OUTPUT_DIR / STAGE
FINAL_DIR = STAGE_DIR / "final"
RAW_DIR = STAGE_DIR / "raw"

# ── 评分权重 ──
W_pLDDT = 0.20
W_SASA = 0.40
W_AGG = 0.40

# ── 并发 ──
SASA_CONCURRENCY = 10
AGGRESCAN_CONCURRENCY = 2


def print_distribution(name: str, stats: dict):
    """打印分布到终端。"""
    if stats.get("n", 0) == 0:
        log(f"  {name}: 无有效数据")
        return
    log(f"  {name}: mean={stats['mean']:.4f}, median={stats['median']:.4f}, "
        f"std={stats['std']:.4f}, range=[{stats['min']:.4f}, {stats['max']:.4f}]")


# ═══════════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════════

def load_constructs() -> list[dict]:
    """加载全部 construct（含 channel 标签）。"""
    input_path = OUTPUT_DIR / "round05_3d" / "final" / "round6_input.json"
    construct_list = read_json(input_path)["results"]

    constructs = []
    for entry in construct_list:
        cid = entry["construct_id"]
        con_dir = OUTPUT_DIR / "round05_3d" / "constructs" / cid

        meta = read_json(con_dir / "metadata.json")
        scores = read_json(con_dir / "scores.json")

        pdb_path = con_dir / f"{cid}_omegafold.pdb"
        if not pdb_path.exists():
            log(f"  ⚠ {cid}: OmegaFold PDB 缺失，跳过")
            continue

        constructs.append({
            "construct_id": cid,
            "channel": meta.get("channel", entry.get("channel", "top")),
            "peptide_id": meta["peptide_id"],
            "peptide_sequence": meta["peptide_sequence"],
            "position": meta["position"],
            "linker_id": meta["linker_id"],
            "pdb_path": pdb_path,
            "peptide_composite": scores.get("peptide_composite"),
            "construct_composite": scores.get("construct_composite"),
            "sodope_score": scores.get("sodope"),
            "temstapro_score": scores.get("temstapro_construct"),
            "construct_anoxpepred": scores.get("construct_anoxpepred"),
            "construct_bepipred3": scores.get("construct_bepipred3"),
            "anox_change_ratio": scores.get("anox_change_ratio"),
            "omegafold_plddt": scores.get("structure", {}).get("omegafold", {}).get("plddt"),
            "round3_services": scores.get("round3_services", {}),
            "sasa_score": None,
            "sasa_label": None,
            "sasa_details": None,
            "aggrisk_score": None,
            "aggrisk_label": None,
            "aggrisk_details": None,
            "round6_score": None,
        })

    return constructs


# ═══════════════════════════════════════════════════════════════════════
# SASA + Aggrescan3D
# ═══════════════════════════════════════════════════════════════════════

async def call_sasa_batch(constructs: list[dict]) -> None:
    import httpx
    url = "http://127.0.0.1:8101/predict/batch"
    sem = asyncio.Semaphore(SASA_CONCURRENCY)
    results: list[tuple[str, dict | None]] = []

    async def call_one(c: dict) -> tuple[str, dict | None]:
        cid = c["construct_id"]
        pdb_content = c["pdb_path"].read_text(encoding="utf-8")
        payload = {
            "pdb_content": pdb_content,
            "peptide_id": cid,
            "sequence": c["peptide_sequence"],
            "chain_id": "A",
        }
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(url, json={"requests": [payload]})
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("results"):
                            return cid, data["results"][0]
                    return cid, None
            except Exception as e:
                log(f"  ⚠ SASA {cid} 失败: {e}")
                return cid, None

    tasks = [call_one(c) for c in constructs]
    batch_results = await asyncio.gather(*tasks)

    for cid, result in batch_results:
        for c in constructs:
            if c["construct_id"] == cid and result and result.get("success", True):
                c["sasa_score"] = result.get("score")
                c["sasa_label"] = result.get("label")
                c["sasa_details"] = result.get("details")

    raw = {cid: result for cid, result in batch_results}
    write_json(RAW_DIR / "sasa_results.json", raw)
    ok = sum(1 for c in constructs if c["sasa_score"] is not None)
    log(f"  SASA 完成: {ok}/{len(constructs)}")


async def call_aggrescan3d_batch(constructs: list[dict]) -> None:
    import httpx
    url = "http://127.0.0.1:8102/predict/batch"
    sem = asyncio.Semaphore(AGGRESCAN_CONCURRENCY)
    results: list[tuple[str, dict | None]] = []

    async def call_one(c: dict) -> tuple[str, dict | None]:
        cid = c["construct_id"]
        pdb_content = c["pdb_path"].read_text(encoding="utf-8")
        payload = {
            "pdb_content": pdb_content,
            "peptide_id": cid,
            "sequence": None,
            "chain_id": "A",
        }
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(url, json={"requests": [payload]})
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("results"):
                            return cid, data["results"][0]
                    return cid, None
            except Exception as e:
                log(f"  ⚠ Aggrescan3D {cid} 失败: {e}")
                return cid, None

    tasks = [call_one(c) for c in constructs]
    batch_results = await asyncio.gather(*tasks)

    for cid, result in batch_results:
        for c in constructs:
            if c["construct_id"] == cid and result and result.get("success", True):
                c["aggrisk_score"] = result.get("score")
                c["aggrisk_label"] = result.get("label")
                c["aggrisk_details"] = result.get("details")

    raw = {cid: result for cid, result in batch_results}
    write_json(RAW_DIR / "aggrescan3d_results.json", raw)
    ok = sum(1 for c in constructs if c["aggrisk_score"] is not None)
    log(f"  Aggrescan3D 完成: {ok}/{len(constructs)}")


# ═══════════════════════════════════════════════════════════════════════
# 评分计算
# ═══════════════════════════════════════════════════════════════════════

def compute_scores(constructs: list[dict]) -> list[dict]:
    """计算 Round 6 综合评分并排序。"""
    plddt_vals = [c["omegafold_plddt"] for c in constructs if c["omegafold_plddt"] is not None]
    sasa_vals = [c["sasa_score"] for c in constructs if c["sasa_score"] is not None]
    agg_vals = [c["aggrisk_score"] for c in constructs if c["aggrisk_score"] is not None]

    plddt_min = min(plddt_vals) if plddt_vals else 0
    plddt_max = max(plddt_vals) if plddt_vals else 1
    plddt_range = plddt_max - plddt_min if plddt_max > plddt_min else 1

    log(f"\n  归一化基准: pLDDT min={plddt_min:.4f}, max={plddt_max:.4f}")

    scored = []
    for c in constructs:
        plddt_norm = (c["omegafold_plddt"] - plddt_min) / plddt_range if c["omegafold_plddt"] is not None else 0.5
        sasa = c["sasa_score"] if c["sasa_score"] is not None else 0.0
        agg_inv = 1.0 - (c["aggrisk_score"] if c["aggrisk_score"] is not None else 0.5)

        round6 = W_SASA * sasa + W_AGG * agg_inv + W_pLDDT * plddt_norm

        c["plddt_norm"] = round(plddt_norm, 4)
        c["agg_inv"] = round(agg_inv, 4)
        c["round6_score"] = round(round6, 4)
        scored.append(c)

    scored.sort(key=lambda x: x["round6_score"] or 0, reverse=True)
    for i, c in enumerate(scored):
        c["round6_rank"] = i + 1

    return scored


# ═══════════════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════════════

CSV_FIELDS = [
    "round6_rank", "channel", "construct_id", "peptide_id", "peptide_sequence",
    "position", "linker_id",
    "round6_score", "sasa_score", "sasa_label", "aggrisk_score", "aggrisk_label", "agg_inv",
    "omegafold_plddt", "plddt_norm",
    "construct_composite", "construct_anoxpepred", "construct_bepipred3",
    "sodope_score", "temstapro_score", "anox_change_ratio",
]


def save_csv(scored: list[dict], path: Path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for c in scored:
            w.writerow(c)


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def main():
    t0 = time.time()
    setup_stage(STAGE)
    make_dir(STAGE_DIR, "final")
    make_dir(STAGE_DIR, "raw")

    log("=" * 60)
    log("Round 6：PDB 评估 — SASA + Aggrescan3D")
    log("=" * 60)

    # 1. 加载
    log("\n📂 加载 construct 数据...")
    constructs = load_constructs()
    log(f"  已加载 {len(constructs)} 个 construct")

    # 2. SASA
    log("\n🔬 调用 SASA...")
    await call_sasa_batch(constructs)

    # 3. Aggrescan3D
    log("\n🧬 调用 Aggrescan3D...")
    await call_aggrescan3d_batch(constructs)

    # 4. 评分
    log("\n📊 计算 Round 6 评分...")
    scored = compute_scores(constructs)

    # 5. 分布统计
    log("\n📈 分数分布:")
    distributions = {}
    for key in ["construct_composite", "omegafold_plddt", "sasa_score",
                "aggrisk_score", "round6_score"]:
        vals = [c.get(key) for c in scored if c.get(key) is not None]
        if vals:
            dist = describe_dict(key, vals)
            distributions[key] = dist
            print_distribution(key, dist)

    write_json(FINAL_DIR / "score_distribution.json", distributions)

    # 6. 保存 sasa_ranking.csv（修复后的文件名，原脚本输出 all_ranked.csv 但 round07 读 final_ranked_sasa.csv）
    csv_path = FINAL_DIR / "sasa_ranking.csv"
    save_csv(scored, csv_path)
    log(f"\n✅ sasa_ranking.csv 已保存: {csv_path}")

    # 7. Top/Bottom 展示
    log(f"\nTop 5:")
    for c in scored[:5]:
        log(f"  {c['round6_rank']}. {c['construct_id']:12s} | "
            f"sasa={c['sasa_score']:.4f} agg={c['aggrisk_score']:.4f} "
            f"plddt={c['omegafold_plddt']:.4f} | round6={c['round6_score']:.4f}")

    log(f"\nBottom 5:")
    for c in scored[-5:]:
        log(f"  {c['round6_rank']}. {c['construct_id']:12s} | "
            f"sasa={c['sasa_score']:.4f} agg={c['aggrisk_score']:.4f} "
            f"plddt={c['omegafold_plddt']:.4f} | round6={c['round6_score']:.4f}")

    # 8. README
    elapsed = time.time() - t0
    _write_readme(scored, distributions, elapsed)
    log(f"\n✅ Round 6 完成！耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")


def _write_readme(scored, dist, elapsed):
    top5 = scored[:5]
    dist_lines = []
    for key, label in [
        ("construct_composite", "construct_composite（肽功能+SoDoPE+TemStaPro）"),
        ("omegafold_plddt", "OmegaFold pLDDT"),
        ("sasa_score", "SASA 功能肽暴露度"),
        ("aggrisk_score", "Aggrescan3D 聚集风险"),
        ("round6_score", "Round 6 综合评分"),
    ]:
        s = dist.get(key)
        if s and s.get("n", 0) > 0:
            dist_lines.append(f"- **{label}**: mean={s['mean']:.4f}, median={s['median']:.4f}, "
                              f"range=[{s['min']:.4f}, {s['max']:.4f}]")

    readme = f"""# Round 6：PDB 评估 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)
**Construct 数**: {len(scored)}

## 评分公式

```
round6_score = {W_SASA} * SASA_exposure
             + {W_AGG} * (1 - aggrisk)
             + {W_pLDDT} * pLDDT_norm
```

## 分数分布

{chr(10).join(dist_lines)}

## Top 5

| 排名 | Construct | 肽 | 分量 | Round6 |
|------|-----------|-----|------|--------|
{chr(10).join(
    f"| {c['round6_rank']} | {c['construct_id']} | {c['peptide_id']} | "
    f"cc={c['construct_composite']:.3f} sasa={c['sasa_score']:.3f} agg={c['aggrisk_score']:.3f} | **{c['round6_score']:.4f}** |"
    for c in top5
)}

## 输出

- `final/sasa_ranking.csv` — 全部 {len(scored)} 个 construct 排名（含 channel 标签）
- `final/score_distribution.json` — 各分数维度分布统计
- `raw/sasa_results.json` — SASA 原始返回
- `raw/aggrescan3d_results.json` — Aggrescan3D 原始返回
"""
    (STAGE_DIR / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
