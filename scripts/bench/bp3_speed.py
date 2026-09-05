"""bp3_speed.py — BepiPred-3.0 速度最大化基准测试

目标:把 BepiPred-3.0 在当前机器(RTX 5880 Ada + 125GB RAM)上能跑到的
吞吐量(seq/s)探到极限,以此决定是否值得跑 20M 全量。

3 个 phase:
  1. 单服务 / 单 client,扫 batch={50,100,200,500,1000} 找甜点
  2. 多服务 + 多 client 并行(2/4 个实例不同端口),看 GPU 真并行上限
  3. In-process 直接调 bp3,绕开 HTTP/uvicorn 看纯推理上限

输出:
  - 终端表格
  - /tmp/bp3_logs/bench_<timestamp>.json
  - 外推 20M 总耗时
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

FASTA_PATH = Path("/tmp/bp3_test_5000.fasta")
LOG_DIR = Path("/tmp/bp3_logs")
LOG_DIR.mkdir(exist_ok=True)


def load_test_seqs(n: int) -> list[str]:
    seqs: list[str] = []
    with FASTA_PATH.open() as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith(">"):
                seqs.append(s)
                if len(seqs) >= n:
                    break
    return seqs


# ---------------- HTTP bench ----------------

def _post_batch_sync(client: httpx.Client, port: int,
                     items: list[tuple[int, str]], timeout: float) -> dict:
    payload = {
        "sequences": [
            {"peptide_id": str(pid), "sequence": seq} for pid, seq in items
        ]
    }
    r = client.post(f"http://127.0.0.1:{port}/predict/batch",
                    json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def bench_single_client(sequences: list[str], port: int, batch_size: int,
                        n_batches: int, warmup: int = 1) -> dict:
    """单 client 顺序打同一服务。"""
    client = httpx.Client(timeout=600.0)
    for _ in range(warmup):
        items = [(j, sequences[j % len(sequences)]) for j in range(batch_size)]
        _post_batch_sync(client, port, items, 600.0)

    latencies: list[float] = []
    total_seqs = 0
    t_total0 = time.perf_counter()
    for i in range(n_batches):
        items = [(j, sequences[(i * batch_size + j) % len(sequences)])
                 for j in range(batch_size)]
        t0 = time.perf_counter()
        _post_batch_sync(client, port, items, 600.0)
        latencies.append(time.perf_counter() - t0)
        total_seqs += batch_size
    elapsed = time.perf_counter() - t_total0
    client.close()
    return {
        "phase": "single_client",
        "port": port,
        "batch_size": batch_size,
        "n_batches": n_batches,
        "total_seqs": total_seqs,
        "elapsed_sec": round(elapsed, 3),
        "throughput_seq_per_s": round(total_seqs / elapsed, 2),
        "latency_sec_median": round(statistics.median(latencies), 3),
        "latency_sec_p95": round(sorted(latencies)[int(0.95 * len(latencies))], 3),
        "latency_sec_min": round(min(latencies), 3),
        "latency_sec_max": round(max(latencies), 3),
    }


def bench_multi_client_round_robin(sequences: list[str], ports: list[int],
                                   batch_size: int, n_batches: int,
                                   n_workers: int) -> dict:
    """n_workers 个线程,RR 分发到 len(ports) 个端口。"""
    chunk = (n_batches + n_workers - 1) // n_workers
    latencies_per_worker: list[list[float]] = [[] for _ in range(n_workers)]

    def worker(wid: int) -> tuple[int, float]:
        client = httpx.Client(timeout=600.0)
        seqs_done = 0
        try:
            for k in range(chunk):
                idx = wid * chunk + k
                if idx >= n_batches:
                    break
                port = ports[idx % len(ports)]
                items = [(j, sequences[(idx * batch_size + j) % len(sequences)])
                         for j in range(batch_size)]
                t0 = time.perf_counter()
                _post_batch_sync(client, port, items, 600.0)
                latencies_per_worker[wid].append(time.perf_counter() - t0)
                seqs_done += batch_size
        finally:
            client.close()
        return seqs_done, 0.0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        list(ex.map(worker, range(n_workers)))
    elapsed = time.perf_counter() - t0
    total_seqs = n_batches * batch_size
    all_lat = [l for sub in latencies_per_worker for l in sub]
    return {
        "phase": "multi_client",
        "ports": ports,
        "batch_size": batch_size,
        "n_workers": n_workers,
        "n_batches": n_batches,
        "total_seqs": total_seqs,
        "elapsed_sec": round(elapsed, 3),
        "throughput_seq_per_s": round(total_seqs / elapsed, 2),
        "latency_sec_median": round(statistics.median(all_lat), 3),
        "latency_sec_p95": round(sorted(all_lat)[int(0.95 * len(all_lat))], 3),
        "latency_sec_min": round(min(all_lat), 3),
        "latency_sec_max": round(max(all_lat), 3),
    }


# ---------------- in-process 极限 ----------------

def bench_inprocess(sequences: list[str], batch_size: int,
                    n_batches: int) -> dict:
    """绕过服务,直接调 bp3。"""
    sys.path.insert(0, "/home/lenovo/Projects/iGEM-silk")
    os.environ.setdefault(
        "TORCH_HOME",
        "/home/lenovo/Projects/iGEM-silk/tools/models/fair-esm",
    )
    # noqa: 必须在 sys.path 后 import
    from bp3 import bepipred3  # type: ignore  # noqa
    import tempfile

    antigens_class = bepipred3.Antigens
    predictor_class = bepipred3.BP3EnsemblePredict
    esm_dir = Path("/home/lenovo/Projects/iGEM-silk/tools/BepiPred-3.0/esm_cache")
    esm_dir.mkdir(exist_ok=True)

    latencies: list[float] = []
    total_seqs = 0
    t0 = time.perf_counter()
    for i in range(n_batches):
        items = [(j, sequences[(i * batch_size + j) % len(sequences)])
                 for j in range(batch_size)]
        with tempfile.NamedTemporaryFile("w", suffix=".fasta",
                                         delete=False) as f:
            for pid, seq in items:
                f.write(f">{pid}\n{seq}\n")
            fasta_path = Path(f.name)
        t1 = time.perf_counter()
        try:
            antigens = antigens_class(
                fasta_file=fasta_path,
                esm_encoding_dir=esm_dir,
                add_seq_len=False,
            )
            predictor = predictor_class(
                antigens, rolling_window_size=7, top_pred_pct=0.2
            )
            predictor.run_bp3_ensemble()
        finally:
            fasta_path.unlink(missing_ok=True)
        latencies.append(time.perf_counter() - t1)
        total_seqs += batch_size
    elapsed = time.perf_counter() - t0
    return {
        "phase": "inprocess",
        "batch_size": batch_size,
        "n_batches": n_batches,
        "total_seqs": total_seqs,
        "elapsed_sec": round(elapsed, 3),
        "throughput_seq_per_s": round(total_seqs / elapsed, 2),
        "latency_sec_median": round(statistics.median(latencies), 3),
        "latency_sec_p95": round(sorted(latencies)[int(0.95 * len(latencies))], 3),
        "latency_sec_min": round(min(latencies), 3),
        "latency_sec_max": round(max(latencies), 3),
    }


# ---------------- phase 编排 ----------------

def run_phase1(sequences: list[str], ports: list[int]) -> list[dict]:
    print("\n=== Phase 1: 单服务 / 单 client / 扫 batch ===")
    out: list[dict] = []
    for bs in [50, 100, 200, 500, 1000]:
        n = 3 if bs <= 500 else 2
        if len(sequences) < bs * n:
            sequences = sequences * ((bs * n) // len(sequences) + 1)
        r = bench_single_client(sequences, ports[0], bs, n_batches=n, warmup=1)
        out.append(r)
        print(f"  batch={bs:4d}  thr={r['throughput_seq_per_s']:8.2f} seq/s"
              f"  lat(med)={r['latency_sec_median']:6.2f}s"
              f"  lat(p95)={r['latency_sec_p95']:6.2f}s")
    return out


def run_phase2(sequences: list[str], ports: list[int]) -> list[dict]:
    print(f"\n=== Phase 2: 多服务 / 多 client / ports={ports} ===")
    out: list[dict] = []
    for bs in [200, 500, 1000]:
        for nw in [len(ports), 2 * len(ports)]:
            n = 4 if bs <= 500 else 2
            if len(sequences) < bs * n * nw:
                sequences = sequences * ((bs * n * nw) // len(sequences) + 1)
            r = bench_multi_client_round_robin(
                sequences, ports, bs, n_batches=n, n_workers=nw
            )
            out.append(r)
            print(f"  batch={bs:4d}  workers={nw:2d}"
                  f"  thr={r['throughput_seq_per_s']:8.2f} seq/s"
                  f"  lat(med)={r['latency_sec_median']:6.2f}s")
    return out


def run_phase3(sequences: list[str]) -> list[dict]:
    print("\n=== Phase 3: In-process 极限(绕开 HTTP) ===")
    out: list[dict] = []
    for bs in [200, 500, 1000]:
        n = 3 if bs <= 500 else 2
        if len(sequences) < bs * n:
            sequences = sequences * ((bs * n) // len(sequences) + 1)
        try:
            r = bench_inprocess(sequences, bs, n_batches=n)
            out.append(r)
            print(f"  batch={bs:4d}  thr={r['throughput_seq_per_s']:8.2f} seq/s"
                  f"  lat(med)={r['latency_sec_median']:6.2f}s")
        except Exception as e:
            print(f"  batch={bs:4d}  ERROR: {e}")
    return out


def estimate_20m(results: list[dict], total: int) -> None:
    valid = [r for r in results if r.get("throughput_seq_per_s", 0) > 0]
    if not valid:
        return
    best = max(valid, key=lambda r: r["throughput_seq_per_s"])
    thr = best["throughput_seq_per_s"]
    sec = total / thr
    h = sec / 3600
    print(f"\n=== 20M 外推 ===")
    print(f"  最佳配置: phase={best['phase']}, "
          f"batch={best['batch_size']}, "
          f"workers={best.get('n_workers', 1)}, ports={best.get('ports', best.get('port'))}")
    print(f"  吞吐: {thr:.2f} seq/s")
    print(f"  {total/1e6:.1f}M 条预估: {h:.2f} 小时 ({h*60:.0f} 分钟)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", type=int, default=0,
                   help="0=全跑,1/2/3=单独跑某 phase")
    p.add_argument("--total", type=int, default=20_000_000)
    p.add_argument("--ports", type=str, default="8002",
                   help="逗号分隔端口列表")
    args = p.parse_args()

    ports = [int(x) for x in args.ports.split(",")]
    sequences = load_test_seqs(5000)
    lens = [len(s) for s in sequences]
    print(f"loaded {len(sequences)} test seqs, "
          f"len min={min(lens)} max={max(lens)} avg={sum(lens)/len(lens):.1f}aa")

    # 健康检查
    for port in ports:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5.0)
            r.raise_for_status()
            print(f"port {port}: alive")
        except Exception as e:
            print(f"port {port}: DEAD ({e})")
            sys.exit(1)

    results: list[dict] = []
    if args.phase in (0, 1):
        results += run_phase1(sequences, ports)
    if args.phase in (0, 2):
        results += run_phase2(sequences, ports)
    if args.phase in (0, 3):
        results += run_phase3(sequences)

    out_path = LOG_DIR / f"bench_{int(time.time())}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nresults -> {out_path}")
    estimate_20m(results, args.total)


if __name__ == "__main__":
    main()