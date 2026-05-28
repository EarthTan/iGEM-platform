"""
Round 7: Final Round — 最终输出。

为 250 个 construct 各自创建独立文件夹，包含全部评分数据、排名、来源信息和 PDB 文件。

用法:
    uv run python -m main.stages4.s4_round07_final
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.stages4.s4_db import PipelineDB

OUTPUT4 = PROJECT_ROOT / "output4"
FINAL_DIR = OUTPUT4 / "final"
CONSTRUCTS_DIR = FINAL_DIR / "constructs"
REPORT_DIR = OUTPUT4 / "reports"

# ── 评分权重 ──
W_SASA = 0.40
W_AGG = 0.40
W_PLDDT = 0.20
FORMULA_DESC = f"{W_SASA}×SASA + {W_AGG}×(1−A3D) + {W_PLDDT}×pLDDT_norm"
WEIGHT_SNAPSHOT = {
    "formula": FORMULA_DESC,
    "W_SASA": W_SASA, "W_AGG": W_AGG, "W_PLDDT": W_PLDDT,
}


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_all_construct_data(db: PipelineDB) -> list[dict]:
    """加载 250 个 constructs 的完整数据（所有分数 + 排名 + 来源）。"""
    conn = db.connect()
    rows = conn.execute("""
        SELECT
            c.construct_id,
            c.candidate_id,
            c.channel,
            c.position,
            c.linker,
            c.scaffold_seq,
            c.linker_seq,
            c.peptide_seq,
            c.full_sequence,

            -- Candidate 来源信息
            ca.source,
            ca.source_id,
            ca.header,

            -- Round 1: 肽级别初筛
            r1s.anoxpepred_score,
            r1s.algpred2_score,
            r1c.rank_in_channel         AS r1_rank_in_channel,

            -- Round 2: 安全过滤
            r2s.toxinpred3_score,
            r2s.hemopi2_score,
            r2s.mhcflurry_score,

            -- Round 3: 重服务评分
            r3s.bepipred3_score          AS r3_bepipred3,
            r3s.temstapro_score          AS r3_temstapro,
            r3s.sodope_score             AS r3_sodope,
            r3s.plm4cpps_score,
            r3s.graphcpp_score,
            r3s.toxinpred3_score         AS r3_toxinpred3,
            r3r.composite_score          AS r3_composite,
            r3r.rank                     AS r3_rank,

            -- Round 4: construct 级别评分
            cs.sodope_score,
            cs.temstapro_score,
            cs.bepipred3_score,
            r4p.combined_score           AS r4_combined,
            r4p.rank                     AS r4_rank,

            -- Round 5: 3D 结构
            sr.plddt,
            sr.pdb_path,

            -- Round 6: PDB 评估
            pe.sasa_score,
            pe.aggrescan3d_score

        FROM constructs c
        JOIN structure_results sr       ON sr.construct_id  = c.construct_id
        LEFT JOIN candidates ca         ON ca.candidate_id  = c.candidate_id
        LEFT JOIN round1_scores r1s     ON r1s.candidate_id = c.candidate_id
        LEFT JOIN round1_channels r1c   ON r1c.candidate_id = c.candidate_id
        LEFT JOIN round2_scores r2s     ON r2s.candidate_id = c.candidate_id
        LEFT JOIN round3_scores r3s     ON r3s.candidate_id = c.candidate_id
        LEFT JOIN round3_ranking r3r    ON r3r.candidate_id = c.candidate_id
        LEFT JOIN construct_scores cs   ON cs.construct_id  = c.construct_id
        LEFT JOIN round4_phase1_passed r4p ON r4p.construct_id = c.construct_id
        LEFT JOIN pdb_eval pe           ON pe.construct_id  = c.construct_id
        ORDER BY c.construct_id
    """).fetchall()

    constructs = []
    for r in rows:
        cid = int(r[0])
        cand_id = int(r[1]) if r[1] is not None else None

        def f(v):
            return float(v) if v is not None else None

        def i(v):
            return int(v) if v is not None else None

        constructs.append({
            "construct_id": cid,
            "candidate_id": cand_id,

            # 基本信息
            "channel": r[2],
            "position": r[3],
            "linker": r[4],
            "sequences": {
                "scaffold": r[5] or "",
                "linker": r[6] or "",
                "peptide": r[7] or "",
                "full": r[8] or "",
            },

            # 来源
            "source": r[9] or "",
            "source_id": r[10] or "",
            "source_header": r[11] or "",

            # 分数
            "scores": {
                "round1": {
                    "anoxpepred": f(r[12]),
                    "algpred2": f(r[13]),
                },
                "round2": {
                    "toxinpred3": f(r[15]),
                    "hemopi2": f(r[16]),
                    "mhcflurry": f(r[17]),
                },
                "round3": {
                    "bepipred3": f(r[18]),
                    "temstapro": f(r[19]),
                    "sodope": f(r[20]),
                    "plm4cpps": f(r[21]),
                    "graphcpp": f(r[22]),
                    "toxinpred3": f(r[23]),
                },
                "construct": {
                    "sodope": f(r[26]),
                    "temstapro": f(r[27]),
                    "bepipred3": f(r[28]),
                },
                "structure": {
                    "omegafold_plddt": f(r[31]),
                },
                "pdb_eval": {
                    "sasa": f(r[33]),
                    "aggrescan3d": f(r[34]),
                },
            },

            # 各阶段排名
            "rankings": {
                "round1_channel": {
                    "rank_in_channel": i(r[14]),
                },
                "round3_peptide": {
                    "composite_score": f(r[24]),
                    "rank": i(r[25]),
                },
                "round4_phase1": {
                    "combined_score": f(r[29]),
                    "rank": i(r[30]),
                },
            },

            # PDB 路径
            "pdb_path": r[32] or "",

            # 计算用的原始数据
            "_sasa": f(r[33]),
            "_agg": f(r[34]),
            "_plddt": f(r[31]),
        })
    return constructs


def compute_rankings(constructs: list[dict]) -> list[dict]:
    """计算 Round 7 综合评分 + 全局排名 + 通道内排名，直接写入 construct dict。"""
    plddt_vals = [c["_plddt"] for c in constructs if c["_plddt"] is not None]
    plddt_min = min(plddt_vals) if plddt_vals else 0
    plddt_max = max(plddt_vals) if plddt_vals else 1
    plddt_range = plddt_max - plddt_min if plddt_max > plddt_min else 1

    for c in constructs:
        plddt_norm = (c["_plddt"] - plddt_min) / plddt_range if c["_plddt"] is not None else 0.5
        sasa = c["_sasa"] if c["_sasa"] is not None else 0.0
        agg_inv = 1.0 - (c["_agg"] if c["_agg"] is not None else 0.5)
        round7 = W_SASA * sasa + W_AGG * agg_inv + W_PLDDT * plddt_norm
        c["round7_score"] = round(round7, 4)
        c["_plddt_norm"] = round(plddt_norm, 4)

    # 全局排序
    constructs.sort(key=lambda x: x["round7_score"], reverse=True)
    for i, c in enumerate(constructs):
        c["global_rank"] = i + 1

    # 通道内排序
    for channel in ["top", "bottom"]:
        channel_cons = [c for c in constructs if c["channel"] == channel]
        channel_cons.sort(key=lambda x: x["round7_score"], reverse=True)
        for i, c in enumerate(channel_cons):
            c["channel_rank"] = i + 1

    return constructs


def write_construct_folder(c: dict):
    """为单个 construct 创建文件夹，写入 JSON + 复制 PDB。"""
    cid = c["construct_id"]
    folder = CONSTRUCTS_DIR / f"con_{cid:04d}"
    folder.mkdir(parents=True, exist_ok=True)

    # 组装 construct.json
    payload = {
        "construct_id": c["construct_id"],
        "candidate_id": c["candidate_id"],
        "channel": c["channel"],
        "position": c["position"],
        "linker": c["linker"],
        "sequences": c["sequences"],
        "source": {
            "database": c["source"],
            "accession": c["source_id"],
            "header": c["source_header"],
        },
        "scores": c["scores"],
        "rankings": {
            **c["rankings"],
            "round7_final": {
                "composite_score": c["round7_score"],
                "formula": FORMULA_DESC,
                "weights": WEIGHT_SNAPSHOT,
                "global_rank": c["global_rank"],
                "channel_rank": c["channel_rank"],
            },
        },
    }

    (folder / "construct.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 复制 PDB 文件
    if c["pdb_path"]:
        pdb_src = Path(c["pdb_path"])
        if pdb_src.exists():
            shutil.copy2(pdb_src, folder / "omegafold.pdb")


def write_summary_readme(constructs: list[dict], elapsed: float):
    """生成 final/README.md 概览。"""
    top = [c for c in constructs if c["channel"] == "top"]
    bottom = [c for c in constructs if c["channel"] == "bottom"]
    top5 = top[:5]
    bot5 = bottom[:5]

    def fmt(c):
        return (
            f"| {c['global_rank']} | {c['channel']} | "
            f"con_{c['construct_id']:04d} | "
            f"{c['position']:4s} | {c['linker']:12s} | "
            f"{c['scores']['pdb_eval']['sasa']:.4f} | "
            f"{c['scores']['pdb_eval']['aggrescan3d']:.4f} | "
            f"{c['scores']['structure']['omegafold_plddt']:.4f} | "
            f"{c['round7_score']:.4f} |"
        )

    top5_table = "\n".join(fmt(c) for c in top5)
    bot5_table = "\n".join(fmt(c) for c in bot5)

    readme = f"""# iGEM-silk Stages4 最终结果

