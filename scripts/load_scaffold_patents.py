#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load_scaffold_patents.py — 重组结构蛋白专利数据入库脚本

数据源: data/patent_sequence_reorganized_2026-08-05/patent_db_handoff/
目标表(已由 10_create_scaffold_patents.sql 建立):
    scaffold_patent_records   27 行 (16 有 FASTA + 11 无 FASTA)
    scaffold_experiments      12 行
    scaffold_groups           11 行

设计要点:
    1. 默认 DSN 走 lib.pitfalls.PG_DSN(IGEM_PG_DSN → 默认 igem_peptides)
    2. 启动期跑 pitfalls.validate_environment() 捕获 zcat 等环境坑
    3. 整段 DDL + DML 包在一个 psycopg2 事务里;中途失败整体回滚
    4. 11 条无 FASTA 记录按 (applicant, patent_family) 归一化匹配映射表,
       取 synthetic_record_id(合成键) + scaffold_group_key
    5. Yusong 2 条(有 FASTA,但不在 12 个实验组内)→ scaffold_group_key=NULL

用法:
    python3 scripts/load_scaffold_patents.py --apply-schema
    python3 scripts/load_scaffold_patents.py --apply-schema --dsn "host=... dbname=... user=... password=..."
    python3 scripts/load_scaffold_patents.py --data-dir data/patent_sequence_reorganized_2026-08-05/patent_db_handoff
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

# 引入项目惯例:DSN 默认值 + 启动期校验
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "setup" / "lib"))
from pitfalls import PG_DSN, validate_environment, PitfallError  # noqa: E402

SOURCE_VERSION = "2026-08-05"  # 与数据快照日期一致,DATE 列


# ---------- 工具函数 ----------

def norm(s: str) -> str:
    """(applicant, patent_family) 匹配键归一化:小写、逗号→空格、压缩空白。
    原始数据里 Co., Ltd. / FibroGen, Inc. 等逗号位置不一致,需要容错。"""
    s = (s or "").lower()
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def read_fasta(path: Path):
    """读单条 FASTA,返回拼接后的氨基酸序列;文件不存在/为空 → None。"""
    if not path or not path.is_file():
        return None
    seq = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(">"):
                continue
            seq.append(line)
    return "".join(seq) if seq else None


def load_mapping(path: Path):
    """解析 group_record_mapping.csv,返回四个结构:
        rec_to_group: record_id → scaffold_group_key(只 FASTA 行,Yusong 跳过)
        no_fasta:     (norm(applicant), norm(patent_family)) → (synthetic_record_id, scaffold_group_key)
        name_to_key:  group_name → scaffold_group_key(供 experiments 用)
        groups:       scaffold_group_key → scaffold_group_name(供 groups 维度表用)
    """
    rec_to_group = {}
    no_fasta = {}
    name_to_key = {}
    groups = {}

    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            gk = (r["group_key"] or "").strip()
            gn = (r["group_name"] or "").strip()
            rid = (r["record_id"] or "").strip()
            srid = (r["synthetic_record_id"] or "").strip()
            appl = (r["applicant"] or "").strip()
            pat = (r["patent_family"] or "").strip()

            if gn and gk and gk != "unmapped":
                name_to_key[gn] = gk
                groups[gk] = gn

            if rid:
                # 有 record_id(FASTA 行 / Yusong):Yusong 的 gk=unmapped 不写入
                if gk and gk != "unmapped":
                    rec_to_group[rid] = gk
                continue

            if not rid and srid and appl and pat and gk and gk != "unmapped":
                no_fasta[(norm(appl), norm(pat))] = (srid, gk)

    return rec_to_group, no_fasta, name_to_key, groups


def parse_int(s: str):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


# ---------- 数据导入 ----------

def import_records(cur, tsv_path: Path, fasta_dir: Path, rec_to_group, no_fasta):
    """导入 scaffold_patent_records。返回 (inserted, warn_no_match)。"""
    with tsv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    sql = """
        INSERT INTO scaffold_patent_records (
            record_id, fasta_filename, aa_sequence, length_aa,
            patent_family, applicant, protein_construct, product_intended_use,
            potential_application, regulatory_status, testing_summary,
            highest_evidence_level, fasta_availability, pdb_accession,
            patent_source_url, regulatory_evidence_url, confidence_limitations,
            scaffold_group_key, source_version
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (record_id) DO NOTHING
    """

    inserted = []
    warn_no_match = []
    for r in rows:
        rid = (r["Sequence record ID"] or "").strip()
        appl = (r["Applicant / assignee"] or "").strip()
        pat = (r["Patent / family"] or "").strip()
        fn = (r["FASTA filename"] or "").strip()

        if rid:
            record_id = rid
            group_key = rec_to_group.get(rid)  # Yusong → None
        else:
            hit = no_fasta.get((norm(appl), norm(pat)))
            if hit:
                record_id, group_key = hit
            else:
                # 兜底:合成键仍按 applicant_pat 给,组键归到 other_e1e2(应不会触发)
                record_id = f"{appl}_{pat}"
                group_key = "other_e1e2"
                warn_no_match.append((appl, pat))

        aa = read_fasta(fasta_dir / fn) if fn.endswith(".fasta") else None

        inserted.append((
            record_id or None,
            fn or None,
            aa,
            parse_int(r["Length (aa)"]),
            pat or None,
            appl or None,
            (r["Protein / construct"] or "").strip() or None,
            (r["Product / intended use"] or "").strip() or None,
            (r["Potential application"] or "").strip() or None,
            (r["Market authorization / regulatory status (checked 2026-08-05)"] or "").strip() or None,
            (r["Safety and functional testing summary"] or "").strip() or None,
            (r["Highest evidence level"] or "").strip() or None,
            (r["FASTA availability / download"] or "").strip() or None,
            (r["PDB/CIF accession"] or "").strip() or None,
            (r["Patent source URL"] or "").strip() or None,
            (r["Regulatory / external evidence URL"] or "").strip() or None,
            (r["Confidence and limitations"] or "").strip() or None,
            group_key,
            SOURCE_VERSION,
        ))

    cur.executemany(sql, inserted)
    return len(inserted), warn_no_match


