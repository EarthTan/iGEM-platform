"""
Round 4：枚举 + Construct 级综合评分（双通道 + 融合活性变化）

枚举 Top 40 + Bottom 10 功能肽，构建全长融合蛋白 construct：
  - 2 种 Linker × 3 个插入位置（N/C/Both），SoDoPE 评分
  - Construct 级 re-score：全长 AnOxPePred + BepiPred3（原脚本缺失）
  - 活性变化比：construct_score / peptide_score（反映 scaffold 影响）
  - 分组排序输出 → 3D 结构预测

与原脚本差异：
  - 输出到 output2/
  - 使用 common.py 共享工具
  - TOP_GROUPS=30 → 30×3=90 个 construct
  - 新增 construct 级 re-score（AnOxPePred + BepiPred3）
  - 新增 Top + Bottom 双通道枚举
  - 新增活性变化比分析
  - 新综合分公式：肽综合分×0.40 + SoDoPE×0.25 + con_AnOxPePred×0.20 + con_BepiPred×0.10 + TemStaPro×0.05

用法：
    uv run python -m main.stages2.round04_enumerate

输入：
    output2/round03_heavy/final/top80.csv
    output2/round03_heavy/final/bottom10.csv（可选）
    data/silk.fasta
    data/linker.fasta

输出：
    output2/round04_enumerate/
    ├── README.md
    ├── run.log
    ├── enumeration.csv              ← 全部 construct 枚举清单
    ├── scores/                      ← 各服务原始返回
    ├── final/
    │   ├── constructs_top.csv       ← Top 90 constructs
    │   ├── constructs_bottom.csv    ← Bottom constructs（全部，无额外筛选）
    │   ├── all_constructs.csv       ← 全部
    │   ├── all_constructs.fasta     ← → 3D 预测
    │   ├── context_effect.csv       ← 游离 vs 融合活性变化
    │   └── round5_input.json        ← Round 5 配置
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"

from main.client import ServiceClient
from main.data_loader import load_fasta
from main.stages2.common import (
    OUTPUT_DIR, describe_dict, log, make_dir, read_csv, safe_gather,
    setup_stage, write_csv, write_json, chunk_list,
)

STAGE = "round04_enumerate"
STAGE_DIR = OUTPUT_DIR / STAGE

# ── 枚举参数 ──
TOP_PEPTIDES = 40           # Top 通道取前 N 条肽
TOP_GROUPS = 30             # Top 通道取前 N 组（每组 3 position = 90 construct）
HIS_TAG = "LEHHHHHH"

SELECTED_LINKERS = [
    ("Flex_GGGGSx1", "GGGGS", "短柔性"),
    ("Flex_GGGGSx2", "GGGGSGGGGS", "长柔性"),
]

POSITIONS = [
    ("N", "功能肽在 N 端"),
    ("C", "功能肽在 C 端"),
    ("Both", "两端"),
]

# ── 评分权重（PLAN2 更新版）──
W_PEPTIDE = 0.40       # 肽综合分（Round 3，7 服务加权）
W_SODOPE = 0.25        # SoDoPE 溶解度
W_CON_ANOX = 0.20      # Construct 级 AnOxPePred（新增）
W_CON_BEPI = 0.10      # Construct 级 BepiPred3（新增）
W_TEMSTAPRO = 0.05     # Construct 级 TemStaPro（可选）

# ── 并发控制 ──
SODOPE_CONCURRENCY = 10
ANOX_CONCURRENCY = 10
BEPI_CONCURRENCY = 5

BATCH_SIZE = 50
ACTIVITY_DROP_THRESHOLD = 0.8


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


def parse_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def call_service(
    client: ServiceClient,
    service: str,
    batch: list[dict],
    concurrency: int,
    timeout: float = 300.0,
) -> dict[str, float | None]:
    """并发调用微服务，返回 {construct_id: score}。"""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, float | None] = {}

    async def _call_chunk(chunk: list[dict]):
        async with sem:
            try:
                result = await asyncio.wait_for(
                    client.predict_batch(service, chunk),
                    timeout=timeout,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        cid = r.get("peptide_id", "unknown")
                        results[cid] = r.get("score")
            except Exception:
                for item in chunk:
                    results[item.get("peptide_id", "unknown")] = None

    chunks = chunk_list(batch, BATCH_SIZE)
    tasks = [_call_chunk(chunk) for chunk in chunks]
    await safe_gather(tasks, f"{service}")
    return results


def build_peptide_index(peptides: list[dict]) -> dict[str, dict]:
    idx = {}
    for p in peptides:
        pid = p["peptide_id"]
        idx[pid] = {
            "sequence": p["sequence"],
            "weighted_score": parse_float(p.get("weighted_score")),
            "anoxpepred": parse_float(p.get("anoxpepred")),
            "bepipred3": parse_float(p.get("bepipred3")),
        }
    return idx


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def run():
    start_time = time.time()
    setup_stage(STAGE)

    log("=" * 60)
    log("Round 4：枚举 + Construct 级综合评分")
    log("  双通道: Top 40 + Bottom 10")
    log("  Construct 级: AnOxPePred + BepiPred3 + SoDoPE")
    log("=" * 60)

    # ── 1. Scaffold ──
    silk_data = load_fasta(DATA_DIR / "silk.fasta")
    if not silk_data:
        log("❌ 未读取到 silk.fasta")
        return
    scaffold_full = silk_data[0]["sequence"]
    scaffold_core = strip_his6(scaffold_full)
    log(f"\n丝素 scaffold: {len(scaffold_core)} aa + His6 标签")

    # ── 2. Linker ──
    all_linkers = {l["id"]: l["sequence"] for l in load_fasta(DATA_DIR / "linker.fasta")}
    selected_linkers = [(lid, all_linkers[lid], desc)
                        for lid, _seq, desc in SELECTED_LINKERS
                        if lid in all_linkers]
    log(f"Linker: {len(selected_linkers)} 种")

    # ── 3. 加载肽（Top + Bottom 双通道）──
    top_path = OUTPUT_DIR / "round03_heavy" / "final" / "top80.csv"
    bottom_path = OUTPUT_DIR / "round03_heavy" / "final" / "bottom10.csv"

    if not top_path.exists():
        log(f"❌ 找不到 {top_path}")
        return

    top_peptides = read_csv(top_path)[:TOP_PEPTIDES]
    bottom_peptides = read_csv(bottom_path) if bottom_path.exists() else []
    log(f"\nTop 通道: {len(top_peptides)} 条肽")
    log(f"Bottom 通道: {len(bottom_peptides)} 条肽")

    top_idx = build_peptide_index(top_peptides)

    # ══════════════════════════════════════════════════════════════════
    # 枚举
    # ══════════════════════════════════════════════════════════════════
    log("\n🔗 枚举 construct...")
    constructs: list[dict] = []
    con_idx = 0

    for channel, peptides, idx in [
        ("top", top_peptides, top_idx),
        ("bottom", bottom_peptides, build_peptide_index(bottom_peptides)),
    ]:
        for pep in peptides:
            pid = pep["peptide_id"]
            pep_seq = pep["sequence"]
            pinfo = idx.get(pid, {})
            for lid, lseq, _ldesc in selected_linkers:
                for pos_name, _pos_desc in POSITIONS:
                    con_idx += 1
                    full_seq = build_construct(pep_seq, lseq, pos_name, scaffold_core)
                    constructs.append({
                        "construct_id": f"con_{con_idx:04d}",
                        "channel": channel,
                        "peptide_id": pid,
                        "peptide_sequence": pep_seq,
                        "peptide_weighted_score": pinfo.get("weighted_score"),
                        "peptide_anoxpepred": pinfo.get("anoxpepred"),
                        "peptide_bepipred3": pinfo.get("bepipred3"),
                        "linker_id": lid,
                        "linker_sequence": lseq,
                        "position": pos_name,
                        "sequence": full_seq,
                        "length": len(full_seq),
                    })

    n_top_cons = sum(1 for c in constructs if c["channel"] == "top")
    n_bottom_cons = sum(1 for c in constructs if c["channel"] == "bottom")
    total = len(constructs)
    log(f"  生成 {total} 个 construct（Top: {n_top_cons}, Bottom: {n_bottom_cons}）")

    basic_fields = ["construct_id", "channel", "peptide_id", "peptide_sequence",
                    "linker_id", "linker_sequence", "position", "length"]
    write_csv(STAGE_DIR / "enumeration.csv", basic_fields, constructs)

    # ── 评分 batch ──
    seq_batch = [{"sequence": c["sequence"], "peptide_id": c["construct_id"]} for c in constructs]
    client = ServiceClient(timeout=300.0)

    # ══════════════════════════════════════════════════════════════════
    # SoDoPE
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 SoDoPE — {total} 个")
    t0 = time.time()
    sodope_scores = await call_service(client, "sodope", seq_batch, SODOPE_CONCURRENCY)
    n_ok = sum(1 for v in sodope_scores.values() if v is not None)
    log(f"  → {time.time()-t0:.1f}s, {n_ok}/{total} 有效")
    write_json(make_dir(STAGE_DIR, "scores") / "sodope.json", sodope_scores)

    # ══════════════════════════════════════════════════════════════════
    # Construct 级 AnOxPePred（新增！原脚本缺失）
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 Construct 级 AnOxPePred — {total} 个")
    t0 = time.time()
    con_anox_scores = await call_service(client, "anoxpepred", seq_batch, ANOX_CONCURRENCY)
    n_ok = sum(1 for v in con_anox_scores.values() if v is not None)
    log(f"  → {time.time()-t0:.1f}s, {n_ok}/{total} 有效")
    write_json(make_dir(STAGE_DIR, "scores") / "construct_anoxpepred.json", con_anox_scores)

    # ══════════════════════════════════════════════════════════════════
    # Construct 级 BepiPred3（新增！原脚本缺失）
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 Construct 级 BepiPred3 — {total} 个")
    t0 = time.time()
    con_bepi_scores = await call_service(client, "bepipred3", seq_batch, BEPI_CONCURRENCY)
    n_ok = sum(1 for v in con_bepi_scores.values() if v is not None)
    log(f"  → {time.time()-t0:.1f}s, {n_ok}/{total} 有效")
    write_json(make_dir(STAGE_DIR, "scores") / "construct_bepipred3.json", con_bepi_scores)

    # ══════════════════════════════════════════════════════════════════
    # TemStaPro（可选）
    # ══════════════════════════════════════════════════════════════════
    log(f"\n📊 TemStaPro（可选）— {total} 个")
    health = await client.check_health(["temstapro"])
    temstapro_ok = health.get("temstapro", {}).get("available", False)
    if temstapro_ok:
        t0 = time.time()
        con_temsta_scores = await call_service(client, "temstapro", seq_batch, 2, timeout=600.0)
        n_ok = sum(1 for v in con_temsta_scores.values() if v is not None)
        log(f"  → {time.time()-t0:.1f}s, {n_ok}/{total} 有效")
        write_json(make_dir(STAGE_DIR, "scores") / "construct_temstapro.json", con_temsta_scores)
    else:
        con_temsta_scores = {}
        log(f"  → 未就绪，跳过")

    await client.close()

    # ══════════════════════════════════════════════════════════════════
    # 综合分 + 活性变化比
    # ══════════════════════════════════════════════════════════════════
    log(f"\n🧮 计算综合分 + 活性变化比...")

    for c in constructs:
        cid = c["construct_id"]
        c["sodope_score"] = sodope_scores.get(cid)
        c["construct_anoxpepred"] = con_anox_scores.get(cid)
        c["construct_bepipred3"] = con_bepi_scores.get(cid)
        c["construct_temstapro"] = con_temsta_scores.get(cid) if temstapro_ok else None

        # 活性变化比
        pep_anox = c["peptide_anoxpepred"]
        pep_bepi = c["peptide_bepipred3"]
        con_anox = c["construct_anoxpepred"]
        con_bepi = c["construct_bepipred3"]

        if pep_anox and con_anox and pep_anox > 0:
            ratio = con_anox / pep_anox
            c["anox_change_ratio"] = round(ratio, 4)
            c["anox_change_flag"] = "⚠ DROP" if ratio < ACTIVITY_DROP_THRESHOLD else "OK"
        else:
            c["anox_change_ratio"] = None
            c["anox_change_flag"] = "N/A"

        if pep_bepi and con_bepi and pep_bepi > 0:
            c["bepi_change_ratio"] = round(con_bepi / pep_bepi, 4)
        else:
            c["bepi_change_ratio"] = None

        # 综合分（新公式）
        weighted = 0.0
        total_w = 0.0

        ps = c["peptide_weighted_score"]
        if ps is not None:
            weighted += ps * W_PEPTIDE
            total_w += W_PEPTIDE
        if c["sodope_score"] is not None:
            weighted += c["sodope_score"] * W_SODOPE
            total_w += W_SODOPE
        if con_anox is not None:
            weighted += con_anox * W_CON_ANOX
            total_w += W_CON_ANOX
        if con_bepi is not None:
            weighted += con_bepi * W_CON_BEPI
            total_w += W_CON_BEPI
        if c["construct_temstapro"] is not None:
            weighted += c["construct_temstapro"] * W_TEMSTAPRO
            total_w += W_TEMSTAPRO

        c["composite_score"] = round(weighted / total_w, 4) if total_w > 0 else None

    # ══════════════════════════════════════════════════════════════════
    # 分组排序
    # ══════════════════════════════════════════════════════════════════
    import pandas as pd
    df = pd.DataFrame(constructs)
    df["group_key"] = df["peptide_id"] + "|" + df["linker_id"]

    # Top 通道：分组取最高分，取 TOP_GROUPS 组
    df_top = df[df["channel"] == "top"].copy()
    top_best = df_top.loc[df_top.groupby("group_key")["composite_score"].idxmax()]
    top_best = top_best.sort_values("composite_score", ascending=False).head(TOP_GROUPS)
    top_keys = set(top_best["group_key"])

    df_top_out = df[df["group_key"].isin(top_keys)].copy()
    rank_map = {k: i + 1 for i, k in enumerate(top_best["group_key"])}
    df_top_out["group_rank"] = df_top_out["group_key"].map(rank_map)
    df_top_out = df_top_out.sort_values(["group_rank", "composite_score"], ascending=[True, False])
    df_top_out["rank"] = range(1, len(df_top_out) + 1)

    # Bottom 通道：全部保留，按综合分降序
    df_bottom = df[df["channel"] == "bottom"].copy()
    df_bottom = df_bottom.sort_values("composite_score", ascending=False)
    df_bottom["group_rank"] = 0
    df_bottom["rank"] = range(1, len(df_bottom) + 1)

    df_out = pd.concat([df_top_out, df_bottom], ignore_index=True)

    # ══════════════════════════════════════════════════════════════════
    # 输出
    # ══════════════════════════════════════════════════════════════════
    final_dir = make_dir(STAGE_DIR, "final")

    df_top_out.to_csv(final_dir / "constructs_top.csv", index=False)
    df_bottom.to_csv(final_dir / "constructs_bottom.csv", index=False)
    df_out.to_csv(final_dir / "all_constructs.csv", index=False)

    n_top_out = len(df_top_out)
    n_bottom_out = len(df_bottom)
    log(f"\n  输出: {n_top_out} Top + {n_bottom_out} Bottom = {len(df_out)} 个 construct")

    # FASTA（全部 → 3D 预测）
    fasta_path = final_dir / "all_constructs.fasta"
    with open(fasta_path, "w") as f:
        for _, r in df_out.iterrows():
            f.write(f">{r['construct_id']} | {r['peptide_id']} | {r['linker_id']} | "
                    f"{r['position']} | score={r['composite_score']:.4f} | "
                    f"{r['length']}aa | channel={r['channel']}\n")
            f.write(r["sequence"] + "\n")
    log(f"  FASTA: {fasta_path} ({len(df_out)} 条)")

    # 活性变化比
    ctx_fields = ["construct_id", "channel", "peptide_id", "peptide_anoxpepred",
                  "construct_anoxpepred", "anox_change_ratio", "anox_change_flag",
                  "peptide_bepipred3", "construct_bepipred3", "bepi_change_ratio"]
    change_rows = [{
        k: c.get(k) for k in ctx_fields
    } for c in constructs if c["anox_change_ratio"] is not None or c["bepi_change_ratio"] is not None]
    write_csv(final_dir / "context_effect.csv", ctx_fields, change_rows)
    n_warn = sum(1 for r in change_rows if r.get("anox_change_flag") == "⚠ DROP")
    log(f"  活性变化: {len(change_rows)} 条, {n_warn} 个下降警告")

    # Round 5 输入配置（含 channel 标签，确保信息向后续轮次传递）
    out_records = df_out.to_dict("records")
    round5_input = {
        "source_stage": STAGE,
        "timestamp": datetime.now().isoformat(),
        "n_constructs": len(out_records),
        "n_top": n_top_out,
        "n_bottom": n_bottom_out,
        "weights": {
            "peptide": W_PEPTIDE, "sodope": W_SODOPE,
            "construct_anox": W_CON_ANOX, "construct_bepi": W_CON_BEPI,
            "temstapro": W_TEMSTAPRO if temstapro_ok else 0,
        },
        "constructs": [{
            "construct_id": c["construct_id"],
            "channel": c["channel"],
            "peptide_id": c["peptide_id"],
            "peptide_weighted_score": c.get("peptide_weighted_score"),
            "peptide_anoxpepred": c.get("peptide_anoxpepred"),
            "linker_id": c["linker_id"],
            "position": c["position"],
            "length": c["length"],
            "sodope_score": c.get("sodope_score"),
            "construct_anoxpepred": c.get("construct_anoxpepred"),
            "construct_bepipred3": c.get("construct_bepipred3"),
            "construct_temstapro": c.get("construct_temstapro"),
            "anox_change_ratio": c.get("anox_change_ratio"),
            "composite_score": c.get("composite_score"),
        } for c in out_records],
    }
    write_json(final_dir / "round5_input.json", round5_input)

    # ══════════════════════════════════════════════════════════════════
    # 统计 + README
    # ══════════════════════════════════════════════════════════════════
    elapsed = time.time() - start_time

    composite_vals = [c["composite_score"] for c in constructs if c["composite_score"] is not None]
    sodope_vals = [c["sodope_score"] for c in constructs if c["sodope_score"] is not None]
    con_anox_vals = [c["construct_anoxpepred"] for c in constructs if c["construct_anoxpepred"] is not None]

    stats = {
        "stage": STAGE,
        "elapsed_sec": round(elapsed, 1),
        "enumeration": {
            "top_peptides": len(top_peptides),
            "bottom_peptides": len(bottom_peptides),
            "linkers": len(selected_linkers),
            "positions": len(POSITIONS),
            "total_constructs": total,
        },
        "scoring": {
            "composite": describe_dict("composite", composite_vals),
            "sodope": describe_dict("sodope", sodope_vals),
            "construct_anoxpepred": describe_dict("construct_anoxpepred", con_anox_vals),
            "temstapro_available": temstapro_ok,
        },
        "output": {
            "top_constructs": n_top_out,
            "bottom_constructs": n_bottom_out,
            "fasta": str(fasta_path),
        },
    }
    write_json(STAGE_DIR / "stats.json", stats)

    # 分组摘要
    group_lines = []
    for _, r in top_best.iterrows():
        group_data = df[df["group_key"] == r["group_key"]]
        scores_str = " | ".join(
            f"{row['position']}={row['composite_score']:.4f}" if pd.notna(row['composite_score']) else f"{row['position']}=N/A"
            for _, row in group_data.sort_values("position").iterrows()
        )
        group_lines.append(f"| {rank_map[r['group_key']]:2d} | {r['peptide_id']:12s} | {r['linker_id']:25s} | {scores_str} | {r['composite_score']:.4f} |")

    top5_lines = "\n".join(group_lines[:5])

    # 分数分布文本（条件构建）
    dist_lines = []
    if composite_vals:
        m = sum(composite_vals) / len(composite_vals)
        dist_lines.append(f"- **综合分**: n={len(composite_vals)}, mean={m:.4f}, max={max(composite_vals):.4f}")
    else:
        dist_lines.append("- **综合分**: 无")
    if sodope_vals:
        m = sum(sodope_vals) / len(sodope_vals)
        dist_lines.append(f"- **SoDoPE**: n={len(sodope_vals)}, mean={m:.4f}")
    else:
        dist_lines.append("- **SoDoPE**: 无")
    if con_anox_vals:
        m = sum(con_anox_vals) / len(con_anox_vals)
        dist_lines.append(f"- **Construct AnOxPePred**: n={len(con_anox_vals)}, mean={m:.4f}")
    else:
        dist_lines.append("- **Construct AnOxPePred**: 无")

    readme = f"""# Round 4：枚举 + Construct 级评分 — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**耗时**: {elapsed:.0f} 秒

