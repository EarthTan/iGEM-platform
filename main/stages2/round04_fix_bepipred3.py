"""
补跑 Construct 级 BepiPred3（修复并发太高导致超时问题）
- 从 all_constructs.csv 读取 300 个 construct
- 串行提交（Semaphore=1），不超时
- 更新综合分，重新输出 final/ 文件
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.client import ServiceClient
from main.stages2.common import log, make_dir, read_csv, setup_stage, write_csv, write_json

STAGE = "round04_enumerate"
STAGE_DIR = PROJECT_ROOT / "output2" / STAGE
W_CON_BEPI = 0.10
W_PEPTIDE = 0.40
W_SODOPE = 0.25
W_CON_ANOX = 0.20
W_TEMSTAPRO = 0.05

BATCH_SIZE = 50


async def run():
    setup_stage(STAGE)
    log("=" * 50)
    log("补跑 Construct 级 BepiPred3")
    log("=" * 50)

    constructs = read_csv(STAGE_DIR / "final" / "all_constructs.csv")
    log(f"读取 {len(constructs)} 个 construct")

    ids_and_seq = [(c["construct_id"], c["sequence"]) for c in constructs]
    chunks = []
    for i in range(0, len(ids_and_seq), BATCH_SIZE):
        chunk = ids_and_seq[i:i + BATCH_SIZE]
        chunks.append([{"sequence": s, "peptide_id": cid} for cid, s in chunk])

    log(f"共 {len(chunks)} 批 (每批 ≤{BATCH_SIZE})")

    client = ServiceClient(timeout=600.0)
    sem = asyncio.Semaphore(1)
    all_results: dict[str, float | None] = {}
    errors = 0

    async def process_chunk(chunk: list[dict]) -> None:
        nonlocal errors
        async with sem:
            try:
                result = await asyncio.wait_for(
                    client.predict_batch("bepipred3", chunk),
                    timeout=600.0,
                )
                if result.get("success") and result.get("results"):
                    for r in result["results"]:
                        cid = r.get("peptide_id", "unknown")
                        all_results[cid] = r.get("score")
                else:
                    errors += 1
                    for item in chunk:
                        all_results[item["peptide_id"]] = None
            except Exception as e:
                errors += 1
                log(f"  错误: {e}")
                for item in chunk:
                    all_results[item["peptide_id"]] = None
            log(f"  已完成 {sum(1 for v in all_results.values() if v is not None)}/{len(all_results)}")

    tasks = [process_chunk(chunk) for chunk in chunks]
    t0 = time.time()
    await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.time() - t0

    n_valid = sum(1 for v in all_results.values() if v is not None)
    log(f"\nBepiPred3: {n_valid}/{len(all_results)} 有效 ({elapsed:.0f}s, {errors} 错误)")

    await client.close()

    # 保存结果
    scores_dir = make_dir(STAGE_DIR, "scores")
    with open(scores_dir / "construct_bepipred3.json") as f:
        old_data = json.load(f)
    old_data.update(all_results)
    write_json(scores_dir / "construct_bepipred3.json", old_data)
    log(f"已更新 scores/construct_bepipred3.json")

    # ── 更新综合分 ──
    def pf(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    for c in constructs:
        cid = c["construct_id"]
        c["construct_bepipred3"] = all_results.get(cid)
        weighted = 0.0
        total_w = 0.0
        ps = pf(c.get("peptide_weighted_score"))
        if ps is not None:
            weighted += ps * W_PEPTIDE
            total_w += W_PEPTIDE
        sodope = pf(c.get("sodope_score"))
        if sodope is not None:
            weighted += sodope * W_SODOPE
            total_w += W_SODOPE
        con_anox = pf(c.get("construct_anoxpepred"))
        if con_anox is not None:
            weighted += con_anox * W_CON_ANOX
            total_w += W_CON_ANOX
        con_bepi = pf(c.get("construct_bepipred3"))
        if con_bepi is not None:
            weighted += con_bepi * W_CON_BEPI
            total_w += W_CON_BEPI
        con_temsta = pf(c.get("construct_temstapro"))
        if con_temsta is not None:
            weighted += con_temsta * W_TEMSTAPRO
            total_w += W_TEMSTAPRO
        c["composite_score"] = round(weighted / total_w, 4) if total_w > 0 else None

    # ── 重新分组排序 ──
    import pandas as pd
    df = pd.DataFrame(constructs)
    df["group_key"] = df["peptide_id"] + "|" + df["linker_id"]

    df_top = df[df["channel"] == "top"].copy()
    top_best = df_top.loc[df_top.groupby("group_key")["composite_score"].idxmax()]
    top_best = top_best.sort_values("composite_score", ascending=False).head(30)
    top_keys = set(top_best["group_key"])

    df_top_out = df[df["group_key"].isin(top_keys)].copy()
    rank_map = {k: i + 1 for i, k in enumerate(top_best["group_key"])}
    df_top_out["group_rank"] = df_top_out["group_key"].map(rank_map)
    df_top_out = df_top_out.sort_values(["group_rank", "composite_score"], ascending=[True, False])
    df_top_out["rank"] = range(1, len(df_top_out) + 1)

    df_bottom = df[df["channel"] == "bottom"].copy()
    df_bottom = df_bottom.sort_values("composite_score", ascending=False)
    df_bottom["group_rank"] = 0
    df_bottom["rank"] = range(1, len(df_bottom) + 1)

    df_out = pd.concat([df_top_out, df_bottom], ignore_index=True)

    # ── 输出 ──
    final_dir = make_dir(STAGE_DIR, "final")
    df_top_out.to_csv(final_dir / "constructs_top.csv", index=False)
    df_bottom.to_csv(final_dir / "constructs_bottom.csv", index=False)
    df_out.to_csv(final_dir / "all_constructs.csv", index=False)

    # FASTA
    fasta_path = final_dir / "all_constructs.fasta"
    with open(fasta_path, "w") as f:
        for _, r in df_out.iterrows():
            f.write(f">{r['construct_id']} | {r['peptide_id']} | {r['linker_id']} | "
                    f"{r['position']} | score={r['composite_score']:.4f} | "
                    f"{r['length']}aa | channel={r['channel']}\n")
            f.write(r["sequence"] + "\n")

    # Round 5 input
    out_records = df_out.to_dict("records")
    round5_input = {
        "source_stage": STAGE,
        "n_constructs": len(out_records),
        "n_top": len(df_top_out),
        "n_bottom": len(df_bottom),
        "weights": {"peptide": 0.40, "sodope": 0.25, "construct_anox": 0.20,
                     "construct_bepi": 0.10, "temstapro": 0.05},
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
            "composite_score": c.get("composite_score"),
        } for c in out_records],
    }
    write_json(final_dir / "round5_input.json", round5_input)

    n_top_out = len(df_top_out)
    n_bottom_out = len(df_bottom)
    log(f"\n输出: {n_top_out} Top + {n_bottom_out} Bottom = {len(df_out)} constructs")
    log(f"FASTA: {fasta_path} ({len(df_out)} 条)")
    log(f"耗时: {time.time()-t0:.0f}s")


def main():
    asyncio.run(run())

if __name__ == "__main__":
    main()