def import_groups(cur, exp_tsv: Path, name_to_key, groups):
    """导入 scaffold_groups(11 行,从 experiments 反查元数据)。"""
    # 先扫 experiments 取组级元数据
    with exp_tsv.open(encoding="utf-8-sig", newline="") as f:
        exp_rows = list(csv.DictReader(f, delimiter="\t"))

    group_meta = {}
    for r in exp_rows:
        gn = (r["Patent / product group"] or "").strip()
        gk = name_to_key.get(gn)
        if not gk:
            continue
        if gk not in group_meta:
            group_meta[gk] = (
                (r["Potential application"] or "").strip() or None,
                (r["Highest level"] or "").strip() or None,
            )

    sql = """
        INSERT INTO scaffold_groups (
            scaffold_group_key, scaffold_group_name,
            potential_application, highest_level, source_version
        ) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (scaffold_group_key) DO NOTHING
    """
    rows = [
        (gk, gn, group_meta.get(gk, (None, None))[0], group_meta.get(gk, (None, None))[1], SOURCE_VERSION)
        for gk, gn in groups.items()
    ]
    cur.executemany(sql, rows)
    return len(rows), [gn for gn, _ in [
        (r["Patent / product group"], name_to_key.get((r["Patent / product group"] or "").strip()))
        for r in exp_rows
    ] if _ is None]


def import_experiments(cur, exp_tsv: Path, name_to_key):
    """导入 scaffold_experiments(12 行)。"""
    with exp_tsv.open(encoding="utf-8-sig", newline="") as f:
        exp_rows = list(csv.DictReader(f, delimiter="\t"))

    sql = """
        INSERT INTO scaffold_experiments (
            scaffold_group_key, potential_application, highest_level,
            experiment_level, specific_experiments, principal_result,
            result_location, evidence_type_limitations, source_version
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """

    inserted = []
    skipped = []
    for r in exp_rows:
        gn = (r["Patent / product group"] or "").strip()
        gk = name_to_key.get(gn)
        if not gk:
            skipped.append(gn)
            continue
        inserted.append((
            gk,
            (r["Potential application"] or "").strip() or None,
            (r["Highest level"] or "").strip() or None,
            (r["Experiment level"] or "").strip() or None,
            (r["Specific experiments"] or "").strip() or None,
            (r["Principal result / validation stage"] or "").strip() or None,
            (r["Where to find the result"] or "").strip() or None,
            (r["Evidence type and limitations"] or "").strip() or None,
            SOURCE_VERSION,
        ))
    cur.executemany(sql, inserted)
    return len(inserted), skipped


# ---------- 校验 ----------