**生成日期**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Construct 总数**: {len(constructs)}（Top: {len(top)}, Bottom: {len(bottom)}）
**耗时**: {elapsed:.0f}s

## 排名公式

```
round7_score = {FORMULA_DESC}
```

## Top 5（按通道各自排名）

| 全局排名 | 通道 | ID | 位置 | Linker | SASA | A3D | pLDDT | 总分 |
|---------|------|----|------|--------|------|-----|-------|------|
{top5_table}

## Bottom 5

| 全局排名 | 通道 | ID | 位置 | Linker | SASA | A3D | pLDDT | 总分 |
|---------|------|----|------|--------|------|-----|-------|------|
{bot5_table}

## 输出结构

```
output4/final/
├── README.md                           ← 本文件
└── constructs/
    ├── con_0001/
    │   ├── construct.json              ← 全部评分、排名、来源信息
    │   └── omegafold.pdb               ← OmegaFold 预测的 3D 结构
    ├── con_0002/
    │   ├── construct.json
    │   └── omegafold.pdb
    └── ...                             ← 共 {len(constructs)} 个 construct
```

## 各 construct JSON 内容说明

`construct.json` 顶层字段：
- `construct_id`, `candidate_id` — 数据库 ID
- `channel` — Top / Bottom 通道
- `position` — 功能肽插入位置 (N/C/Both)
- `linker` — 连接肽类型
- `sequences` — 各片段序列及全长
- `source` — 来源数据库 (uniprot/mgy) + accession + header
- `scores.round1` — AnOxPePred, AlgPred2
- `scores.round2` — ToxinPred3, HemoPI2, MHCflurry
- `scores.round3` — BepiPred3, TemStaPro, SoDoPE, pLM4CPPs, GraphCPP
- `scores.construct` — construct 级别 SoDoPE, TemStaPro, BepiPred3
- `scores.structure` — OmegaFold pLDDT
- `scores.pdb_eval` — SASA, Aggrescan3D
- `rankings` — 各阶段排名 (round1/round3/round4/round7)
"""
    (FINAL_DIR / "README.md").write_text(readme, encoding="utf-8")
    log(f"  README.md 已保存")


def main():
    start_time = time.time()
    log("=" * 55)
    log("  Round 7: 最终输出 — per-construct 文件夹")
    log("=" * 55)

    # 清理旧输出
    if CONSTRUCTS_DIR.exists():
        shutil.rmtree(CONSTRUCTS_DIR)
    CONSTRUCTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 加载数据
    db = PipelineDB()
    log("加载完整数据（所有分数 + 排名 + 来源）...")
    constructs = load_all_construct_data(db)
    log(f"  已加载 {len(constructs)} 个 construct")
    if not constructs:
        log("❌ 无数据")
        db.close()
        return

    # 2. 排名计算
    log("计算综合评分 + 排名...")
    constructs = compute_rankings(constructs)

    # 3. 写入 final_ranking 表
    records = []
    for c in constructs:
        records.append({
            "construct_id": c["construct_id"],
            "candidate_id": c["candidate_id"] or 0,
            "channel": c["channel"],
            "composite_score": c["round7_score"],
            "rank": c["global_rank"],
            "rank_in_channel": c.get("channel_rank"),
            "weight_snapshot": json.dumps(WEIGHT_SNAPSHOT),
        })
    written = db.insert_final_ranking(records)
    log(f"  final_ranking 表: {written} 条")

    # 4. 输出 per-construct 文件夹
    log("生成 construct 文件夹...")
    for c in constructs:
        write_construct_folder(c)
    log(f"  {len(constructs)} 个文件夹已写入 {CONSTRUCTS_DIR}")

    # 5. 概要 README
    log("生成概要...")
    elapsed = time.time() - start_time
    write_summary_readme(constructs, elapsed)

    # 6. Checkpoint
    db.set_checkpoint("round7", "final", "done",
                      total=len(constructs), processed=written)

    # 7. 输出 Top10 + Bottom10 CSV 摘要
    log("输出 Top10 / Bottom10 CSV...")
    import csv
    top = sorted([c for c in constructs if c["channel"] == "top"],
                 key=lambda x: x["channel_rank"])
    bottom = sorted([c for c in constructs if c["channel"] == "bottom"],
                    key=lambda x: x["channel_rank"])
    top10 = top[:10]
    bot10 = bottom[:10]

    csv_fields = [
        "global_rank", "channel", "channel_rank",
        "construct_id", "position", "linker",
        "peptide_seq", "source_database", "source_accession",
        "round7_score",
    ]

    def csv_row(c):
        return {
            "global_rank": c["global_rank"],
            "channel": c["channel"],
            "channel_rank": c["channel_rank"],
            "construct_id": f"con_{c['construct_id']:04d}",
            "position": c["position"],
            "linker": c["linker"],
            "peptide_seq": c["sequences"]["peptide"],
            "source_database": c["source"],
            "source_accession": c["source_id"],
            "round7_score": c["round7_score"],
        }

    with open(FINAL_DIR / "top10.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for r in top10:
            w.writerow(csv_row(r))
    with open(FINAL_DIR / "bottom10.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for r in bot10:
            w.writerow(csv_row(r))
    log(f"  top10.csv, bottom10.csv 已保存")

    log(f"\n{'='*55}")
    log(f"  Round 7 完成!")
    log(f"  Construct 文件夹: {len(constructs)}")
    log(f"  输出目录: {CONSTRUCTS_DIR}/")
    log(f"  总耗时: {elapsed:.0f}s")
    log(f"{'='*55}")

    db.close()


if __name__ == "__main__":
    main()
