"""
ToxinPred3 鲁棒补跑

解决 ToxinPred3 (sklearn ExtraTrees) 在处理某些序列时挂死的问题。
采用：
  - 小批次（200条/批），避免单批挂死损失太大
  - 每批 120s 超时
  - 超时后跳过该批，记录为错误，继续下一批
  - 全部完成后统计有效数

用法：
    uv run python -m main.stages2.round02_toxinpred3_robust
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE_DIR = OUTPUT_DIR / "round02_scoring"

from main.client import ServiceClient

LOG_FILE = STAGE_DIR / "run.log"
BATCH_SIZE = 200          # 小批次，避免单批损失太大
PER_BATCH_TIMEOUT = 120   # 每批超时秒数

ALL_WEIGHTS = {
    "anoxpepred": 0.50, "toxinpred3": 0.15, "algpred2": 0.10,
    "hemopi2": 0.10, "mhcflurry": 0.05,
}
SAFETY_THRESHOLDS = {
    "toxinpred3": {"caution": 0.60, "danger": 0.80},
    "algpred2": {"caution": 0.50, "danger": 0.70},
    "hemopi2": {"caution": 0.70, "danger": 0.85},
}
REVERSE = {"toxinpred3", "algpred2", "hemopi2", "mhcflurry"}
TOP_N = 10000
ROUND1_CSV = OUTPUT_DIR / "round01_lightweight" / "final" / "top100k.csv"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def predict_batch_with_timeout(client, service_name, chunk, timeout=PER_BATCH_TIMEOUT):
    """调用 predict_batch，带超时控制。超时返回 None。"""
    try:
        result = await asyncio.wait_for(
            client.predict_batch(service_name, chunk),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        return None
    except Exception as e:
        log(f"    异常: {e}")
        return None


async def run():
    t0 = time.time()
    log("=" * 60)
    log("ToxinPred3 鲁棒补跑（小批次 + 超时）")

    # 加载数据
    peptides = []
    with open(ROUND1_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["anoxpepred"] = float(row["anoxpepred"]) if row.get("anoxpepred") else None
            row["algpred2"] = float(row["algpred2"]) if row.get("algpred2") else None
            row["length"] = int(row["length"]) if row.get("length") else 0
            peptides.append(row)

    total = len(peptides)
    log(f"输入: {total:,} 条")

    # 小批次
    chunks = []
    for i in range(0, total, BATCH_SIZE):
        chunk = peptides[i:i + BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])

    log(f"分块: {len(chunks)} 批 (≤{BATCH_SIZE}/批, 超时 {PER_BATCH_TIMEOUT}s)")

    # 串行处理
    client = ServiceClient(timeout=PER_BATCH_TIMEOUT + 30)
    results: dict[str, dict] = {}
    errors = 0
    timeouts = 0

    log(f"\n开始处理...")
    for idx, chunk in enumerate(chunks):
        chunk_start = time.time()
        result = await predict_batch_with_timeout(client, "toxinpred3", chunk)

        if result is None:
            errors += 1
            timeouts += 1
            for item in chunk:
                results[item["peptide_id"]] = {"score": None, "label": "TIMEOUT"}
        elif result.get("success") and result.get("results"):
            for r in result["results"]:
                pid = r.get("peptide_id", "unknown")
                results[pid] = {"score": r.get("score"), "label": r.get("label", "")}
        else:
            errors += 1
            for item in chunk:
                results[item["peptide_id"]] = {"score": None, "label": "ERROR"}

        elapsed_batch = time.time() - chunk_start
        if (idx + 1) % 50 == 0 or idx == 0 or idx == len(chunks) - 1:
            elapsed = time.time() - t0
            done = (idx + 1) * BATCH_SIZE
            rate = done / elapsed if elapsed > 0 else 0
            remain = (total - done) / rate if rate > 0 else 0
            n_valid = sum(1 for v in results.values() if v.get("score") is not None)
            log(f"  {idx+1:3d}/{len(chunks)}批 | {done:>6,}/{total:,}条 | "
                f"有效={n_valid:>5,} | timeout={timeouts} | "
                f"{rate:.0f} seq/s | ETA {remain/60:.0f}min")

    n_valid = sum(1 for v in results.values() if v.get("score") is not None)
    log(f"\n✅ ToxinPred3: {total:,} 完成, {errors} 错误(含{timeouts}超时), {n_valid:,} 有效")
    await client.close()

    # 保存结果
    scores_dir = STAGE_DIR / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    write_json(scores_dir / "toxinpred3_results.json", results)

    # 加载已有结果
    hemopi2 = {}
    mhcflurry = {}
    for svc, target in [("hemopi2", hemopi2), ("mhcflurry", mhcflurry)]:
        p = scores_dir / f"{svc}_results.json"
        if p.exists():
            with open(p) as f:
                target.update(json.load(f))

    # 重算综合分
    log(f"\n合并分数 & 重算综合分...")
    scored = []
    for pep in peptides:
        pid = pep["peptide_id"]
        row = {k: pep.get(k) for k in ("peptide_id", "sequence", "length", "source",
                                        "anoxpepred", "algpred2")}
        row["toxinpred3"] = results.get(pid, {}).get("score")
        row["hemopi2"] = hemopi2.get(pid, {}).get("score") if hemopi2 else None
        row["mhcflurry"] = mhcflurry.get(pid, {}).get("score") if mhcflurry else None

        w_sum, w_total = 0.0, 0.0
        missing = []
        for svc, weight in ALL_WEIGHTS.items():
            raw = row.get(svc)
            if raw is None:
                missing.append(svc)
                continue
            norm = max(0.0, min(1.0, raw))
            if svc in REVERSE:
                norm = 1.0 - norm
            w_sum += norm * weight
            w_total += weight

        if missing:
            row["missing_services"] = ";".join(missing)
        row["weighted_score"] = round(w_sum / w_total, 4) if w_total > 0 else None

        flags = []
        for svc, cfg in SAFETY_THRESHOLDS.items():
            s = row.get(svc)
            if s is None:
                continue
            if s >= cfg["danger"]:
                flags.append(f"{svc}:danger({s:.3f})")
            elif s >= cfg["caution"]:
                flags.append(f"{svc}:caution({s:.3f})")
        row["safety_flag"] = ";".join(flags) if flags else "safe"
        scored.append(row)

    # 排序输出
    scored.sort(key=lambda x: x["weighted_score"] or 0, reverse=True)
    fieldnames = ["peptide_id", "sequence", "length", "source",
                  "anoxpepred", "toxinpred3", "algpred2", "hemopi2", "mhcflurry",
                  "weighted_score", "safety_flag"]

    final_dir = STAGE_DIR / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    with open(final_dir / "all_scored.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(scored)

    n_top = min(TOP_N, len(scored))
    with open(final_dir / "top10k.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(scored[:n_top])

    danger = [p for p in scored if "danger" in p.get("safety_flag", "")]
    with open(final_dir / "danger_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(danger)

    elapsed = time.time() - t0
    valid_scores = [p["weighted_score"] for p in scored if p["weighted_score"] is not None]
    n_safe = sum(1 for p in scored if p.get("safety_flag") == "safe")
    n_caution = sum(1 for p in scored if "caution" in p.get("safety_flag", ""))
    n_danger = len(danger)
    n_missing_t3 = sum(1 for p in scored if p.get("toxinpred3") is None)

    log(f"\n{'='*60}")
    log(f"Round 2 鲁棒补跑完成")
    log(f"  ToxinPred3: {n_valid:,}/{total:,} 有效 ({n_missing_t3:,} 缺失)")
    log(f"  Top: {n_top:,}")
    if valid_scores:
        log(f"  综合分: mean={sum(valid_scores)/len(valid_scores):.4f}, "
            f"max={max(valid_scores):.4f}")
    log(f"  安全: {n_safe:,} / {n_caution:,} / {n_danger:,}")
    log(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    log(f"{'='*60}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
