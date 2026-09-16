#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重组结构蛋白专利数据包 → PostgreSQL 导入脚本
============================================
把 patent_sequence_reorganized_2026-08-05/ 里的两份 TSV + FASTA 文件，
按 patent_db_schema.sql 定义的表结构，灌入数据库。

依赖:
    pip install psycopg2-binary      # 或用 psycopg (psycopg3)，脚本会优先尝试

用法(示例):
    python import_patent_db.py \
        --tsv        source_data/scaffold_patents_sequence_guide.tsv \
        --exp        source_data/scaffold_patents_experiment_details.tsv \
        --fasta-dir  source_data/fasta_downloads \
        --mapping    group_record_mapping.csv \
        --schema     patent_db_schema.sql \
        --db         "postgresql://user:password@localhost:5432/patentdb" \
        --apply-schema

参数说明:
    --apply-schema   先执行 patent_db_schema.sql 建表（含 DROP TABLE IF EXISTS，可重跑）
    --db             数据库连接串；也可用环境变量 DATABASE_URL
    其余路径默认指向本包内的相对位置，可直接 `python import_patent_db.py --apply-schema` 一键运行。

导入逻辑要点（详见 README_导入说明.md）:
    1. patent_records 27 行：16 行有 FASTA（record_id 取自 TSV，aa_sequence 从 FASTA 读取）；
       11 行无 FASTA（record_id 为空，按 (申请人, 专利族) 在映射表里取合成键）。
    2. experiments 12 行：用“实验组名 → group_key”映射（来自映射表）。
    3. groups 11 行：从 experiments 的组名反查 group_key 得到，作为维度表。
"""

import argparse
import csv
import os
import re
import sys

# ---------------- 路径默认值（相对本脚本所在目录） ----------------
HERE = os.path.dirname(os.path.abspath(__file__))


def default(p):
    return os.path.join(HERE, p)


def norm(s):
    """匹配键归一化：转小写、逗号变空格、压缩空白。用于 (applicant, patent_family)
    的容错匹配（原始数据里 Co., Ltd. / FibroGen, Inc. 等逗号位置不一致）。"""
    s = (s or "").lower()
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------- 数据库适配 ----------------
def get_conn(db_url):
    # 优先 psycopg2，回退 psycopg (v3)
    try:
        import psycopg2
        return psycopg2.connect(db_url), "psycopg2"
    except ImportError:
        try:
            import psycopg
            return psycopg.connect(db_url), "psycopg"
        except ImportError:
            sys.exit("缺少 PostgreSQL 驱动，请先执行: pip install psycopg2-binary")


def read_fasta(path):
    """读取单条 FASTA，返回拼接后的氨基酸序列字符串；文件不存在返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    seq = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(">"):
                continue
            seq.append(line)
    return "".join(seq) if seq else None


def load_mapping(mapping_path):
    """解析 group_record_mapping.csv，返回四个结构。

    列说明:
        record_id          业务键：仅 FASTA 行与 Yusong 有值
        synthetic_record_id 合成键：仅无 FASTA 行有值
        无 FASTA 行的匹配方式：主表 (applicant, patent_family) → 映射表 (applicant, patent_family)
                            取 (synthetic_record_id, group_key)
    """
    rec_to_group = {}        # record_id -> group_key（FASTA 行）
    no_fasta = {}            # (applicant, patent_family) -> (synthetic_record_id, group_key)（无 FASTA 行）
    name_to_key = {}         # group_name -> group_key
    groups = {}              # group_key -> group_name

    with open(mapping_path, encoding="utf-8-sig", newline="") as f:
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
                # FASTA 行 / Yusong：用 record_id 建业务键映射（Yusong 的 gk=unmapped 不写入）
                if gk and gk != "unmapped":
                    rec_to_group[rid] = gk
                continue

            if not rid and srid and appl and pat and gk and gk != "unmapped":
                # 无 FASTA 行：以 (applicant, patent_family) 归一化后为匹配键
                no_fasta[(norm(appl), norm(pat))] = (srid, gk)

    return rec_to_group, no_fasta, name_to_key, groups


