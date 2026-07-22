#!/usr/bin/env python3
"""
bench_one.py — 单服务 bench harness。

输入:服务 base URL(--url),序列文件(--input,一行一条),batch 大小(--batch),请求轮数(--rounds)。
输出(JSON line):
  {"tool": "...", "url":"...", "n_seqs":200, "batch":50, "rounds":4,
   "rtt_ms_p50":..., "rtt_ms_p95":..., "seqs_per_sec":..., "errors":...}

每轮:随机抽 batch 条序列,POST /predict/batch,记录 wall-clock。
最后一遍额外 POST /health 看延迟。

使用:
  python3 scripts/bench_one.py --url http://127.0.0.1:8003 \
      --input /tmp/test_peptides_200.txt --batch 50 --rounds 4 --tool toxinpred3

设计:sub-agent 调用,结果输出到 stdout(JSON),我自己 grep 统计。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--input", required=True, help="一行一条序列的文件")
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--tool", required=True)
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--warmup", type=int, default=1)
    args = p.parse_args()

    seqs = [s.strip() for s in Path(args.input).read_text().splitlines() if s.strip()]
    if len(seqs) < args.batch:
        sys.exit(f"input 只有 {len(seqs)} 条,不足 batch={args.batch}")
    print(f"[bench] tool={args.tool} url={args.url} n={len(seqs)} batch={args.batch} rounds={args.rounds}",
          file=sys.stderr, flush=True)

    # /info 看一下服务状态
    try:
        r = httpx.get(f"{args.url}/info", timeout=10)
        info = r.json()
        print(f"[info] {json.dumps(info, default=str)[:400]}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[info-fail] {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    # /health
    try:
        r = httpx.get(f"{args.url}/health", timeout=10)
        print(f"[health] {r.json()}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[health-fail] {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    latencies_ms = []
    total_seqs = 0
    total_time = 0.0
    errors = 0

    rounds_done = 0
    for i in range(args.rounds + args.warmup):
        sample = random.sample(seqs, args.batch)
        payload = {"sequences": [{"peptide_id": f"p{j}", "sequence": s} for j, s in enumerate(sample)]}
        t0 = time.time()
        try:
            r = httpx.post(f"{args.url}/predict/batch", json=payload, timeout=args.timeout)
            elapsed = time.time() - t0
            if r.status_code != 200:
                errors += 1
                print(f"[round{i}] HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr, flush=True)
                continue
            data = r.json()
            if not data.get("success"):
                errors += 1
                print(f"[round{i}] not success: {data}", file=sys.stderr, flush=True)
                continue
        except Exception as e:
            errors += 1
            elapsed = time.time() - t0
            print(f"[round{i}] {type(e).__name__}: {e} (took {elapsed:.2f}s)", file=sys.stderr, flush=True)
            continue

        if i < args.warmup:
            print(f"[warmup{i}] {elapsed*1000:.1f}ms for {args.batch} seqs", file=sys.stderr, flush=True)
            continue
        latencies_ms.append(elapsed * 1000)
        total_seqs += args.batch
        total_time += elapsed
        rounds_done += 1
        print(f"[round{i}] {elapsed*1000:.1f}ms for {args.batch} seqs", file=sys.stderr, flush=True)

    if rounds_done == 0:
        out = {"tool": args.tool, "url": args.url, "error": "no successful rounds", "errors": errors}
    else:
        out = {
            "tool": args.tool,
            "url": args.url,
            "n_seqs_in_pool": len(seqs),
            "batch": args.batch,
            "rounds_done": rounds_done,
            "errors": errors,
            "rtt_ms_p50": statistics.median(latencies_ms),
            "rtt_ms_p95": sorted(latencies_ms)[int(0.95 * len(latencies_ms))] if len(latencies_ms) >= 2 else latencies_ms[0],
            "seqs_per_sec": total_seqs / total_time,
            "total_seqs_sent": total_seqs,
        }

    print(json.dumps(out))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
