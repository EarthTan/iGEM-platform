"""
Round 7：Final Round — 最终输出（双通道排名）

按 SASA 暴露度排序，分别输出 Top constructs 和 Bottom constructs 的最终结果包。

与原脚本差异：
  - 输出到 output2/
  - 使用 common.py 共享工具
  - 修复输入文件名：读 sasa_ranking.csv（原脚本读 final_ranked_sasa.csv，不存在！）
  - 修复 README 轮次标签（原脚本显示 stages1 的标签，全错）
  - 新增 Bottom 排名：按 channel 分离，各自独立排序输出
  - per-construct 文件夹名包含 channel 标签

用法：
    uv run python -m main.stages2.round07_final

输入：
    output2/round06_pdb_eval/final/sasa_ranking.csv
    output2/round05_3d/constructs/

输出：
    output2/round07_final/
    ├── README.md                     ← 全流程报告（含 Top + Bottom 对比）
    ├── top_ranking.csv               ← Top constructs 排名
    ├── top10_summary.csv             ← Top 10 精简表
    ├── bottom_ranking.csv            ← Bottom constructs 排名
    ├── bottom10_summary.csv          ← Bottom 10 精简表
    ├── score_distribution.json       ← 各分数维度分布
    └── constructs/                   ← 每个 construct 独立文件夹
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from main.stages2.common import OUTPUT_DIR, log, setup_stage, make_dir, write_json, read_csv

STAGE = "round07_final"
STAGE_DIR = OUTPUT_DIR / STAGE
CONSTRUCTS_DIR = STAGE_DIR / "constructs"

# ── 排名 CSV 字段 ──
RANKING_FIELDS = [
    "rank", "channel", "construct_id", "peptide_id", "peptide_sequence",
    "position", "linker_id",
    "round6_score", "construct_composite",
    "sodope_score", "temstapro_score", "construct_anoxpepred", "construct_bepipred3",
    "omegafold_plddt",
    "sasa_score", "sasa_label",
    "aggrisk_score", "aggrisk_label",
]

SUMMARY_FIELDS = [
    "rank", "construct_id", "peptide_id", "peptide_sequence",
    "position", "linker_id",
    "sasa_score", "construct_composite", "omegafold_plddt",
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_sasa_ranking() -> list[dict]:
    """读取 round06 的 sasa_ranking.csv，返回排名行列表。"""
    path = OUTPUT_DIR / "round06_pdb_eval" / "final" / "sasa_ranking.csv"
    if not path.exists():
        log(f"❌ 找不到 {path}")
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    log(f"📋 加载 {len(rows)} 个 construct 排名（从 {path.name}）")
    return rows


def _separate_channels(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 channel 字段分离 Top 和 Bottom。"""
    top = [r for r in rows if r.get("channel", "top") == "top"]
    bottom = [r for r in rows if r.get("channel", "") == "bottom"]
    if not bottom:
        log("  ⚠ 未发现 Bottom constructs（仅 Top 通道）")
    return top, bottom


