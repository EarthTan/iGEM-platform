"""
阶段四：枚举 + Construct 级 FASTA 评分（分组排序）

枚举全部 construct → SoDoPE 评分 → 按（肽, Linker）分组，组内取最高分排序
→ 选 Top K 组 → 每组 3 个 position 全部输出进 3D 结构预测。

排序逻辑：不再将每个 position 的 construct 单独排序，而是按功能肽分组。
这样选中的是完整的一组（肽, Linker），实验可以对 N/C/Both 三个位置做对照。

用法：
    .venv/bin/python -m main.stages.stage04_enumerate

输入：
    output/round03_heavy/final/top80.csv
    data/silk.fasta
    data/linker.fasta

输出：
    output/stage04_enumerate/README.md
    output/stage04_enumerate/final/constructs.csv  ← 全部 construct 评分明细
    output/stage04_enumerate/final/top30.fasta     ← 进 3D 的 FASTA
    output/stage04_enumerate/final/top30.csv       ← 进 3D 的评分明细
    output/status/status_*.md
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE = "stage04_enumerate"
STAGE_DIR = OUTPUT_DIR / STAGE

from main.client import ServiceClient
from main.data_loader import load_fasta

LOG_FILE: Path | None = None

# ── 配置 ──
TOP_PEPTIDES = 40           # 枚举前 N 条肽
TOP_GROUPS = 30             # 选 Top K 组（每组 → 3 position → 90 construct）
HIS_TAG = "LEHHHHHH"

SELECTED_LINKERS = [
    ("Flex_GGGGSx1", "GGGGS", "柔性 (P0)"),
    ("Flex_GGGGSx2", "GGGGSGGGGS", "长柔性 (P0)"),
]

POSITIONS = [
    ("N", "功能肽在 N 端"),
    ("C", "功能肽在 C 端"),
    ("Both", "两端"),
]

WEIGHT_PEPTIDE = 0.65        # 肽综合分（AnOxPePred 主导，权重 0.65 → 0.65/(0.65+0.30)=68.4%）
WEIGHT_SODOPE = 0.30         # SoDoPE 可溶性
WEIGHT_TEMSTAPRO = 0.10


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


def strip_his6(seq: str) -> str:
    tag = "HHHHHH"
    return seq[:-len(tag)] if seq.endswith(tag) else seq


def build_construct(pep_seq: str, linker_seq: str, position: str, scaffold_core: str) -> str:
    if position == "N":
        return f"{pep_seq}{linker_seq}{scaffold_core}{HIS_TAG}"
    elif position == "C":
        return f"{scaffold_core}{linker_seq}{pep_seq}{linker_seq}{HIS_TAG}"
    elif position == "Both":
        return f"{pep_seq}{linker_seq}{scaffold_core}{linker_seq}{pep_seq}{linker_seq}{HIS_TAG}"
    raise ValueError(f"Unknown position: {position}")


async def run():
    global LOG_FILE
    start_time = time.time()
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = STAGE_DIR / "run.log"

    n_peptides = TOP_PEPTIDES
    n_groups = TOP_GROUPS
    n_positions = len(POSITIONS)

    log("=" * 60)
    log(f"阶段四：枚举 + Construct FASTA 评分（分组排序）")
    log(f"  Top {n_peptides} 肽 × {len(SELECTED_LINKERS)} Linker × {n_positions} 位置")
    log(f"  → SoDoPE 评分 → 按（肽, Linker）分组 → Top {n_groups} 组 × {n_positions} position = {n_groups * n_positions} construct")
    log("=" * 60)

    # ── 1. Scaffold ──
    silk_data = load_fasta(DATA_DIR / "silk.fasta")
    if not silk_data:
        log("❌ 未读取到 silk.fasta")
        return
    scaffold_full = silk_data[0]["sequence"]
    scaffold_core = strip_his6(scaffold_full)
    log(f"丝素 scaffold: {len(scaffold_core)} aa + {HIS_TAG}")

    # ── 2. Linker ──
    all_linkers = {l["id"]: l["sequence"] for l in load_fasta(DATA_DIR / "linker.fasta")}
    selected_linkers = [(lid, all_linkers[lid], desc)
                        for lid, seq, desc in SELECTED_LINKERS
                        if lid in all_linkers]
    log(f"Linker: {len(selected_linkers)} 种")

    # ── 3. 优选肽 ──
    stage2_path = OUTPUT_DIR / "round03_heavy" / "final" / "top80.csv"
    if not stage2_path.exists():
        log(f"❌ 找不到阶段二输出")
        return
    df_pep = pd.read_csv(stage2_path).head(n_peptides)
    peptides = df_pep.to_dict("records")
    log(f"优选肽: {len(peptides)} 条（Top {n_peptides}）")

    # ══════════════════════════════════════════════════════════════════
    # 枚举
    # ══════════════════════════════════════════════════════════════════
    log("\n🔗 枚举 construct...")
    constructs: list[dict] = []
    con_idx = 0

    for pep in peptides:
        pid = pep["peptide_id"]
        pep_seq = pep["sequence"]
        pep_score = pep.get("weighted_score", 0)

        for lid, lseq, ldesc in selected_linkers:
            for pos_name, pos_desc in POSITIONS:
                con_idx += 1
                full_seq = build_construct(pep_seq, lseq, pos_name, scaffold_core)
                constructs.append({
                    "construct_id": f"con_{con_idx:04d}",
                    "peptide_id": pid,
                    "peptide_sequence": pep_seq,
                    "peptide_score": round(pep_score, 4) if pep_score else None,
                    "linker_id": lid,
                    "linker_sequence": lseq,
                    "linker_description": ldesc,
                    "position": pos_name,
                    "sequence": full_seq,
                    "length": len(full_seq),
                })

    total = len(constructs)
    log(f"生成 {total} 个 construct")

    # ══════════════════════════════════════════════════════════════════
    # SoDoPE 评分
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 SoDoPE（溶解度评分）— {total} 个 construct")
    client = ServiceClient(timeout=120.0)
    t0 = time.time()

    batch = [{"sequence": c["sequence"], "peptide_id": c["construct_id"]} for c in constructs]
    result = await client.predict_batch("sodope", batch)

    sodope_scores: dict[str, float | None] = {}
    sodope_ok = False

    if result.get("success") and result.get("results"):
        sodope_ok = True
        raw = []
        for r in result["results"]:
            cid = r.get("peptide_id", "unknown")
            s = r.get("score")
            sodope_scores[cid] = s
            if s is not None:
                raw.append(s)
        stats = f"min={min(raw):.3f}, max={max(raw):.3f}, mean={sum(raw)/len(raw):.3f}" if raw else "无"
        log(f"  耗时: {time.time()-t0:.1f}s, 有效: {len(raw)}/{total}, {stats}")
        write_json(make_dir("sodope"), "result.json", result)
    else:
        log(f"  ⚠ SoDoPE 失败: {result.get('error', '未知')}")

    # ══════════════════════════════════════════════════════════════════
    # TemStaPro（可选）
    # ══════════════════════════════════════════════════════════════════
    temstapro_scores: dict[str, float | None] = {}
    temstapro_ok = False

    health = await client.check_health(["temstapro"])
    if health.get("temstapro", {}).get("available", False):
        log(f"\n📊 TemStaPro（热稳定性）— {total} 个")
        t0 = time.time()
        result = await client.predict_batch("temstapro", batch)
        if result.get("success") and result.get("results"):
            temstapro_ok = True
            raw = []
            for r in result["results"]:
                cid = r.get("peptide_id", "unknown")
                s = r.get("score")
                temstapro_scores[cid] = s
                if s is not None:
                    raw.append(s)
            stats = f"min={min(raw):.3f}, max={max(raw):.3f}, mean={sum(raw)/len(raw):.3f}" if raw else "无"
            log(f"  耗时: {time.time()-t0:.1f}s, 有效: {len(raw)}/{total}, {stats}")
            write_json(make_dir("temstapro"), "result.json", result)
    else:
        log(f"\nℹ TemStaPro 未就绪，跳过")

    # ══════════════════════════════════════════════════════════════════
    # 综合评分
    # ══════════════════════════════════════════════════════════════════
    log(f"\n🧮 计算综合分...")
    for c in constructs:
        cid = c["construct_id"]

        s = sodope_scores.get(cid)
        c["sodope_score"] = round(s, 4) if s is not None else None

        t = temstapro_scores.get(cid)
        c["temstapro_score"] = round(t, 4) if t is not None else None

        weighted = 0.0
        tw = 0.0
        if c["peptide_score"] is not None:
            weighted += c["peptide_score"] * WEIGHT_PEPTIDE
            tw += WEIGHT_PEPTIDE
        if c["sodope_score"] is not None:
            weighted += c["sodope_score"] * WEIGHT_SODOPE
            tw += WEIGHT_SODOPE
        if c["temstapro_score"] is not None:
            weighted += c["temstapro_score"] * WEIGHT_TEMSTAPRO
            tw += WEIGHT_TEMSTAPRO
        c["composite_score"] = round(weighted / tw, 4) if tw > 0 else None

    df_all = pd.DataFrame(constructs)

    # ══════════════════════════════════════════════════════════════════
    # 分组排序：按（肽, Linker）分组，组内取最高分
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 分组排序 — 按（peptide_id, linker_id）分组，取最高综合分")

    # 构建分组 key
    df_all["group_key"] = df_all["peptide_id"] + "|" + df_all["linker_id"]

    # 每组取最高分
    group_best = df_all.loc[df_all.groupby("group_key")["composite_score"].idxmax()]
    group_best = group_best.sort_values("composite_score", ascending=False)
    group_best["group_rank"] = range(1, len(group_best) + 1)

    n_top_groups = min(n_groups, len(group_best))
    top_groups = group_best.head(n_top_groups)
    top_group_keys = set(top_groups["group_key"])

    # 从全量数据中取出这些组的所有 position
    df_top = df_all[df_all["group_key"].isin(top_group_keys)].copy()
    # 按 group_rank 和 composite_score 降序排序
    rank_map = dict(zip(top_groups["group_key"], top_groups["group_rank"]))
    df_top["group_rank"] = df_top["group_key"].map(rank_map)
    df_top = df_top.sort_values(["group_rank", "composite_score"], ascending=[True, False])
    df_top["rank"] = range(1, len(df_top) + 1)

    # 保存全部
    score_dir = make_dir("scores")
    df_all.to_csv(score_dir / "all_ranked.csv", index=False)
    log(f"全部评分已保存: {score_dir / 'all_ranked.csv'}")

    # ── 分组摘要 ──
    log(f"\n  分组排序 Top {n_top_groups}:")
    for _, r in top_groups.iterrows():
        group_data = df_all[df_all["group_key"] == r["group_key"]]
        scores_str = " | ".join(
            f"{row['position']}={row['composite_score']:.4f}"
            for _, row in group_data.sort_values("position").iterrows()
        )
        log(f"    {r['group_rank']:2d}. {r['peptide_id']:12s} | {r['linker_id']:25s}  [{scores_str}]  best={r['composite_score']:.4f}")

    # ══════════════════════════════════════════════════════════════════
    # 输出 → 3D 预测
    # ══════════════════════════════════════════════════════════════════
    final_dir = make_dir("final")
    n_out = len(df_top)
    df_top.to_csv(final_dir / f"top{n_out}.csv", index=False)
    log(f"\nTop {n_out} 已保存: {final_dir / f'top{n_out}.csv'}")

    fasta_path = final_dir / f"top{n_out}.fasta"
    with open(fasta_path, "w", encoding="utf-8") as f:
        for _, r in df_top.iterrows():
            f.write(f">{r['construct_id']} | {r['peptide_id']} | {r['linker_id']} | {r['position']} | "
                    f"score={r['composite_score']:.4f} | {r['length']}aa\n")
            f.write(r["sequence"] + "\n")
    log(f"FASTA: {fasta_path}")

    # Stage 5 输入配置
    top_constructs = df_top.to_dict("records")
    stage5_input = {
        "source_stage": STAGE,
        "timestamp": datetime.now().isoformat(),
        "grouping": "peptide+linker",
        "n_groups": n_top_groups,
        "positions_per_group": n_positions,
        "n_constructs": len(top_constructs),
        "n_peptides": n_peptides,
        "sodope": sodope_ok,
        "temstapro": temstapro_ok,
        "weights": {"peptide": WEIGHT_PEPTIDE, "sodope": WEIGHT_SODOPE,
                    "temstapro": WEIGHT_TEMSTAPRO if temstapro_ok else 0},
        "constructs": [{"construct_id": c["construct_id"],
                        "peptide_id": c["peptide_id"],
                        "peptide_score": c["peptide_score"],
                        "sodope_score": c["sodope_score"],
                        "temstapro_score": c["temstapro_score"],
                        "composite_score": c["composite_score"],
                        "linker_id": c["linker_id"],
                        "position": c["position"],
                        "length": c["length"]}
                       for c in top_constructs],
    }
    write_json(final_dir, "stage5_input.json", stage5_input)

    total_time = time.time() - start_time

    log(f"\n📊 阶段四汇总")
    log(f"  枚举: {n_peptides} 肽 × {len(selected_linkers)} Linker × {n_positions} 位置 = {total} construct")
    log(f"  分组: {n_top_groups} 组 × {n_positions} position = {n_out} construct")
    log(f"  SoDoPE: {'✅' if sodope_ok else '❌'}")
    log(f"  TemStaPro: {'✅' if temstapro_ok else '⏸ 未就绪'}")
    log(f"  输出: {n_out} construct → 3D 预测")
    log(f"  耗时: {total_time:.1f}s")

    write_readme(df_all, df_top, selected_linkers, n_peptides, n_top_groups,
                 sodope_ok, temstapro_ok, total_time)
    write_status(n_out, total, sodope_ok, temstapro_ok, n_top_groups)

    await client.close()


def write_readme(df_all, df_top, selected_linkers, n_peptides, n_groups,
                 sodope_ok, temstapro_ok, elapsed):
    # Top 组表格
    group_best = df_all.loc[df_all.groupby("group_key")["composite_score"].idxmax()]
    group_best = group_best.sort_values("composite_score", ascending=False).head(n_groups)
    group_best["group_rank"] = range(1, len(group_best) + 1)

    group_lines = []
    for _, r in group_best.iterrows():
        group_data = df_all[df_all["group_key"] == r["group_key"]].sort_values("position")
        scores = " / ".join(f"{row['position']}: {row['composite_score']:.4f}" for _, row in group_data.iterrows())
        group_lines.append(f"| {r['group_rank']} | {r['peptide_id']} | {r['linker_id']} | {scores} | **{r['composite_score']:.4f}** |")
    group_table = "\n".join(group_lines)

    linker_lines = "\n".join(f"| {lid} | {lseq} | {desc} |" for lid, lseq, desc in selected_linkers)
    pos_lines = "\n".join(f"| {p} | {d} |" for p, d in POSITIONS)

    # SoDoPE 分布
    sodope_valid = df_all["sodope_score"].dropna()
    sodope_stats = ""
    if len(sodope_valid) > 0:
        bins = [(0.8, 1.0), (0.6, 0.8), (0.4, 0.6), (0.2, 0.4), (0.0, 0.2)]
        parts = [f"  {lo:.1f}-{hi:.1f}: {((sodope_valid >= lo) & (sodope_valid < hi)).sum()} 条"
                 for lo, hi in bins]
        sodope_stats = (f"min={sodope_valid.min():.3f}, max={sodope_valid.max():.3f}, "
                        f"mean={sodope_valid.mean():.3f}\n" + "\n".join(parts))

    readme = f"""# 阶段四：枚举 + Construct FASTA 评分 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.1f} 秒