def main():
    ap = argparse.ArgumentParser(description="导入专利数据包到 PostgreSQL")
    ap.add_argument("--tsv", default=default("source_data/scaffold_patents_sequence_guide.tsv"))
    ap.add_argument("--exp", default=default("source_data/scaffold_patents_experiment_details.tsv"))
    ap.add_argument("--fasta-dir", default=default("source_data/fasta_downloads"))
    ap.add_argument("--mapping", default=default("group_record_mapping.csv"))
    ap.add_argument("--schema", default=default("patent_db_schema.sql"))
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--apply-schema", action="store_true", help="先执行 schema 建表")
    args = ap.parse_args()

    if not args.db:
        sys.exit("请提供数据库连接串：--db 'postgresql://...' 或设置环境变量 DATABASE_URL")

    rec_to_group, no_fasta, name_to_key, groups = load_mapping(args.mapping)

    conn, driver = get_conn(args.db)
    print(f"[ok] 已连接数据库（驱动: {driver}）")
    cur = conn.cursor()

    if args.apply_schema:
        with open(args.schema, encoding="utf-8") as f:
            cur.execute(f.read())
        conn.commit()
        print("[ok] 已执行 patent_db_schema.sql 建表")

    # ---------- 1. 导入 patent_records ----------
    with open(args.tsv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    rec_cols = [
        "record_id", "fasta_filename", "aa_sequence", "length_aa", "patent_family",
        "applicant", "protein_construct", "product_intended_use", "potential_application",
        "regulatory_status", "testing_summary", "highest_evidence_level",
        "fasta_availability", "pdb_accession", "patent_source_url",
        "regulatory_evidence_url", "confidence_limitations", "group_key",
    ]
    rec_sql = "INSERT INTO patent_records ({}) VALUES ({})".format(
        ", ".join(rec_cols), ", ".join(["%s"] * len(rec_cols))
    )

    rec_rows = []
    warn_no_match = []
    for r in rows:
        rid = (r["Sequence record ID"] or "").strip()
        appl = (r["Applicant / assignee"] or "").strip()
        pat = (r["Patent / family"] or "").strip()
        fn = (r["FASTA filename"] or "").strip()

        if rid:
            record_id = rid
            group_key = rec_to_group.get(rid)  # 找不到则为 None（如 unmapped 的 Yusong）
        else:
            # 无 FASTA：按 (applicant, patent_family) 归一化后在映射表取合成键与组键
            hit = no_fasta.get((norm(appl), norm(pat)))
            if hit:
                record_id, group_key = hit
            else:
                record_id = f"{appl}_{pat}"
                group_key = "other_e1e2"
                warn_no_match.append((appl, pat))

        aa = read_fasta(os.path.join(args.fasta_dir, fn)) if fn.endswith(".fasta") else None
        la = (r["Length (aa)"] or "").strip()
        length = int(la) if la.isdigit() else None

        rec_rows.append((
            record_id or None,
            fn or None,
            aa,                      # 无 FASTA 为 None
            length,                  # 无 FASTA 为 None
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
            group_key,              # unmapped / 找不到则 None
        ))

    cur.executemany(rec_sql, rec_rows)
    conn.commit()
    print(f"[ok] patent_records 插入 {len(rec_rows)} 行")

    if warn_no_match:
        print(f"[warn] {len(warn_no_match)} 条无 FASTA 记录未能在映射表精确匹配，已用兜底键(other_e1e2):")
        for w in warn_no_match:
            print("       ", w)

    # ---------- 2. 导入 experiments ----------
    with open(args.exp, encoding="utf-8-sig", newline="") as f:
        exp_rows_raw = list(csv.DictReader(f, delimiter="\t"))

    exp_cols = [
        "group_key", "potential_application", "highest_level", "experiment_level",
        "specific_experiments", "principal_result", "result_location",
        "evidence_type_limitations",
    ]
    exp_sql = "INSERT INTO experiments ({}) VALUES ({})".format(
        ", ".join(exp_cols), ", ".join(["%s"] * len(exp_cols))
    )

    exp_rows = []
    exp_groups_seen = {}  # group_key -> (potential_application, highest_level)
    for r in exp_rows_raw:
        gn = (r["Patent / product group"] or "").strip()
        gk = name_to_key.get(gn)
        if not gk:
            print(f"[warn] 实验组名未在映射表找到 group_key: {gn!r} —— 跳过")
            continue
        exp_rows.append((
            gk,
            (r["Potential application"] or "").strip() or None,
            (r["Highest level"] or "").strip() or None,
            (r["Experiment level"] or "").strip() or None,
            (r["Specific experiments"] or "").strip() or None,
            (r["Principal result / validation stage"] or "").strip() or None,
            (r["Where to find the result"] or "").strip() or None,
            (r["Evidence type and limitations"] or "").strip() or None,
        ))
        exp_groups_seen[gk] = (
            (r["Potential application"] or "").strip() or None,
            (r["Highest level"] or "").strip() or None,
        )

    cur.executemany(exp_sql, exp_rows)
    conn.commit()
    print(f"[ok] experiments 插入 {len(exp_rows)} 行")

    # ---------- 3. 导入 groups（维度表，从 experiments 反查）----------
    grp_sql = "INSERT INTO groups (group_key, group_name, potential_application, highest_level) VALUES (%s,%s,%s,%s)"
    grp_rows = []
    for gk, gn in groups.items():
        pa, hl = exp_groups_seen.get(gk, (None, None))
        grp_rows.append((gk, gn, pa, hl))
    if grp_rows:
        cur.executemany(grp_sql, grp_rows)
        conn.commit()
        print(f"[ok] groups 插入 {len(grp_rows)} 行")

    # ---------- 4. 校验 ----------
    def cnt(t):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        return cur.fetchone()[0]

    def cnt_seq():
        cur.execute("SELECT COUNT(*) FROM patent_records WHERE aa_sequence IS NOT NULL")
        return cur.fetchone()[0]

    def cnt_null_gk():
        cur.execute("SELECT COUNT(*) FROM patent_records WHERE group_key IS NULL")
        return cur.fetchone()[0]

    def fk_ok():
        cur.execute(
            "SELECT COUNT(*) FROM patent_records pr "
            "LEFT JOIN groups g ON pr.group_key = g.group_key "
            "WHERE pr.group_key IS NOT NULL AND g.group_key IS NULL"
        )
        return cur.fetchone()[0]

    print("\n================ 校验结果 ================")
    print(f"patent_records 总行数 : {cnt('patent_records')}  (期望 27)")
    print(f"  其中含 aa_sequence  : {cnt_seq()}  (期望 16)")
    print(f"  其中 group_key 为 NULL: {cnt_null_gk()}  (期望 2，即 Yusong)")
    print(f"experiments 总行数    : {cnt('experiments')}  (期望 12)")
    print(f"groups 总行数         : {cnt('groups')}  (期望 11)")
    print(f"外键悬空(records.group_key 无对应 groups): {fk_ok()}  (期望 0)")
    print("=========================================")
    print("[done] 导入完成。")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
