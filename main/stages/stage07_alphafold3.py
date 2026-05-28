"""
阶段七：高精度 3D 结构预测（支持 OmegaFold / AlphaFold3）

读取阶段六 Top N construct，调用高精度结构预测服务生成 3D 结构，
提取置信度指标，保存 PDB 文件。后端可选：

  - omegafold   (默认, ~1 min/条, GPU, 推荐先用)
  - alphafold3  (~20-30 min/条, GPU, 只在 OmegaFold 之后跑前 5)

用法：
    uv run python -m main.stages.stage07_alphafold3                        # 默认 OmegaFold
    uv run python -m main.stages.stage07_alphafold3 --backend alphafold3   # 用 AF3

前提条件：
    - 对应服务的 Docker 容器运行中
    - OmegaFold: port 8204 (docker compose --profile gpu up -d omegafold)
    - AlphaFold3: port 8201 (docker compose --profile gpu up -d alphafold3)

输入：
    output/stage06_pdb_eval/final/all_ranked.csv  ← Top N 排名
    output/stage04_enumerate/scores/all_ranked.csv ← construct 序列

输出：
    output/stage07_{backend}/
    ├── pdb/              ← PDB 文件（每个 construct 一个）
    ├── scores/           ← 置信度和指标汇总
    ├── checkpoint.json   ← 进度检查点
    ├── final/            ← 汇总结果
    └── README.md
"""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient


# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

TOP_N = 10  # 限制 Top N
BACKEND = "omegafold"  # "omegafold" | "alphafold3"

# 各后端参数
BACKEND_CONFIG = {
    "omegafold": {
        "port": 8204,
        "poll_interval": 20,
        "timeout": 1800,
        "expected_per_job": 80,  # ~1.3 min
        "output_suffix": "omegafold",
        "description": "OmegaFold 单序列结构预测",
    },
    "alphafold3": {
        "port": 8201,
        "poll_interval": 60,
        "timeout": 14400,
        "expected_per_job": 1500,  # ~25 min
        "output_suffix": "alphafold3",
        "description": "AlphaFold3 高精度结构预测",
    },
}

# 输入依赖
STAGE6_CSV = PROJECT_ROOT / "output/stage06_pdb_eval/final/all_ranked.csv"
STAGE4_CSV = PROJECT_ROOT / "output/stage04_enumerate/scores/all_ranked.csv"


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _load_checkpoint(path: Path) -> set[str]:
    if path.exists():
        data = json.loads(path.read_text())
        return set(data.get("completed", []))
    return set()


def _save_checkpoint(path: Path, completed: set[str]) -> None:
    path.write_text(json.dumps({
        "completed": sorted(completed),
        "timestamp": datetime.now().isoformat(),
        "total": len(completed),
    }, indent=2))