def validate(cur):
    def cnt(t):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        return cur.fetchone()[0]

    def cnt_seq():
        cur.execute("SELECT COUNT(*) FROM scaffold_patent_records WHERE aa_sequence IS NOT NULL")
        return cur.fetchone()[0]

    def cnt_null_gk():
        cur.execute("SELECT COUNT(*) FROM scaffold_patent_records WHERE scaffold_group_key IS NULL")
        return cur.fetchone()[0]

    def fk_ok():
        cur.execute("""
            SELECT COUNT(*) FROM scaffold_experiments e
            LEFT JOIN scaffold_groups g ON e.scaffold_group_key = g.scaffold_group_key
            WHERE g.scaffold_group_key IS NULL
        """)
        return cur.fetchone()[0]

    def fk_ok_records():
        cur.execute("""
            SELECT COUNT(*) FROM scaffold_patent_records p
            LEFT JOIN scaffold_groups g ON p.scaffold_group_key = g.scaffold_group_key
            WHERE p.scaffold_group_key IS NOT NULL AND g.scaffold_group_key IS NULL
        """)
        return cur.fetchone()[0]

    print("\n================ 校验结果 ================")
    print(f"scaffold_patent_records 总行数 : {cnt('scaffold_patent_records')}  (期望 27)")
    print(f"  其中含 aa_sequence            : {cnt_seq()}  (期望 16)")
    print(f"  其中 scaffold_group_key IS NULL: {cnt_null_gk()}  (期望 2,即 Yusong)")
    print(f"scaffold_experiments 总行数    : {cnt('scaffold_experiments')}  (期望 12)")
    print(f"scaffold_groups 总行数         : {cnt('scaffold_groups')}  (期望 11)")
    print(f"外键悬空(experiments→groups)   : {fk_ok()}  (期望 0)")
    print(f"外键悬空(records→groups,非 NULL): {fk_ok_records()}  (期望 0)")
    print("=========================================")


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="导入 scaffold 专利数据到 PostgreSQL")
    ap.add_argument(
        "--data-dir",
        default="data/patent_sequence_reorganized_2026-08-05/patent_db_handoff",
        help="handoff 包根目录(包含 source_data/ + group_record_mapping.csv)",
    )
    ap.add_argument(
        "--schema",
        default="src/setup/sql/10_create_scaffold_patents.sql",
        help="DDL 路径",
    )
    ap.add_argument("--dsn", default=None, help="PostgreSQL DSN,默认走 IGEM_PG_DSN / pitfalls.PG_DSN")
    ap.add_argument("--apply-schema", action="store_true", help="先执行 DDL(IF NOT EXISTS,可重跑)")
    ap.add_argument("--truncate", action="store_true",
                    help="导入前 TRUNCATE 三张表(需要 scaffold_groups/experiments 一起清,因 FK)")
    args = ap.parse_args()

    # 启动期校验:zcat 可用、import lib OK
    try:
        validate_environment()
    except PitfallError as e:
        sys.exit(f"[abort] {e}")

    data_dir = Path(args.data_dir)
    seq_tsv = data_dir / "source_data" / "scaffold_patents_sequence_guide.tsv"
    exp_tsv = data_dir / "source_data" / "scaffold_patents_experiment_details.tsv"
    fasta_dir = data_dir / "source_data" / "fasta_downloads"
    mapping = data_dir / "group_record_mapping.csv"
    schema = Path(args.schema)

    for p in (seq_tsv, exp_tsv, fasta_dir, mapping, schema):
        if not p.exists():
            sys.exit(f"[abort] 路径不存在: {p}")

    dsn = args.dsn or os.environ.get("IGEM_PG_DSN") or PG_DSN

    try:
        import psycopg2
    except ImportError:
        sys.exit("[abort] 缺 psycopg2: pip install psycopg2-binary")

    rec_to_group, no_fasta, name_to_key, groups = load_mapping(mapping)
    print(f"[ok] 映射表已加载: {len(rec_to_group)} 条 FASTA, "
          f"{len(no_fasta)} 条无 FASTA, {len(groups)} 个组")

    dsn = args.dsn or os.environ.get("IGEM_PG_DSN") or PG_DSN

    try:
        import psycopg2
    except ImportError:
        sys.exit("[abort] 缺 psycopg2: pip install psycopg2-binary")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    print(f"[ok] 已连接数据库(dsn 取自 {'args' if args.dsn else 'IGEM_PG_DSN / pitfalls.PG_DSN'})")

    # 启动后预检:表已有数据 → 报错退出,避免无声重复(experiments 无 UNIQUE,
    # ON CONFLICT 不能复盖)
    if not args.truncate:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM scaffold_patent_records")
            n_existing = cur.fetchone()[0]
        conn.rollback()
        if n_existing > 0:
            conn.close()
            sys.exit(
                f"[abort] scaffold_patent_records 已有 {n_existing} 行。\n"
                "       如需重灌: --truncate  (会清空三张表)\n"
                "       或手动: TRUNCATE scaffold_experiments, scaffold_patent_records, scaffold_groups;"
            )

    try:
        with conn.cursor() as cur:
            if args.apply_schema:
                with schema.open(encoding="utf-8") as f:
                    cur.execute(f.read())  # BEGIN/COMMIT 已含在 DDL 内
                print(f"[ok] 已执行 DDL: {schema}")

            if args.truncate:
                cur.execute("TRUNCATE scaffold_experiments, scaffold_patent_records, scaffold_groups")
                print("[ok] 已 TRUNCATE 三张表")

            # 顺序: groups(无外部依赖) → records(FK→groups) → experiments(FK→groups)
            n_grp, skipped_names = import_groups(cur, exp_tsv, name_to_key, groups)
            print(f"[ok] scaffold_groups 插入 {n_grp} 行")

            n_rec, warns = import_records(cur, seq_tsv, fasta_dir, rec_to_group, no_fasta)
            print(f"[ok] scaffold_patent_records 插入 {n_rec} 行")
            for w in warns:
                print(f"[warn] 无 FASTA 行未匹配映射表,已兜底为 other_e1e2: {w}")
            for sn in skipped_names:
                print(f"[warn] groups 组名未在映射表找到 key: {sn!r}")

            n_exp, skipped = import_experiments(cur, exp_tsv, name_to_key)
            print(f"[ok] scaffold_experiments 插入 {n_exp} 行")
            for sk in skipped:
                print(f"[warn] experiments 跳过: {sk!r}")

            validate(cur)
        conn.commit()
        print("[done] 导入完成。")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()