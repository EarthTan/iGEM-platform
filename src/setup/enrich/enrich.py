#!/usr/bin/env python3
"""
enrich.py — 单工具 enrichment worker

用法:
    python3 -u src/setup/enrich/enrich.py --tool sodope --limit 200 --batch 100
    python3 -u src/setup/enrich/enrich.py --tool sodope --batch 1000
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src" / "setup"))

from enrich import lib as enrich_lib
DB = enrich_lib.DB
dispatch_batch = enrich_lib.dispatch_batch
load_checkpoint = enrich_lib.load_checkpoint
save_checkpoint = enrich_lib.save_checkpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("enrich")


def json_dumps_safe(d: dict) -> str:
    if d is None:
        return "{}"
    try:
        return json.dumps(d, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"_err": "details-serializable-failed"})


def format_eta(sec: float) -> str:
    if sec != sec or sec == float("inf"):
        return "inf"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tool", required=True)
    p.add_argument("--batch", type=int, default=1000)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--restart-from-id", type=int, default=None)
    p.add_argument("--max-batches", type=int, default=None)
    p.add_argument("--check-coverage", action="store_true")
    p.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="每个 batch 拆成 N 份并行打 service(用于打满 GPU)。hemopi2 建议 8。",
    )
    args = p.parse_args()

    with DB() as db:
        if args.check_coverage:
            rows = db.coverage()
            if not rows:
                print("[coverage] (no data yet)")
                return
            print(f"{'tool':<14} {'done':>12} {'eligible':>12} {'pct':>6}")
            for r in rows:
                print(f"{r['tool']:<14} {r['done']:>12} {r['eligible']:>12} {r['pct']:>5.2f}%")
            return

        # checkpoint
        if args.restart_from_id is not None:
            ckpt = load_checkpoint(args.tool)
            ckpt.last_peptide_id = args.restart_from_id
            ckpt.total_done = 0
        else:
            ckpt = load_checkpoint(args.tool)
        ckpt.total_eligible = db.total_count_for_tool(args.tool) - ckpt.total_done

        # 一次性 load done-set 到内存(加速 fetch_remaining)
        _log.info("loading done-set for %s ...", args.tool)
        done_set = db.load_done_set(args.tool)
        _log.info(
            "start tool=%s batch=%d eligible_remaining=%d done_so_far=%d done_set_size=%d from_id=%d",
            args.tool, args.batch, ckpt.total_eligible, ckpt.total_done,
            len(done_set), ckpt.last_peptide_id,
        )

        from enrich.lib.clients import get_client
        client = get_client(args.tool)
        try:
            client.health()
        except Exception as e:
            _log.error("FAIL: %s service not ready: %s", args.tool, e)
            sys.exit(2)

        n_batches = 0
        t_loop = time.time()
        elapsed_window: list[float] = []

        while True:
            batch_rows = db.fetch_remaining(
                args.tool, args.batch,
                after_id=ckpt.last_peptide_id,
                done_set=done_set,
            )
            if not batch_rows:
                _log.info("DONE tool=%s total_done=%d", args.tool, ckpt.total_done)
                break

            t0 = time.time()
            if args.concurrent > 1:
                scores = client.score_concurrent(batch_rows, args.concurrent)
            else:
                scores = client.score(batch_rows)
            elapsed = time.time() - t0
            elapsed_window.append(elapsed)
            if len(elapsed_window) > 20:
                elapsed_window.pop(0)

            upsert_rows = [
                (s.peptide_id, s.score, s.label, json_dumps_safe(s.details))
                for s in scores
            ]
            n_inserted = db.upsert_results(args.tool, upsert_rows)

            # 更新 done-set,后续 batch 不会重复
            for s in scores:
                done_set.add(s.peptide_id)

            last_id = batch_rows[-1][0]
            ckpt.last_peptide_id = last_id
            ckpt.total_done += n_inserted
            if elapsed_window:
                ckpt.estimated_seq_per_sec = len(batch_rows) / statistics.mean(elapsed_window)
            save_checkpoint(ckpt)

            rate = ckpt.estimated_seq_per_sec
            remaining = max(0, ckpt.total_eligible - ckpt.total_done)
            eta_sec = remaining / rate if rate > 0 else float("inf")
            pct = 100 * ckpt.total_done / max(1, ckpt.total_eligible + ckpt.total_done)

            n_err = sum(1 for s in scores if (s.label or "").startswith("ERROR"))
            _log.info(
                "batch done | last_id=%d size=%d inserted=%d errs=%d | elapsed=%.2fs | rate=%.1f seq/s | done=%d (%.1f%%) | ETA=%s",
                last_id, len(batch_rows), n_inserted, n_err,
                elapsed, rate,
                ckpt.total_done, pct, format_eta(eta_sec),
            )

            n_batches += 1
            if args.limit and ckpt.total_done >= args.limit:
                _log.info("LIMIT hit, stop. tool=%s done=%d", args.tool, ckpt.total_done)
                break
            if args.max_batches and n_batches >= args.max_batches:
                _log.info("MAX-BATCHES hit (%d), stop.", args.max_batches)
                break

        total_elapsed = time.time() - t_loop
        _log.info(
            "final tool=%s total=%d elapsed=%.1fs avg=%.1f seq/s",
            args.tool, ckpt.total_done, total_elapsed,
            ckpt.total_done / total_elapsed if total_elapsed > 0 else 0,
        )
        client.close()


if __name__ == "__main__":
    main()