## 评分权重

| 维度 | 权重 | 说明 |
|------|------|------|
| 肽综合分（Round 3） | {W_PEPTIDE} | 7 服务加权综合分 |
| SoDoPE 溶解度 | {W_SODOPE} | Construct 级全长评分 |
| **Construct AnOxPePred** | {W_CON_ANOX} | **新增**：全长融合蛋白抗氧化评分 |
| **Construct BepiPred3** | {W_CON_BEPI} | **新增**：全长融合蛋白 B 细胞表位 |
| TemStaPro 热稳定性 | {W_TEMSTAPRO if temstapro_ok else '0（未就绪）'} | — |

## 双通道枚举

| 通道 | 肽数 | Linker | 位置 | Construct 数 | 输出数 |
|------|------|--------|------|-------------|--------|
| Top | {len(top_peptides)} | {len(selected_linkers)} | {len(POSITIONS)} | {n_top_cons} | {n_top_out} |
| Bottom | {len(bottom_peptides)} | {len(selected_linkers)} | {len(POSITIONS)} | {n_bottom_cons} | {n_bottom_out} |

## 活性变化比

AnOxPePred 活性下降警告（变化比 < {ACTIVITY_DROP_THRESHOLD}）: {n_warn} 个

变化比 > 1.0 = 融合后活性增强 | ≈ 1.0 = 不受 scaffold 影响 | < {ACTIVITY_DROP_THRESHOLD} = 显著下降

## Top 分组摘要

| 排名 | 肽 | Linker | N/C/Both 分 | 组最高分 |
|------|------|--------|-------------|----------|
{chr(10).join(group_lines)}

## 分数分布

{chr(10).join(dist_lines)}

## 输出

- `final/constructs_top.csv` — Top {n_top_out} constructs
- `final/constructs_bottom.csv` — Bottom {n_bottom_out} constructs
- `final/all_constructs.fasta` — {len(df_out)} 条 → Round 5 3D 预测
- `final/context_effect.csv` — 游离 vs 融合活性变化
"""
    (STAGE_DIR / "README.md").write_text(readme, encoding="utf-8")
    log(f"报告已写入: {STAGE_DIR / 'README.md'}")

    log(f"\n{'=' * 60}")
    log(f"Round 4 汇总")
    log(f"  枚举: {total} construct ({n_top_cons} Top + {n_bottom_cons} Bottom)")
    log(f"  输出: {n_top_out} Top + {n_bottom_out} Bottom → Round 5")
    log(f"  活性下降警告: {n_warn}")
    log(f"  耗时: {elapsed:.1f}s")
    log(f"{'=' * 60}")


def main():
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
