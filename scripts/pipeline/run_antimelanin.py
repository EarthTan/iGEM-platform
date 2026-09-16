#!/usr/bin/env python3
"""Run antimelanin pipeline end-to-end.

等价于：
    from src.pipeline.antimelanin.run import run
    from src.pipeline.antimelanin import AntimelaninConfig
    print(run(AntimelaninConfig()))

用法：
    python3 scripts/pipeline/run_antimelanin.py
    python3 scripts/pipeline/run_antimelanin.py --alpha 0.8
    python3 scripts/pipeline/run_antimelanin.py --top-n 200 --bottom-n 150

注意：所有通过的 construct 会**标 status='WIP'**（提案 §5）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline.antimelanin import AntimelaninConfig  # noqa: E402
from src.pipeline.antimelanin.run import run  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--alpha", type=float, default=1.0, help="人工干预系数（默认 1.0）")
    p.add_argument("--top-n", type=int, default=150)
    p.add_argument("--bottom-n", type=int, default=100)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = AntimelaninConfig(alpha=args.alpha, top_n=args.top_n, bottom_n=args.bottom_n)
    summary = run(cfg)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())