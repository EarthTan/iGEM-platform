"""
ToxinPred3 同步补跑（socket 级超时）

asyncio.wait_for + httpx 的超时有 bug（挂死时无法取消）。
改用 requests.post(timeout=...) 的 socket 级超时，更可靠。

用法：
    uv run python -m main.stages2.round02_toxinpred3_sync
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
STAGE_DIR = OUTPUT_DIR / "round02_scoring"
SCORES_DIR = STAGE_DIR / "scores"
LOG_FILE = STAGE_DIR / "run.log"

from main.config import service_url

BATCH_SIZE = 200
PER_BATCH_TIMEOUT = 120   # 每批超时秒数
RESTART_THRESHOLD = 5     # 连续超时阈值，触发服务重启
TOXINPRED3_TOKEN = "toxinpred3"

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

# ToxinPred3 service URL
TOXINPRED3_URL = f"{service_url(TOXINPRED3_TOKEN)}/predict/batch"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def predict_batch_sync(chunk, timeout=PER_BATCH_TIMEOUT):
    """同步调用 ToxinPred3，带 socket 级超时。超时返回 None。"""
    payload = {"sequences": chunk}
    try:
        resp = requests.post(TOXINPRED3_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        return None
    except Exception as e:
        log(f"    请求异常: {e}")
        return None


def restart_service():
    """重启 ToxinPred3 服务（通过调用 /restart 端点）。"""
    try:
        url = f"{service_url(TOXINPRED3_TOKEN)}/restart"
        resp = requests.post(url, timeout=10)
        if resp.status_code == 200:
            log(f"    服务重启成功")
            return True
    except Exception as e:
        log(f"    服务重启失败: {e}")
    return False


def run():
    t0 = time.time()
    log("=" * 60)
    log("ToxinPred3 同步补跑 (requests + socket 超时)")

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

    # 分块
    chunks = []
    for i in range(0, total, BATCH_SIZE):
        chunk = peptides[i:i + BATCH_SIZE]
        chunks.append([{"sequence": p["sequence"], "peptide_id": p["peptide_id"]} for p in chunk])

    log(f"分块: {len(chunks)} 批 (≤{BATCH_SIZE}/批, 超时 {PER_BATCH_TIMEOUT}s)")

    # 串行处理
    results: dict[str, dict] = {}
    errors = 0
    timeouts = 0
    consecutive_timeouts = 0

    log(f"\n开始处理...")
    for idx, chunk in enumerate(chunks):
        chunk_start = time.time()
        result = predict_batch_sync(chunk)

        if result is None:
            errors += 1
            timeouts += 1
            consecutive_timeouts += 1
            for item in chunk:
                results[item["peptide_id"]] = {"score": None, "label": "TIMEOUT"}
            log(f"  ⏰ 批 {idx+1} 超时 ({PER_BATCH_TIMEOUT}s)")
        elif result.get("success") and result.get("results"):
            consecutive_timeouts = 0
            for r in result["results"]:
                pid = r.get("peptide_id", "unknown")
                results[pid] = {"score": r.get("score"), "label": r.get("label", "")}
        else:
            errors += 1
            consecutive_timeouts += 1
            for item in chunk:
                results[item["peptide_id"]] = {"score": None, "label": "ERROR"}

        # 连续超时 → 重启服务
        if consecutive_timeouts >= RESTART_THRESHOLD:
            log(f"  连续 {consecutive_timeouts} 批超时，重启服务...")
            restart_service()
            consecutive_timeouts = 0

        # 进度打印
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
    log(f"\n✅ ToxinPred3: {total:,} 完成, {errors} 错误({timeouts}超时), {n_valid:,} 有效")

    # 保存结果
    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    write_json(SCORES_DIR / "toxinpred3_results.json", results)

    # 加载已有结果
    hemopi2 = {}
    mhcflurry = {}
    for svc, target in [("hemopi2", hemopi2), ("mhcflurry", mhcflurry)]:
        p = SCORES_DIR / f"{svc}_results.json"
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
    log(f"Round 2 ToxinPred3 同步补跑完成")
    log(f"  ToxinPred3: {n_valid:,}/{total:,} 有效 ({n_missing_t3:,} 缺失)")
    log(f"  Top: {n_top:,}")
    if valid_scores:
        log(f"  综合分: mean={sum(valid_scores)/len(valid_scores):.4f}, "
            f"max={max(valid_scores):.4f}")
    log(f"  安全: {n_safe:,} / {n_caution:,} / {n_danger:,}")
    log(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    log(f"{'='*60}")


if __name__ == "__main__":
    run()
