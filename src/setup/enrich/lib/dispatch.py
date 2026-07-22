"""dispatch.py — 给定 tool,调对应 client 跑批,返回 Score 列表。

worker 流程:
    while True:
        batch = db.fetch_remaining(tool, BATCH, after_id=ckpt.last_id)
        if not batch: break
        scores = dispatch_batch(tool, batch)
        db.upsert_results(tool, [(s.peptide_id, s.score, s.label, s.details) ...])
        ckpt.update(last_id=batch[-1][0], total_done+=...)
"""
from __future__ import annotations

import logging
from .clients import TOOL_REGISTRY


_log = logging.getLogger("enrich")


def dispatch_batch(tool: str, items: list[tuple[int, str]]):
    """调对应 client 的 .score()。

    items: [(peptide_id, sequence), ...]
    returns: Score list
    """
    if tool not in TOOL_REGISTRY:
        raise ValueError(f"tool {tool} not registered")
    return TOOL_REGISTRY[tool].score(items)