def _save_summary(results: list[dict], output_dir: Path, backend: str) -> None:
    fieldnames = [
        "construct_id", "peptide_id", "peptide_sequence", "linker_id", "position",
        "length",
        f"{backend}_confidence",
        "elapsed_min", "success", "error",
        "esmfold_plddt", "sasa_score", "final_score",
    ]
    rows = []
    for r in results:
        rows.append({
            "construct_id": r["construct_id"],
            "peptide_id": r["peptide_id"],
            "peptide_sequence": r["peptide_sequence"],
            "linker_id": r["linker_id"],
            "position": r["position"],
            "length": r["length"],
            f"{backend}_confidence": r.get("confidence", ""),
            "elapsed_min": round(r.get("elapsed", 0) / 60, 1) if r.get("elapsed") else "",
            "success": r.get("success", False),
            "error": r.get("error", ""),
            "esmfold_plddt": r.get("esmfold_plddt", ""),
            "sasa_score": r.get("sasa_score", ""),
            "final_score": r.get("final_score", ""),
        })

    csv_path = output_dir / "final" / "all_scores.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  Summary CSV: {csv_path}")

    json_path = output_dir / "scores" / "all_results.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Summary JSON: {json_path}")


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def main():
    # ── 解析参数 ──
    backend = BACKEND
    top_n = TOP_N
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--backend" and i + 1 < len(args):
            backend = args[i + 1]
        elif arg == "--top-n" and i + 1 < len(args):
            top_n = int(args[i + 1])
    if backend not in BACKEND_CONFIG:
        print(f"❌ Unknown backend: {backend}. Options: {list(BACKEND_CONFIG.keys())}")
        sys.exit(1)

    cfg = BACKEND_CONFIG[backend]
    output_dir = PROJECT_ROOT / f"output/stage07_{cfg['output_suffix']}"
    pdb_dir = output_dir / "pdb"
    ckpt_path = output_dir / "checkpoint.json"

    t_start = time.monotonic()
    print("=" * 60)
    print(f"  阶段七：{cfg['description']} (backend={backend})")
    print("=" * 60)

    # ── 准备目录 ──
    for d in [output_dir, pdb_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # ── 加载 top N ──
    top_constructs: list[dict] = []
    reader = csv.DictReader(open(STAGE6_CSV))
    for i, r in enumerate(reader):
        if i >= top_n:
            break
        top_constructs.append(r)
    print(f"\nLoaded {len(top_constructs)} constructs from stage 6 ranking")

    # ── 加载序列 ──
    seq_map: dict[str, str] = {}
    for r in csv.DictReader(open(STAGE4_CSV)):
        seq_map[r["construct_id"]] = r["sequence"]

    # ── 加载 ESMFold pLDDT 作为对比基线 ──
    plddt_map: dict[str, str] = {}
    plddt_csv = PROJECT_ROOT / "output/stage05_esmfold/scores/all_plddt.csv"
    if plddt_csv.exists():
        for r in csv.DictReader(open(plddt_csv)):
            plddt_map[r.get("construct_id", "")] = r.get("plddt", "")

    # ── 检查点 ──
    completed = _load_checkpoint(ckpt_path)
    if completed:
        print(f"  Checkpoint: {len(completed)} already completed")

    # ── 构造任务列表 ──
    tasks = []
    for c in top_constructs:
        cid = c["construct_id"]
        if cid in completed:
            print(f"  ⏭  {cid} — already completed, skipping")
            continue
        seq = seq_map.get(cid, "")
        if not seq:
            print(f"  ⚠️   {cid} — sequence not found, skipping")
            continue
        tasks.append(c)

    if not tasks:
        print("\n✅ All tasks already completed!")
        _wrap_up(top_constructs, results, output_dir, pdb_dir, backend, cfg, t_start)
        _update_final_output(pdb_dir, backend)
        return

    n_total = len(tasks) + (len(completed) - len(set(c["construct_id"] for c in top_constructs if c["construct_id"] in completed)))
    print(f"\n  Total to process: {len(tasks)} constructs")
    print(f"  Estimated time: ~{len(tasks) * cfg['expected_per_job'] / 60:.0f} min")
    print()

    # ── 逐一提交预测（串行，独占 GPU）──
    results: list[dict] = []
    client = ServiceClient(timeout=30.0)

    try:
        for idx, c in enumerate(tasks):
            cid = c["construct_id"]
            seq = seq_map[cid]
            fmt = c["construct_format"]
            print(f"\n[{idx+1}/{len(tasks)}] {cid} ({c['peptide_sequence']} | {c['linker_id']} {c['position']})")
            print(f"  Sequence length: {len(seq)} aa | {fmt}")

            t_job = time.monotonic()
            result = await client.predict_structure_async(
                backend, seq,
                peptide_id=cid,
                poll_interval=cfg["poll_interval"],
                timeout=cfg["timeout"],
            )
            elapsed = time.monotonic() - t_job

            # 附加元数据
            result["construct_id"] = cid
            result["peptide_id"] = c["peptide_id"]
            result["peptide_sequence"] = c["peptide_sequence"]
            result["linker_id"] = c["linker_id"]
            result["position"] = c["position"]
            result["length"] = c["length"]
            result["elapsed"] = elapsed
            result["esmfold_plddt"] = plddt_map.get(cid, "")
            result["sasa_score"] = c.get("sasa_score", "")
            result["final_score"] = c.get("final_score", "")

            if result.get("success"):
                confidence = result.get("confidence", "N/A")
                print(f"  ✅ {cid} done in {elapsed/60:.1f} min, confidence={confidence}")

                # 保存 PDB（OmegaFold 直接返回 PDB，AF3 返回 mmCIF 需转换）
                pdb_content = result.get("pdb_content", "") or ""
                if backend == "alphafold3" and pdb_content.strip().startswith("data_"):
                    pdb_content = _mmcif_to_pdb(pdb_content)

                if pdb_content.strip():
                    pdb_path = pdb_dir / f"{cid}.pdb"
                    pdb_path.write_text(pdb_content)
                    print(f"     PDB saved: {pdb_path.relative_to(PROJECT_ROOT)} ({len(pdb_content)} bytes)")
                else:
                    print(f"     ⚠️  No PDB content in response")

                # 额外指标
                if conf := result.get("details", {}).get("confidence_metrics"):
                    print(f"     ptm={conf.get('ptm')}, iptm={conf.get('iptm')}, "
                          f"disordered={conf.get('fraction_disordered')}, clash={conf.get('has_clash')}")
            else:
                error = result.get("error", "Unknown error")
                print(f"  ❌ {cid} failed after {elapsed/60:.1f} min: {error}")

            results.append(result)
            completed.add(cid)
            _save_checkpoint(ckpt_path, completed)

            remaining = len(tasks) - idx - 1
            if remaining > 0:
                avg_time = sum(r.get("elapsed", 0) for r in results if r.get("elapsed"))
                avg_count = max(len([r for r in results if r.get("elapsed")]), 1)
                eta = remaining * (avg_time / avg_count)
                print(f"  ── Progress: {len(completed)}/{n_total} done, ~{eta/60:.0f} min remaining")

    finally:
        await client.close()

    # ── 汇总 ──
    _wrap_up(top_constructs, results, output_dir, pdb_dir, backend, cfg, t_start)
    _update_final_output(pdb_dir, backend)


# ── mmCIF→PDB（仅 AF3 需要） ──────────────────────────────────────────

def _mmcif_to_pdb(cif_content: str) -> str:
    from Bio.PDB.MMCIFParser import MMCIFParser
    from Bio.PDB.PDBIO import PDBIO
    import io, tempfile, os
    if not cif_content.strip():
        return ""
    with tempfile.NamedTemporaryFile(suffix=".cif", mode="w", delete=False) as f:
        f.write(cif_content)
        tmp_cif = f.name
    try:
        parser = MMCIFParser(QUIET=True)
        structure = parser.get_structure("model", tmp_cif)
        bio = PDBIO()
        bio.set_structure(structure)
        buf = io.StringIO()
        bio.save(buf)
        return buf.getvalue()
    except Exception as exc:
        print(f"  [WARN] mmCIF→PDB: {exc}")
        return ""
    finally:
        os.unlink(tmp_cif)


# ── 汇总输出 ──────────────────────────────────────────────────────────

def _wrap_up(top_constructs, results, output_dir, pdb_dir, backend, cfg, t_start):
    total_elapsed = time.monotonic() - t_start
    n_success = sum(
        1 for c in top_constructs
        if (pdb_dir / f"{c['construct_id']}.pdb").exists()
    )

    print(f"\n{'=' * 60}")
    print(f"  阶段七 ({backend}) 完成！总耗时: {total_elapsed/60:.1f} min")
    print(f"  Top {len(top_constructs)}: {n_success} PDBs generated")

    # README
    n_fail = len(top_constructs) - n_success
    readme = f"""# 阶段七：结构预测 ({backend}) — 报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**后端**: {backend}
**耗时**: {total_elapsed/60:.1f} 分钟
**输入**: Top {len(top_constructs)} construct（阶段六排名）

## 结果

| 状态 | 数量 |
|------|------|
| 成功 | {n_success} |
| 失败 | {n_fail} |

## 输出

- `pdb/` — PDB 结构文件
- `scores/` — 置信度指标
- `final/all_scores.csv` — 汇总表
"""
    (output_dir / "README.md").write_text(readme)
    print(f"  README: {output_dir / 'README.md'}")

    _save_summary(results if results else top_constructs, output_dir, backend)


def _update_final_output(pdb_dir: Path, backend: str = ""):
    """将新 PDB 复制到 final_output 中，始终带方法标签。

    保存 construct_{tag}.pdb （如 construct_af3.pdb / construct_omegafold.pdb）。
    不再生成 bare construct.pdb，避免方法来源歧义。

    backend → tag 映射：
      "alphafold3" → "af3"
      "omegafold"  → "omegafold"
    """
    TAG_MAP = {"alphafold3": "af3", "omegafold": "omegafold"}
    tag = TAG_MAP.get(backend, backend) if backend else ""
    if not tag:
        return
    final_out = PROJECT_ROOT / "output/final_output"
    if not final_out.exists():
        return
    n = 0
    for folder in final_out.iterdir():
        if not folder.is_dir() or not folder.name.startswith("con_"):
            continue
        parts = folder.name.split("_", 2)
        cid = f"{parts[0]}_{parts[1]}"
        src = pdb_dir / f"{cid}.pdb"
        if src.exists():
            shutil.copy2(src, folder / f"construct_{tag}.pdb")
            n += 1
    print(f"  final_output: {n} PDBs updated (construct_{tag}.pdb)")


if __name__ == "__main__":
    asyncio.run(main())
