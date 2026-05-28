"""
Round 8: AlphaFold3 — Top 10 + Bottom 10 高精度结构预测。

对 Round 7 最终排名中的 Top 10 / Bottom 10 constructs 运行 AlphaFold3，
输出 mmCIF 文件及置信度指标到 construct 文件夹。

用法:
    uv run python -m main.stages4.s4_round08_af3
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.stages4.s4_db import PipelineDB

TOP_N = 10
BOTTOM_N = 10
TOTAL = TOP_N + BOTTOM_N

WORKSPACE = Path("/tmp/af3_round8")
CONSTRUCTS_DIR = PROJECT_ROOT / "output4" / "final" / "constructs"

AF3_IMAGE = "alphafold3"
AF3_MODEL_DIR = Path("/home/lenovo/af_models")
AF3_DATABASE_DIR = Path("/home/lenovo/public_databases")
AF3_TIMEOUT = 14400  # 4 hours per prediction


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_targets(db: PipelineDB) -> list[dict]:
    """从 final_ranking 表加载 Top N + Bottom N constructs."""
    conn = db.connect()
    rows = conn.execute(f"""
        (
            SELECT c.construct_id, c.full_sequence, c.channel
            FROM constructs c
            JOIN final_ranking fr ON fr.construct_id = c.construct_id
            WHERE fr.channel = 'top'
            ORDER BY fr.rank_in_channel
            LIMIT {TOP_N}
        )
        UNION ALL
        (
            SELECT c.construct_id, c.full_sequence, c.channel
            FROM constructs c
            JOIN final_ranking fr ON fr.construct_id = c.construct_id
            WHERE fr.channel = 'bottom'
            ORDER BY fr.rank_in_channel
            LIMIT {BOTTOM_N}
        )
    """).fetchall()
    return [
        {"construct_id": r[0], "full_sequence": r[1], "channel": r[2]}
        for r in rows
    ]


def write_af3_input(construct_id: int, sequence: str, input_dir: Path) -> str:
    """生成 AF3 输入 JSON，返回 job_name."""
    job_name = f"igemsilk_con{construct_id:04d}"
    payload = {
        "name": job_name,
        "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": sequence}}],
        "dialect": "alphafold3",
        "version": 3,
    }
    (input_dir / f"{job_name}.json").write_text(json.dumps(payload, indent=2))
    return job_name


def run_af3_docker(
    job_name: str, input_dir: Path, output_dir: Path
) -> subprocess.CompletedProcess:
    """调用 docker run alphafold3 ..."""
    cmd = [
        "docker", "run", "--rm", "--gpus", "all",
        "--volume", f"{input_dir}:/root/af_input",
        "--volume", f"{output_dir}:/root/af_output",
        "--volume", f"{AF3_MODEL_DIR}:/root/models",
        "--volume", f"{AF3_DATABASE_DIR}:/root/public_databases",
        AF3_IMAGE,
        "python", "run_alphafold.py",
        "--json_path", f"/root/af_input/{job_name}.json",
        "--model_dir", "/root/models",
        "--output_dir", "/root/af_output",
    ]
    log(f"  Running AF3 (timeout={AF3_TIMEOUT}s) …")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=AF3_TIMEOUT)


def parse_confidence(af3_output_dir: Path, job_name: str) -> dict | None:
    """解析 summary_confidences.json，返回置信度指标。"""
    summary_path = af3_output_dir / f"{job_name}_summary_confidences.json"
    if not summary_path.exists():
        return None
    data = json.loads(summary_path.read_text())
    return {
        "ranking_score": data.get("ranking_score"),
        "ptm": data.get("ptm"),
        "iptm": data.get("iptm"),
        "fraction_disordered": data.get("fraction_disordered"),
        "has_clash": data.get("has_clash"),
        "chain_ptm": data.get("chain_ptm"),
        "chain_iptm": data.get("chain_iptm"),
    }


def update_construct_json(construct_id: int, confidence: dict | None, success: bool):
    """更新 construct.json，追加 AlphaFold3 结果。"""
    folder = CONSTRUCTS_DIR / f"con_{construct_id:04d}"
    json_path = folder / "construct.json"
    if not json_path.exists():
        return

    data = json.loads(json_path.read_text(encoding="utf-8"))

    af3_entry = {
        "success": success,
        "pdb_path": "alphafold3.cif" if success else None,
    }
    if confidence:
        af3_entry["ranking_score"] = confidence["ranking_score"]
        af3_entry["ptm"] = confidence["ptm"]
        af3_entry["iptm"] = confidence["iptm"]
        af3_entry["fraction_disordered"] = confidence["fraction_disordered"]
        af3_entry["has_clash"] = confidence["has_clash"]

    data["scores"]["structure"]["alphafold3"] = af3_entry

    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    log("=" * 55)
    log("  Round 8: AlphaFold3 — Top 10 + Bottom 10")
    log("=" * 55)

    db = PipelineDB()

    # 1. 加载目标
    targets = get_targets(db)
    log(f"  目标 constructs: {len(targets)} (Top {TOP_N} + Bottom {BOTTOM_N})")
    db.close()

    # 2. 准备工作空间
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # 3. 逐条预测
    completed = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    for i, t in enumerate(targets):
        cid = t["construct_id"]
        seq = t["full_sequence"]
        channel = t["channel"]
        construct_folder = CONSTRUCTS_DIR / f"con_{cid:04d}"
        cif_path = construct_folder / "alphafold3.cif"

        log(f"[{i+1}/{TOTAL}] con_{cid:04d} ({channel}, {len(seq)} aa)")

        # 跳过已完成
        if cif_path.exists():
            log("  alphafold3.cif 已存在，跳过")
            skipped += 1
            continue

        job_name = write_af3_input(cid, seq, WORKSPACE)

        try:
            proc = run_af3_docker(job_name, WORKSPACE, WORKSPACE)
        except subprocess.TimeoutExpired:
            log(f"  ❌ 超时 ({AF3_TIMEOUT}s)")
            failed += 1
            continue
        except Exception as exc:
            log(f"  ❌ Docker 错误: {exc}")
            failed += 1
            continue

        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:] if proc.stderr else ""
            log(f"  ❌ AF3 失败 (exit={proc.returncode}): {stderr_tail}")
            failed += 1
            continue

        # 4. 解析输出
        af3_out = WORKSPACE / job_name
        if not af3_out.exists():
            log(f"  ❌ 输出目录未找到: {af3_out}")
            failed += 1
            continue

        # 复制 mmCIF
        top_cif = af3_out / f"{job_name}_model.cif"
        if top_cif.exists():
            construct_folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(top_cif, cif_path)
            log(f"  ✓ {cif_path.name} 已保存")
        else:
            # fallback: 找任意 .cif
            cif_files = sorted(af3_out.glob("*.cif"))
            if cif_files:
                shutil.copy2(cif_files[0], cif_path)
                log(f"  ✓ {cif_files[0].name} → {cif_path.name}")
            else:
                log(f"  ❌ 无 .cif 输出")
                failed += 1
                continue

        # 5. 解析置信度
        confidence = parse_confidence(af3_out, job_name)
        if confidence:
            def _fmt(v):
                return f"{v:.4f}" if v is not None else "N/A"
            log(f"  ranking_score={_fmt(confidence['ranking_score'])}  "
                f"ptm={_fmt(confidence['ptm'])}  "
                f"iptm={_fmt(confidence['iptm'])}")
        else:
            log(f"  ⚠ 未找到 summary_confidences.json")

        # 6. 更新 construct.json
        update_construct_json(cid, confidence, success=True)

        completed += 1
        elapsed_h = (time.time() - start_time) / 3600
        log(f"  总耗时: {elapsed_h:.1f}h  |  已完成 {completed}/{TOTAL}")

        # 7. 清理临时文件
        shutil.rmtree(af3_out, ignore_errors=True)
        input_json = WORKSPACE / f"{job_name}.json"
        if input_json.exists():
            input_json.unlink()

    # 8. 最终统计
    total_elapsed = time.time() - start_time
    log(f"\n{'=' * 55}")
    log(f"  Round 8 完成!")
    log(f"  成功: {completed}  |  跳过: {skipped}  |  失败: {failed}")
    log(f"  总耗时: {total_elapsed / 3600:.1f}h")
    log(f"{'=' * 55}")

    shutil.rmtree(WORKSPACE, ignore_errors=True)


if __name__ == "__main__":
    main()
