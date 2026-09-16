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


def _normalize_allele(a: str) -> str:
    """规范化 MHC 等位基因写法为 'HLA-A*02:01' 形式。

    MHCflurry 模型能识别多种写法(已验证:HLA-A*02:01 / A*02:01 / HLA-A02:01
    在同一序列上给完全一致的 score),但 details['allele'] 是忠实存储,
    为了 wiki / 审计文件一致,这里统一为 'HLA-{gene}*{field}:{subfield}'。
    """
    s = a.strip()
    if not s:
        return s
    # 补 HLA- 前缀
    if not s.upper().startswith("HLA-"):
        s = "HLA-" + s
    # 统一大小写: HLA-, gene 字母, * 分隔
    # 接受 'A02:01' / 'A*02:01' / 'A*0201' / 'A02-01' 等,统一成 'A*02:01'
    # 拆 HLA- 与后面部分
    assert s.upper().startswith("HLA-")
    after = s[4:]
    # 拆 gene (字母) + 后面数字
    i = 0
    while i < len(after) and after[i].isalpha():
        i += 1
    gene = after[:i].upper()
    rest = after[i:].lstrip("*-")
    # rest 现在是 '02:01' 或 '0201',统一为 '02:01'
    if ":" in rest:
        field, _, subfield = rest.partition(":")
    elif len(rest) >= 4 and rest.isdigit():
        field, subfield = rest[:2], rest[2:]
    else:
        # 无法识别,原样返回(服务端会报错或回退)
        return s
    return f"HLA-{gene}*{field}:{subfield}"


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
    p.add_argument(
        "--alleles",
        type=str,
        default=None,
        help="逗号分隔 MHC 等位基因列表,例如 'A*02:01,A*11:01'。仅 mhcflurry 生效。"
             "每个 allele 派生独立 tool 名({tool}-A0201, {tool}-A1101, ...)入库,"
             "checkpoints 独立,可中断续跑。",
    )
    args = p.parse_args()

    # 解析 alleles。空 = 不派生(走默认 allele)。
    alleles: list[str] | None = None
    if args.alleles:
        raw_alleles = [a.strip() for a in args.alleles.split(",") if a.strip()]
        alleles = [_normalize_allele(a) for a in raw_alleles]
        if not alleles:
            alleles = None

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

        # 多 allele 跑批:外层 allele 循环,内层原 batch 循环。
        # 每个 allele 派生 tool_name,checkpoint 独立,DB 已 (peptide_id, tool) upsert。
        if alleles:
            from enrich.lib.db import TOOL_LENGTH_RANGE

            def _allele_to_suffix(a: str) -> str:
                # "HLA-A*02:01" -> "A0201"; 与 anoxpepred-frs/chelating 派生约定一致
                # 去掉 HLA- 前缀,去 *, 去 :
                a2 = a.replace("HLA-", "").replace("*", "").replace(":", "")
                return a2

            for allele in alleles:
                suffix = _allele_to_suffix(allele)
                tool_name = f"{args.tool}-{suffix}"
                _log.info("===== allele loop | allele=%s | derived_tool=%s =====",
                          allele, tool_name)

                # 派生 tool 的 length range 与原 tool 共享(mhcflurry 都是 5..15)
                # TOOL_LENGTH_RANGE[tool_name] 在 db 层会 KeyError,所以临时补一条
                if tool_name not in TOOL_LENGTH_RANGE:
                    # 找最近的原 tool 配置
                    base = args.tool
                    if base in TOOL_LENGTH_RANGE:
                        TOOL_LENGTH_RANGE[tool_name] = TOOL_LENGTH_RANGE[base]

                # 每个 allele 独立 ckpt,避免 allele 间互相覆盖
                ckpt = load_checkpoint(tool_name)
                # 不继承 caller 改过的 last_peptide_id(避免污染),只继承 total_eligible
                ckpt.total_eligible = db.total_count_for_tool(tool_name) - ckpt.total_done
                done_set_allele = db.load_done_set(tool_name)
                _log.info(
                    "start tool=%s batch=%d eligible_remaining=%d done_so_far=%d done_set_size=%d from_id=%d",
                    tool_name, args.batch, ckpt.total_eligible, ckpt.total_done,
                    len(done_set_allele), ckpt.last_peptide_id,
                )

                _run_one_allele(
                    db=db, client=client, tool_name=tool_name, allele=allele,
                    args=args, ckpt=ckpt, done_set=done_set_allele,
                )
        else:
            _run_one_allele(
                db=db, client=client, tool_name=args.tool, allele=None,
                args=args, ckpt=ckpt, done_set=done_set,
            )

        client.close()


def _run_one_allele(*, db, client, tool_name: str, allele: str | None,
                    args, ckpt, done_set: set) -> None:
    """单 allele(或单 default) 主循环。"""
    n_batches = 0
    t_loop = time.time()
    elapsed_window: list[float] = []

    while True:
        batch_rows = db.fetch_remaining(
            tool_name, args.batch,
            after_id=ckpt.last_peptide_id,
            done_set=done_set,
        )
        if not batch_rows:
            _log.info("DONE tool=%s total_done=%d", tool_name, ckpt.total_done)
            break

        t0 = time.time()
        if args.concurrent > 1:
            scores = client.score_concurrent(batch_rows, args.concurrent, allele=allele)
        else:
            scores = client.score(batch_rows, allele=allele)
        elapsed = time.time() - t0
        elapsed_window.append(elapsed)
        if len(elapsed_window) > 20:
            elapsed_window.pop(0)

        upsert_rows = [
            (s.peptide_id, s.score, s.label, json_dumps_safe(s.details))
            for s in scores
        ]
        n_inserted = db.upsert_results(tool_name, upsert_rows)

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
            _log.info("LIMIT hit, stop. tool=%s done=%d", tool_name, ckpt.total_done)
            break
        if args.max_batches and n_batches >= args.max_batches:
            _log.info("MAX-BATCHES hit (%d), stop.", args.max_batches)
            break

    total_elapsed = time.time() - t_loop
    _log.info(
        "final tool=%s total=%d elapsed=%.1fs avg=%.1f seq/s",
        tool_name, ckpt.total_done, total_elapsed,
        ckpt.total_done / total_elapsed if total_elapsed > 0 else 0,
    )


if __name__ == "__main__":
    main()