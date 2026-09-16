"""construct assembly + persistence — proposal §7.

与 antibacterial/assemble.py 基本一致，差异：
  - direction = 'anti_inflammatory'
  - scenario  = 'inflammation'（占位；构造器场景驱动选择 backbone + linker 上线后可重跑覆盖）
  - status    = 'WIP'（提案 §0：本方向仍标 WIP；唯一可用 AIP 模型 imfp_lg_AIP
                 有训练泄漏 + 碎片盲点，候选名单为"启发式候选"，需湿实验确认）

scores JSONB 包含 14 项基线分（与 antibacterial 比多 5 项跨功能 + aip_specific）：
  - aip_imfp           : 主信号 raw（imfp_lg_AIP）
  - aip_specific       : 去跨功能混淆后信号
  - imfp_lg_acp/adp/ahp/amp : iMFP-LG 跨功能通道
  - aopxsvm / amp_esm  : 抗氧化 / 抗菌 跨功能
  - toxinpred3 / hemopi2 / mhcflurry / netmhcipan_pctrank : 安全门控四项
  - plm4cpps / algpred2 / bepipred3 / aggrescan_a3v       : 可开发性 + B 细胞表位 + 聚集
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import db

log = logging.getLogger("antiinflammatory.assemble")


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


def assemble_full_sequence(backbone_name: str, linker_seq: str, peptide_seq: str) -> str:
    """[backbone] | [linker] | [peptide]  简单拼接。

    backbone_name 在 DB 里是 PLACEHOLDER 字符串（length=0），实际构造器会拉
    真实序列；这里仅用于占位落库。
    """
    return f"{backbone_name}|{linker_seq}|{peptide_seq}"


def persist_constructs(df: pd.DataFrame, parts: ConstructParts, channel: str) -> int:
    """落库 constructs 表。返回写入行数。

    df 必须包含列：
      peptide_id, sequence, length, aip_imfp, aip_specific, status, len_bucket,
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
            # 主信号 + 跨功能去混淆后信号
            "aip_imfp":             None if pd.isna(r.get("aip_imfp")) else float(r["aip_imfp"]),
            "aip_specific":         None if pd.isna(r.get("aip_specific")) else float(r["aip_specific"]),
            "other_max":            None if pd.isna(r.get("other_max")) else float(r["other_max"]),
            # iMFP-LG 跨功能通道
            "imfp_lg_acp":          None if pd.isna(r.get("imfp_lg_ACP")) else float(r["imfp_lg_ACP"]),
            "imfp_lg_adp":          None if pd.isna(r.get("imfp_lg_ADP")) else float(r["imfp_lg_ADP"]),
            "imfp_lg_ahp":          None if pd.isna(r.get("imfp_lg_AHP")) else float(r["imfp_lg_AHP"]),
            "imfp_lg_amp":          None if pd.isna(r.get("imfp_lg_AMP")) else float(r["imfp_lg_AMP"]),
            # 抗氧化 / 抗菌 跨功能
            "aopxsvm":              None if pd.isna(r.get("aopxsvm")) else float(r["aopxsvm"]),
            "amp_esm":              None if pd.isna(r.get("amp-esm")) else float(r["amp-esm"]),
            # 安全门控四项
            "toxinpred3":           None if pd.isna(r.get("toxinpred3")) else float(r["toxinpred3"]),
            "hemopi2":              None if pd.isna(r.get("hemopi2")) else float(r["hemopi2"]),
            "mhcflurry":            None if pd.isna(r.get("mhcflurry")) else float(r["mhcflurry"]),
            # netMHCIIpan: 从 netmhc_score 表聚合的字段
            "netmhcipan_min_rank_pct":    None if pd.isna(r.get("netmhcipan_min_rank_pct")) else float(r["netmhcipan_min_rank_pct"]),
            "netmhcipan_min_sb_rank_pct": None if pd.isna(r.get("netmhcipan_min_sb_rank_pct")) else float(r["netmhcipan_min_sb_rank_pct"]),
            "netmhcipan_has_sb":          None if pd.isna(r.get("netmhcipan_has_sb")) else bool(r["netmhcipan_has_sb"]),
            "netmhcipan_n_alleles":       None if pd.isna(r.get("netmhcipan_n_alleles")) else int(r["netmhcipan_n_alleles"]),
            # 可开发性 + B 细胞表位 + 聚集
            "plm4cpps":             None if pd.isna(r.get("plm4cpps")) else float(r["plm4cpps"]),
            "algpred2":             None if pd.isna(r.get("algpred2")) else float(r["algpred2"]),
            "bepipred3":            None if pd.isna(r.get("bepipred3")) else float(r["bepipred3"]),
            "aggrescan_a3v":        None if pd.isna(r.get("aggrescan_a3v")) else float(r["aggrescan_a3v"]),
            # WIP 信号（与 antimelanin 一致）
            "func_score_meaning":   "ranking_only_not_probability",
        }
        delivery_json = {
            d: float(r[f"composite_{d}"]) if f"composite_{d}" in r.index else 0.0
            for d in ("topical", "nano", "microneedle", "injection")
        }
        # WIP 方向：所有 passed construct 落 WIP（提案 §0 + §7）
        status_to_persist = "WIP" if r["status"] == "passed" else r["status"]

        rows.append((
            "anti_inflammatory",
            "inflammation",
            parts.backbone_id,
            parts.linker_id,
            int(r["peptide_id"]),
            assemble_full_sequence(
                parts.backbone_name,
                parts.linker_seq,
                r["sequence"],
            ),
            channel,
            status_to_persist,
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
