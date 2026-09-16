"""construct assembly + persistence — proposal §5.

抗黑素方向的特殊性（提案 §5）：
  - **所有 construct 标 `status='WIP'`**：因为 TIPred 99.5% 阳性无富集力，
    BLSAM-TIP 未上线生产 DB；本方向产出的不是"已筛出的抗黑素肽"，而是
    "按 TIPred 弱信号排出的待湿实验验证短名单"。
  - scores JSONB 里额外存 `tipred` 作为功能分；同时记 `func_score_meaning`
    明确"ranking_only_not_probability"。
  - 其它 11 项分数（抗菌完全同套）+ 4 种递送综合分照常落库。
  - `assessed_*` 字段照常落，便于后续湿实验追踪评估完整度。

score JSONB 包含 11 项基线分：
  - tipred            : 主功能分（提案 §0 唯一可用信号；99.5% 阳性、弱排序）
  - toxinpred3 / hemopi2 / mhcflurry : 安全门控（无 MHC-II 列；用独立表聚合）
  - netmhcipan_min_rank_pct / netmhcipan_min_sb_rank_pct / netmhcipan_has_sb
                       : MHC-II（来自独立表 netmhc_score 聚合）
  - netmhcipan_n_alleles : 有效预测 allele 数
  - plm4cpps / algpred2 / bepipred3 / aggrescan_a3v : 可开发性 + B 细胞表位 + 聚集
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import db

log = logging.getLogger("antimelanin.assemble")


@dataclass
class ConstructParts:
    backbone_id: int
    backbone_name: str
    linker_id: int
    linker_name: str
    linker_seq: str


def fetch_default_parts() -> ConstructParts:
    """默认 backbone + linker（占位），与抗菌/抗氧化同套。"""
    with db.cursor() as cur:
        cur.execute("SELECT id, name FROM backbone_proteins ORDER BY id LIMIT 1")
        bb = cur.fetchone()
        cur.execute("SELECT id, name, sequence FROM linkers ORDER BY id LIMIT 1")
        lk = cur.fetchone()
    return ConstructParts(
        backbone_id=bb["id"],
        backbone_name=bb["name"],
        linker_id=lk["id"],
        linker_name=lk["name"],
        linker_seq=lk["sequence"],
    )


def assemble_full_sequence(backbone_seq: str, linker_seq: str, peptide_seq: str) -> str:
    """[backbone] | [linker] | [peptide] 简单拼接，与抗菌/抗氧化完全相同。"""
    return f"{backbone_seq}|{linker_seq}|{peptide_seq}"


def load_peptide_sequence(peptide_id: int) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT sequence FROM peptides WHERE id = %s", (peptide_id,))
        row = cur.fetchone()
    return row["sequence"] if row else ""


def persist_constructs(
    df: pd.DataFrame,
    parts: ConstructParts,
    channel: str,
    review_status: str = "human_review_pending",
    score_meaning: str = "ranking_only_not_probability",
) -> int:
    """落库 constructs 表。返回写入行数。

    抗黑素方向特有的处理：
      - 把 `status='passed'` 改成 `status='WIP'`（提案 §5：全方向标 WIP）
      - scores JSONB 多存 `tipred` (主功能分) 和 `func_score_meaning`
      - direction='antimelanin'

    df 必须包含列：
      peptide_id, sequence, tipred,
      composite_topical, composite_nano, composite_microneedle, composite_injection,
      assessed_hemo (= hemopi2.notna()), assessed_mhci (= mhcflurry.notna()),
      assessed_mhcii (= netmhcipan_has_sb / netmhcipan_pctrank 是否非空)
    """
    if df.empty:
        log.warning("empty df for channel=%s, nothing to persist", channel)
        return 0

    rows = []
    for _, r in df.iterrows():
        scores_json = {
            "tipred":                   None if pd.isna(r.get("tipred")) else float(r["tipred"]),
            "func_score_meaning":       score_meaning,
            "toxinpred3":               None if pd.isna(r.get("toxinpred3")) else float(r["toxinpred3"]),
            "hemopi2":                  None if pd.isna(r.get("hemopi2")) else float(r["hemopi2"]),
            "mhcflurry":                None if pd.isna(r.get("mhcflurry")) else float(r["mhcflurry"]),
            # netMHCIIpan（从 netmhc_score 表聚合的字段，与抗菌同套）
            "netmhcipan_min_rank_pct":    None if pd.isna(r.get("netmhcipan_min_rank_pct")) else float(r["netmhcipan_min_rank_pct"]),
            "netmhcipan_min_sb_rank_pct": None if pd.isna(r.get("netmhcipan_min_sb_rank_pct")) else float(r["netmhcipan_min_sb_rank_pct"]),
            "netmhcipan_has_sb":          None if pd.isna(r.get("netmhcipan_has_sb")) else bool(r["netmhcipan_has_sb"]),
            "netmhcipan_n_alleles":       None if pd.isna(r.get("netmhcipan_n_alleles")) else int(r["netmhcipan_n_alleles"]),
            "plm4cpps":                 None if pd.isna(r.get("plm4cpps")) else float(r["plm4cpps"]),
            "algpred2":                 None if pd.isna(r.get("algpred2")) else float(r["algpred2"]),
            "bepipred3":                None if pd.isna(r.get("bepipred3")) else float(r["bepipred3"]),
            "aggrescan_a3v":            None if pd.isna(r.get("aggrescan_a3v")) else float(r["aggrescan_a3v"]),
        }
        delivery_json = {
            d: float(r[f"composite_{d}"]) if f"composite_{d}" in r.index else 0.0
            for d in ("topical", "nano", "microneedle", "injection")
        }
        # 抗黑素方向：所有通过安全门控的 construct 一律标 WIP（提案 §5）
        wip_status = "WIP" if r.get("status") == "passed" else r["status"]
        rows.append((
            "antimelanin",
            "depigmentation",
            parts.backbone_id,
            parts.linker_id,
            int(r["peptide_id"]),
            assemble_full_sequence(
                parts.backbone_name,
                parts.linker_seq,
                r["sequence"],
            ),
            channel,
            wip_status,
            None if pd.isna(r.get("rank")) else int(r["rank"]),
            json_dumps(scores_json),
            json_dumps(delivery_json),
            bool(r.get("assessed_hemo", False)),
            bool(r.get("assessed_mhci", False)),
            bool(r.get("assessed_mhcii", False)),
        ))

    sql = """
        INSERT INTO constructs
            (direction, scenario, backbone_id, linker_id, peptide_id, full_sequence,
             channel, status, rank, scores, delivery_scores,
             assessed_hemo, assessed_mhci, assessed_mhcii)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
        ON CONFLICT (direction, backbone_id, linker_id, peptide_id) DO UPDATE
            SET status=EXCLUDED.status,
                scores=EXCLUDED.scores,
                delivery_scores=EXCLUDED.delivery_scores,
                rank=EXCLUDED.rank,
                assessed_hemo=EXCLUDED.assessed_hemo,
                assessed_mhci=EXCLUDED.assessed_mhci,
                assessed_mhcii=EXCLUDED.assessed_mhcii
    """
    with db.connect(readonly=False) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    log.info("persisted %d constructs (channel=%s, status='%s')",
             len(rows), channel, wip_status if rows else "-")
    return len(rows)


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))