def _write_ranking_csv(path: Path, rows: list[dict], fields: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log(f"  📄 {path.name} ({len(rows)} 行)")


def _build_per_construct_folders(scored_rows: list[dict]):
    """为排名中的 construct 创建独立文件夹。"""
    n_ok = 0
    for row in scored_rows:
        cid = row["construct_id"]
        channel = row.get("channel", "top")
        con_src = OUTPUT_DIR / "round05_3d" / "constructs" / cid
        if not con_src.exists():
            log(f"  ⚠ {cid}: construct 目录不存在，跳过")
            continue

        # 读取 metadata 和 scores
        try:
            meta = read_json(con_src / "metadata.json")
            scores = read_json(con_src / "scores.json")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            log(f"  ⚠ {cid}: 读取失败: {e}")
            continue

        # 读取 SASA / A3D 原始数据
        try:
            sasa_raw = read_json(OUTPUT_DIR / "round06_pdb_eval" / "raw" / "sasa_results.json")
            a3d_raw = read_json(OUTPUT_DIR / "round06_pdb_eval" / "raw" / "aggrescan3d_results.json")
        except FileNotFoundError:
            sasa_raw = {}
            a3d_raw = {}

        sasa_entry = sasa_raw.get(cid, {})
        a3d_entry = a3d_raw.get(cid, {})

        pep_label = meta.get("peptide_id", "unknown").replace("pep_", "pep")
        pos_label = meta.get("position", "X")
        folder_name = f"{cid}_{pep_label}_{pos_label}_{channel}"
        folder_path = CONSTRUCTS_DIR / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        # construct.fasta
        seq = meta.get("construct_sequence", "")
        fasta_content = (
            f">{cid} | {meta.get('peptide_id', '')} | {meta.get('position', '')} "
            f"| {meta.get('linker_id', '')} | silk-fibroin fusion | channel={channel}\n{seq}\n"
        )
        (folder_path / "construct.fasta").write_text(fasta_content, encoding="utf-8")

        # PDB
        pdb_src = con_src / f"{cid}_omegafold.pdb"
        if pdb_src.exists():
            shutil.copy2(pdb_src, folder_path / "construct_omegafold.pdb")

        # scores.json — 聚合全流水线评分
        combined_scores = {
            "construct_id": cid,
            "channel": channel,
            "peptide_id": meta.get("peptide_id", ""),
            "peptide_sequence": meta.get("peptide_sequence", ""),
            "position": meta.get("position", ""),
            "linker_id": meta.get("linker_id", ""),
            "pipeline_scores": {
                "peptide_composite": scores.get("peptide_composite"),
                "construct_composite": scores.get("construct_composite"),
                "sodope": scores.get("sodope"),
                "temstapro_construct": scores.get("temstapro_construct"),
                "construct_anoxpepred": scores.get("construct_anoxpepred"),
                "construct_bepipred3": scores.get("construct_bepipred3"),
                "anox_change_ratio": scores.get("anox_change_ratio"),
                "round3_services": scores.get("round3_services"),
            },
            "structure": {
                "method": "omegafold",
                "plddt": scores.get("structure", {}).get("omegafold", {}).get("plddt"),
                "esmfold_plddt": scores.get("structure", {}).get("esmfold", {}).get("plddt"),
            },
            "pdb_evaluation": {
                "sasa_score": sasa_entry.get("score"),
                "sasa_label": sasa_entry.get("label", ""),
                "sasa_details": sasa_entry.get("details"),
                "aggrescan3d_score": a3d_entry.get("score"),
                "aggrescan3d_label": a3d_entry.get("label", ""),
            },
            "ranking": {
                "criteria": "round6_score",
                "rank": int(row.get("round6_rank", 0)),
                "channel": channel,
            },
        }
        write_json(folder_path / "scores.json", combined_scores)
        write_json(folder_path / "metadata.json", meta.get("original_database", {}))
        n_ok += 1

    log(f"  📁 共 {n_ok} 个文件夹")


def _compute_distribution(rows: list[dict]) -> dict:
    vals: dict[str, list[float]] = {
        "sasa": [],
        "construct_composite": [],
        "plddt": [],
        "aggrisk": [],
        "round6_score": [],
    }

    sasa_raw_path = OUTPUT_DIR / "round06_pdb_eval" / "raw" / "sasa_results.json"
    a3d_raw_path = OUTPUT_DIR / "round06_pdb_eval" / "raw" / "aggrescan3d_results.json"
    sasa_raw = read_json(sasa_raw_path) if sasa_raw_path.exists() else {}
    a3d_raw = read_json(a3d_raw_path) if a3d_raw_path.exists() else {}

    for row in rows:
        cid = row["construct_id"]
        try:
            cc = float(row.get("construct_composite", 0) or 0)
            plddt = float(row.get("omegafold_plddt", 0) or 0)
            sasa = float(row.get("sasa_score", 0) or 0)
            a3d = float(a3d_raw.get(cid, {}).get("score", 0) or 0)
            r6 = float(row.get("round6_score", 0) or 0)
        except (ValueError, TypeError):
            continue

        vals["sasa"].append(sasa)
        vals["construct_composite"].append(cc)
        vals["plddt"].append(plddt)
        vals["aggrisk"].append(a3d)
        vals["round6_score"].append(r6)

    def describe(v):
        n = len(v)
        if n == 0:
            return {"n": 0}
        sv = sorted(v)
        mean = sum(sv) / n
        median = sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2
        return {
            "n": n, "mean": round(mean, 4), "median": round(median, 4),
            "min": round(sv[0], 4), "max": round(sv[-1], 4),
        }

    return {k: describe(v) for k, v in vals.items() if v}


def _build_readme(all_rows, top_rows, bottom_rows, dist):
    top5 = top_rows[:5]
    bottom5_sorted = sorted(bottom_rows, key=lambda r: float(r.get("sasa_score", 0) or 0), reverse=True)[:5]

    top5_table = "\n".join(
        f"| {r['rank']} | {r['construct_id']} | {r['peptide_sequence'][:20]:20s} | "
        f"{r['position']} | {r['linker_id']} | {r['sasa_score']} | {r['construct_composite']} | {r['omegafold_plddt']} |"
        for r in top5
    )

    if bottom5_sorted:
        bot5_table = "\n".join(
            f"| {r.get('round6_rank', '?')} | {r['construct_id']} | {r['peptide_sequence'][:20]:20s} | "
            f"{r['position']} | {r['linker_id']} | {r['sasa_score']} | {r['construct_composite']} | {r['omegafold_plddt']} |"
            for r in bottom5_sorted
        )
    else:
        bot5_table = "无 Bottom constructs"

    def fmt(key, label):
        s = dist.get(key, {})
        if s.get("n", 0) == 0:
            return f"| {label} | N/A | N/A | N/A |"
        return f"| {label} | {s['mean']} | {s['min']} ~ {s['max']} | {s['n']} |"

    # 修复轮次标签（原脚本显示的是 stages1 的标签，全错）
    readme = f"""# Round 7：Final Round — iGEM-silk 抗氧化肽融合蛋白最终结果

**生成日期**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Construct 总数**: {len(all_rows)}（Top: {len(top_rows)}, Bottom: {len(bottom_rows)}）
**骨架**: 丝素蛋白 (~346 aa) + His6 标签
**排名标准**: Round 6 综合评分（SASA × 0.40 + (1-aggrisk) × 0.40 + pLDDT_norm × 0.20）

---

## 全流程回顾（stages2 修复版 ✅）

| 阶段 | 步骤 | 输入 → 输出 | 主要服务 | 说明 |
|------|------|------------|---------|------|
| Step 0 | 数据整合 | 1,081,772 → 1,055,116 | 清洗+去重 | 3-30aa, 标准氨基酸 |
| Round 1 | 轻量评分 | 1,055,116 → Top 50K | AnOxPePred(0.50), ToxinPred3(0.15), AlgPred2(0.10) | 3 并发服务 |
| Round 2 | 追加评分 | 50K → Top 10K | +HemoPI2(0.10), +MHCflurry(0.05) | 复用 Round 1 ToxinPred3 |
| Round 3 | 重服务评分 | 10K → Top 80 + Bottom 10 | +BepiPred3(0.07), +TemStaPro(0.05) | 新增 Bottom-N |
| Round 4 | 枚举+Construct评分 | 40 肽+10 Bottom → ~150 construct | SoDoPE+全长AnOxPePred+BepiPred3 | 新增双通道+活性比 |
| Round 5 | 3D 结构预测 | ~150 construct → PDB | ESMFold + OmegaFold | 双模型并发 |
| Round 6 | PDB 评估 | ~150 PDB → SASA+Aggrescan3D | SASA, Aggrescan3D | 修复文件名 |
| **Round 7** | **最终输出** | **双通道排名→结果包** | — | 修复标签+新增Bottom排名 |

## 排名标准

按 **Round 6 综合分** 从高到低排序。Top 和 Bottom 各自独立排序。

## Top 5

| 排名 | Construct | 肽序列 | 位置 | Linker | SASA | 综合分 | pLDDT |
|------|-----------|--------|------|--------|------|--------|-------|
{top5_table}

## Bottom 5（抗氧化最差但其他安全）

| 排名 | Construct | 肽序列 | 位置 | Linker | SASA | 综合分 | pLDDT |
|------|-----------|--------|------|--------|------|--------|-------|
{bot5_table}

## 分数分布概览

| 维度 | 均值 | 范围 | 样本数 |
|------|------|------|--------|
{fmt('round6_score', 'Round 6 综合分')}
{fmt('sasa', 'SASA 暴露度')}
{fmt('construct_composite', 'construct_composite')}
{fmt('plddt', 'OmegaFold pLDDT')}
{fmt('aggrisk', 'Aggrescan3D 风险')}

## 输出目录结构

```
output2/round07_final/
├── README.md                        ← 本报告（含 Top + Bottom 对比）
├── top_ranking.csv                  ← Top constructs 排名
├── top10_summary.csv                ← Top 10 精简表
├── bottom_ranking.csv               ← Bottom constructs 排名（仅当有 Bottom 时）
├── bottom10_summary.csv             ← Bottom 10 精简表
├── score_distribution.json          ← 各分数维度分布统计
└── constructs/                      ← 每个 construct 独立文件夹
    ├── con_0001_pep000743_N_top/
    │   ├── construct.fasta
    │   ├── construct_omegafold.pdb
    │   ├── scores.json
    │   └── metadata.json
    └── ...（共 {len(all_rows)} 个文件夹）
```

## 注意事项

- **Top 排名**: 综合分最高的功能肽融合 construct
- **Bottom 排名**: 抗氧化活性（AnOxPePred）最低、但所有安全维度（毒性/致敏/溶血/免疫/B 细胞）均通过阈值的 construct，作为阴性对照
- OmegaFold pLDDT 均值约 0.42，属于中等偏低置信度，SASA/A3D 结果仅供参考
- 建议选取 Top 5-10 进行 wet-lab 验证
"""
    return readme


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

def main():
    setup_stage(STAGE)
    make_dir(STAGE_DIR, "constructs")
    log("=" * 60)
    log("Round 7：Final Round — 最终输出（双通道排名）")
    log("=" * 60)

    # 1. 读取 sasa_ranking.csv（已修复文件名一致）
    all_rows = _read_sasa_ranking()
    if not all_rows:
        return

    # 2. 按 channel 分离
    top_rows, bottom_rows = _separate_channels(all_rows)

    # 3. 各自排序（按 round6_score 降序）
    top_rows.sort(key=lambda r: float(r.get("round6_score", 0) or 0), reverse=True)
    for i, r in enumerate(top_rows, 1):
        r["rank"] = i

    if bottom_rows:
        bottom_rows.sort(key=lambda r: float(r.get("round6_score", 0) or 0), reverse=True)
        for i, r in enumerate(bottom_rows, 1):
            r["rank"] = i

    # 4. 输出排名 CSV
    _write_ranking_csv(STAGE_DIR / "top_ranking.csv", top_rows, RANKING_FIELDS)
    _write_ranking_csv(STAGE_DIR / "top10_summary.csv", top_rows[:10], SUMMARY_FIELDS)

    if bottom_rows:
        _write_ranking_csv(STAGE_DIR / "bottom_ranking.csv", bottom_rows, RANKING_FIELDS)
        _write_ranking_csv(STAGE_DIR / "bottom10_summary.csv", bottom_rows[:10], SUMMARY_FIELDS)
    else:
        log("  ⚠ 无 Bottom constructs，跳过 bottom 排名输出")

    # 5. per-construct 文件夹
    log("\n📁 生成 construct 文件夹...")
    _build_per_construct_folders(all_rows)

    # 6. 分布统计
    log("\n📊 分数分布...")
    dist = _compute_distribution(all_rows)
    write_json(STAGE_DIR / "score_distribution.json", dist)

    # 7. README
    log("\n📝 写入 README...")
    readme = _build_readme(all_rows, top_rows, bottom_rows, dist)
    (STAGE_DIR / "README.md").write_text(readme, encoding="utf-8")

    # 汇总
    log(f"\n{'=' * 60}")
    log(f"Round 7 汇总")
    log(f"  Top constructs: {len(top_rows)}")
    log(f"  Bottom constructs: {len(bottom_rows)}")
    log(f"  Per-construct folders: {len(all_rows)}")
    log(f"  ✅ 最终输出目录: {STAGE_DIR}")
    log(f"{'=' * 60}")


if __name__ == "__main__":
    main()
