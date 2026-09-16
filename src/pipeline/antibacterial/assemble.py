"""construct assembly + persistence — proposal §5.

与 antioxidant/assemble.py 完全对称：
  - 默认 backbone + linker 占位来自 backbone_proteins / linkers
  - 写入 constructs 表，direction='antibacterial'
  - scores JSONB 包含抗菌方向所需的全部基线分
  - 唯一索引保证幂等

抗菌方向的"场景"默认填 'wound_careing'（同 antioxidant）——构造器场景驱动选择
backbone + linker 的能力上线后可重跑覆盖此值。

score JSONB 包含 10 项基线分（与 antioxidant 比多 imfp_lg_amp 一项）：
  - amp_esm          : 主功能分
  - imfp_lg_amp      : 抗菌交叉确认
  - toxinpred3 / hemopi2 / mhcflurry / netmhcipan_pctrank : 安全门控四项
  - plm4cpps / algpred2 / bepipred3 / aggrescan_a3v       : 可开发性 + B 细胞表位 + 聚集
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import db

log = logging.getLogger("antibacterial.assemble")


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
      peptide_id, sequence, amp_esm, status,
      composite_topical, composite_nano, composite_microneedle, composite_injection,
      assessed_hemo (= hemopi2.notna()), assessed_mhci (= mhcflurry.notna()),
      assessed_mhcii (= netmhcipan_pctrank.notna())
    """
    if df.empty:
        log.warning("empty df for channel=%s, nothing to persist", channel)
        return 0

    rows = []
    for _, r in df.iterrows():
        scores_json = {
            "amp_esm":               None if pd.isna(r.get("amp_esm")) else float(r["amp_esm"]),
            "imfp_lg_amp":           None if pd.isna(r.get("imfp_lg_AMP")) else float(r["imfp_lg_AMP"]),
            "toxinpred3":            None if pd.isna(r.get("toxinpred3")) else float(r["toxinpred3"]),
            "hemopi2":               None if pd.isna(r.get("hemopi2")) else float(r["hemopi2"]),
            "mhcflurry":             None if pd.isna(r.get("mhcflurry")) else float(r["mhcflurry"]),
            # netMHCIIpan: 从 netmhc_score 表聚合的字段。
            # netmhcipan_min_rank_pct：所有 allele 中最低 %Rank，越低越强结合
            # netmhcipan_has_sb：True iff 至少一个 allele 为 Strong Binder（%Rank ≤ 2）
            # netmhcipan_n_alleles：有效预测 allele 数
            "netmhcipan_min_rank_pct":    None if pd.isna(r.get("netmhcipan_min_rank_pct")) else float(r["netmhcipan_min_rank_pct"]),
            "netmhcipan_min_sb_rank_pct": None if pd.isna(r.get("netmhcipan_min_sb_rank_pct")) else float(r["netmhcipan_min_sb_rank_pct"]),
            "netmhcipan_has_sb":          None if pd.isna(r.get("netmhcipan_has_sb")) else bool(r["netmhcipan_has_sb"]),
            "netmhcipan_n_alleles":       None if pd.isna(r.get("netmhcipan_n_alleles")) else int(r["netmhcipan_n_alleles"]),
            "plm4cpps":              None if pd.isna(r.get("plm4cpps")) else float(r["plm4cpps"]),
            "algpred2":              None if pd.isna(r.get("algpred2")) else float(r["algpred2"]),
            "bepipred3":             None if pd.isna(r.get("bepipred3")) else float(r["bepipred3"]),
            "aggrescan_a3v":         None if pd.isna(r.get("aggrescan_a3v")) else float(r["aggrescan_a3v"]),
        }
        delivery_json = {
            d: float(r[f"composite_{d}"]) if f"composite_{d}" in r.index else 0.0
            for d in ("topical", "nano", "microneedle", "injection")
        }
        rows.append((
            "antibacterial",
            "wound_careing",
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