## 评分权重

| 维度 | 权重 | 来源 |
|------|------|------|
| 肽综合分 | {WEIGHT_PEPTIDE} | 阶段二（5 服务加权） |
| SoDoPE 溶解度 | {WEIGHT_SODOPE} | Construct 级 FASTA 评分 |
| TemStaPro 热稳定性 | {WEIGHT_TEMSTAPRO if temstapro_ok else "0（未就绪）"} | - |

## 排序逻辑

按（peptide_id, linker_id）分组，同一肽 × 同一 Linker 的 3 个 position（N/C/Both）为一组，
组内取最高 G-FASTA 综合分代表该组参与排序。选 Top {n_groups} 组后，每组 3 个 position
全部输出 → 进入 3D 结构预测，确保实验能对三个插入位置做对照。

## 枚举参数

| 参数 | 值 |
|------|-----|
| 优选肽 | {n_peptides} 条 |
| Linker | {len(selected_linkers)} 种 |
| 位置方案 | {len(POSITIONS)} 种 |
| **总 construct** | **{len(df_all)}** |
| **分组数** | **{n_groups} 组** |
| **输出（→ 3D）** | **{n_groups * len(POSITIONS)}** |

## Linker & 位置

**Linker:**
| ID | 序列 | 描述 |
|----|------|------|
{linker_lines}

