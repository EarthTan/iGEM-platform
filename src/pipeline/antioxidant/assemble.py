"""construct assembly + persistence — proposal §5.

对 Top 150 / Bottom 100 各肽，从 backbone_proteins + linkers 选一个 (骨架, linker)
组合（默认：第一个非空 backbone × 第一个非空 linker；构造器场景驱动在产品里实现，
这里只用占位骨架确保落库），组装成 construct，写到 constructs 表。

当前骨架 / linker 都是 PLACEHOLDER（src/setup/sql/12_create_constructs.sql）。
真实 backbone / linker 序列与场景绑定由前端 + 后端构造器计算服务负责，本脚本只
负责把"肽 × 占位骨架 × 占位 linker"的最小可行组合落库，让 constructs 表 schema
# 与示例数据齐备即可。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import db

log = logging.getLogger("antioxidant.assemble")


@dataclass
class ConstructParts:
    backbone_id: int
    backbone_name: str
    linker_id: int
    linker_name: str
    linker_seq: str


def fetch_default_parts() -> ConstructParts:
    """默认 backbone + linker（占位）。"""
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
    """[backbone] | [linker] | [peptide]  简单拼接。

    backbone_seq 在 DB 里是 PLACEHOLDER 字符串（length=0），实际构造器会拉
    真实序列；这里仅用于占位落库。
    """
    return f"{backbone_seq}|{linker_seq}|{peptide_seq}"


def load_peptide_sequence(peptide_id: int) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT sequence FROM peptides WHERE id = %s", (peptide_id,))
        row = cur.fetchone()
    return row["sequence"] if row else ""


def persist_constructs(df: pd.DataFrame, parts: ConstructParts, channel: str) -> int:
    """落库 constructs 表。返回写入行数。

    df 必须包含列：
      peptide_id, sequence, aopxsvm, status, composite_topical, composite_nano,
      composite_microneedle, composite_injection,
      assessed_hemo (= hemopi2.notna()), assessed_mhci (= mhcflurry.notna()),
      assessed_mhcii (= netmhcipan_pctrank.notna())
    """
    if df.empty:
        log.warning("empty df for channel=%s, nothing to persist", channel)
        return 0

    rows = []
    for _, r in df.iterrows():
        scores_json = {
            "aopxsvm":               None if pd.isna(r.get("aopxsvm")) else float(r["aopxsvm"]),
            "anoxpepred_frs":        None if pd.isna(r.get("anoxpepred_frs")) else float(r["anoxpepred_frs"]),
            "anoxpepred_chel":       None if pd.isna(r.get("anoxpepred_chel")) else float(r["anoxpepred_chel"]),
            "toxinpred3":            None if pd.isna(r.get("toxinpred3")) else float(r["toxinpred3"]),
            "hemopi2":               None if pd.isna(r.get("hemopi2")) else float(r["hemopi2"]),
            "mhcflurry":             None if pd.isna(r.get("mhcflurry")) else float(r["mhcflurry"]),
            "netmhcipan_pctrank":    None if pd.isna(r.get("netmhcipan_pctrank")) else float(r["netmhcipan_pctrank"]),
            "plm4cpps":              None if pd.isna(r.get("plm4cpps")) else float(r["plm4cpps"]),
            "algpred2":              None if pd.isna(r.get("algpred2")) else float(r["algpred2"]),
            "bepipred":              None if pd.isna(r.get("bepipred")) else float(r["bepipred"]),
            "aggrescan_a3v":         None if pd.isna(r.get("aggrescan_a3v")) else float(r["aggrescan_a3v"]),
        }
        delivery_json = {
            d: float(r[f"composite_{d}"]) if f"composite_{d}" in r.index else 0.0
            for d in ("topical", "nano", "microneedle", "injection")
        }
        rows.append((
            "antioxidant",
            "wound_care",
            parts.backbone_id,
            parts.linker_id,
            int(r["peptide_id"]),
            assemble_full_sequence(
                parts.backbone_name,   # 占位：用 backbone name 而不是真实 seq
                parts.linker_seq,
                r["sequence"],
            ),
            channel,
            r["status"],
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
    return len(rows)


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))