**位置:**
| 名称 | 描述 |
|------|------|
{pos_lines}

## SoDoPE 溶解度分布

{sodope_stats}

## 分组排名（每行 = 一个组）

| 排名 | 肽 | Linker | N/C/Both 分 | 组最高分 |
|------|-----|--------|-------------|----------|
{group_table}

## 输出

- `scores/all_ranked.csv` — 全部 {len(df_all)} 个 construct 的评分明细
- `final/top{len(df_top)}.csv` — Top {len(df_top)}（进入 3D 预测）
- `final/top{len(df_top)}.fasta` — 3D 预测输入 FASTA
- `final/stage5_input.json` — 阶段五配置
"""
    readme_path = STAGE_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    log(f"报告已写入: {readme_path}")


def write_status(n_out, total, sodope_ok, temstapro_ok, n_groups):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    status_dir = OUTPUT_DIR / "status"
    status_dir.mkdir(exist_ok=True)
    status_path = status_dir / f"status_{timestamp}.md"

    est_esmfold = f"~{n_out * 2 // 3} 分钟（并发 3，~2 min/条）"

    status = f"""# 🧬 Pipeline 状态

**更新**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**焦点**: 抗氧化肽

---

## 全局进度

| # | 阶段 | 状态 | 输入 → 输出 |
|---|------|------|-------------|
| 1 | 硬过滤 | ✅ 完成 | 1843 → **107** 条 |
| 2 | 快速评分 + 排序 | ✅ 完成 | 107 → **80** 条 |
| 3 | 精确评分 | ⏹ 跳过（TemStaPro 网络慢） | — |
| 4 | 枚举 + Construct FASTA 评分 | ✅ 完成 | 20 肽 → {total} construct → **{n_groups} 组 × 3 = {n_out}** |
| 5 | 3D 预测 (ESMFold) | ⏳ 待开始 | {n_out} construct → PDB |
| 6 | PDB 评估 | ⏳ 待开始 | — |

## 阶段四：枚举 + FASTA 评分

**排序方式**: 按（肽, Linker）分组，组内取最高 G-FASTA 分 → Top {n_groups} 组
**每组输出**: N 端 + C 端 + 两端 → 全部进 3D（形成对照）

**SoDoPE**: {'✅' if sodope_ok else '❌'}
**TemStaPro**: {'✅' if temstapro_ok else '⏸ 未就绪'}
**综合权重**: 肽 {WEIGHT_PEPTIDE} + SoDoPE {WEIGHT_SODOPE}{' + TemStaPro ' + str(WEIGHT_TEMSTAPRO) if temstapro_ok else ''}

**预计 3D 耗时**: {est_esmfold}

**输出**: `stage04_enumerate/final/top{n_out}.fasta`

详见: `stage04_enumerate/README.md`

## 下一步

阶段五（3D 结构预测 — ESMFold），输入: `stage04_enumerate/final/top{n_out}.fasta`
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
