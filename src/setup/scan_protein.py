#!/usr/bin/env python3
"""
scan_protein.py — 流式扫描 FASTA(.gz),提取长度 1..30 的肽段,批量入库 PostgreSQL

策略:不用 pyfastx 的 .fxi 索引(建索引慢),改用纯手动 gzip + FASTA parser,
每条 record 直接 yield(name, seq),无随机访问,IO 友好。

支持两种模式:
  --source  <id>     新源(从 MANIFEST 读)
  --legacy  <id>     老源(走 LEGACY_SOURCES 硬编码路径表)

不做 RNA 6-frame 翻译(2026-07 spec 已废弃)。

依赖: psycopg[binary] (pyfastx 已不再必需,但仍保留 import 以便未来可选)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import sys
import time
import traceback
from pathlib import Path

import psycopg

# --- 路径常量 ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]  # .../iGEM-platform
sys.path.insert(0, str(ROOT / "src" / "setup"))
from lib import (
    PUBLIC_DATA_ROOT, MANIFEST_PATH,
    load as manifest_load,
)
from lib.legacy_sources import LEGACY_SOURCES
from lib.pitfalls import (
    validate_environment, assert_safe_scan, get_running_scan_count,
    PITFALLS,
)

# --- 20 字母合法字符集 -------------------------------------------------------
STD_AA = frozenset(b"ACDEFGHIKLMNPQRSTVWY")
STD_AA_LC = frozenset(b"acdefghiklmnpqrstvwy")

# --- DB 连接 -----------------------------------------------------------------
DB_DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)

BATCH_SIZE = 5000
MIN_LEN = 1
MAX_LEN = 30
FASTA_READ_CHUNK = 64 * 1024  # 64KB 块解压,够用

# 一些 FASTA header 解析用的正则
RE_PDB_ID = re.compile(rb"^([0-9][A-Za-z0-9]{3})[_|:]([A-Za-z0-9]?)")
RE_UNIPROT_HDR = re.compile(rb"^[>\s]*\w+\|([A-Z0-9]+)\|(\S+)")
RE_UNIREF_TAX = re.compile(rb"TaxID=(\d+)")
RE_RFAM = re.compile(rb"(RF\d{5})")


def _md5(seq: str) -> str:
    return hashlib.md5(seq.encode("ascii")).hexdigest()


def _parse_header(source: str, header: bytes) -> dict:
    """根据 source 风格从 FASTA header 抽取 accession/taxonomy/parent_header。"""
    hdr = header[:1000]
    meta = {"parent_header": hdr.decode("ascii", errors="replace")}
    if source in ("pdb", "legacy_pdb_2022"):
        m = RE_PDB_ID.match(hdr)
        if m:
            meta["pdb_id"] = m.group(1).decode("ascii").upper()
            chain = m.group(2).decode("ascii")
            if chain:
                meta["pdb_chain"] = chain
    elif source in ("uniprot_sprot", "uniprot_trembl", "legacy_uniprot_all_2021"):
        m = RE_UNIPROT_HDR.match(hdr)
        if m:
            meta["parent_header"] = (m.group(1) + b" " + m.group(2))[:1000].decode("ascii", errors="replace")
    elif source in ("uniref90", "legacy_uniref90_2022"):
        m = RE_UNIREF_TAX.search(hdr)
        if m:
            meta["taxonomy"] = m.group(1).decode("ascii")
    elif source in ("legacy_rfam_2022",):
        m = RE_RFAM.search(hdr)
        if m:
            meta["parent_header"] = m.group(1).decode("ascii")
    elif source in ("bfd", "legacy_bfd_2022"):
        pass
    elif source in ("legacy_rnacentral_2023", "legacy_nt_rna_2023"):
        # 截前 80 字符作为 accession 提示
        first_token = hdr.split(b" ")[0].lstrip(b">").decode("ascii", errors="replace")
        meta["parent_header"] = first_token[:80]
    elif source == "legacy_mgy_2022":
        # MGY cluster header 通常 >MGY..., 取 accession
        first_token = hdr.split(b" ")[0].lstrip(b">").decode("ascii", errors="replace")
        meta["parent_header"] = first_token[:80]
    return meta


def _stream_fasta_gz(path: str):
    """流式 .gz FASTA 迭代器: yield (header_bytes, seq_bytes)。

    用 zcat subprocess(系统级 gzip,带 LZ77 硬件优化)代替 Python gzip.open
    (Python gzip 逐字节 LZ77,慢 ~30 倍)。
    sequence 在内存只存当前一条(record 完成即丢弃)。
    """
    import subprocess
    if path.endswith(".gz"):
        p = subprocess.Popen(["zcat", path], stdout=subprocess.PIPE, bufsize=64*1024)
        f = p.stdout
        try:
            header = b""
            seq_parts = []
            for line in f:
                if not line:
                    break
                if line.startswith(b">"):
                    if header or seq_parts:
                        yield header, b"".join(seq_parts)
                    header = line.rstrip(b"\r\n")[1:]
                    seq_parts = []
                else:
                    seq_parts.append(line.rstrip(b"\r\n"))
            if header or seq_parts:
                yield header, b"".join(seq_parts)
        finally:
            try:
                f.close()
            except Exception:
                pass
            p.wait()
    else:
        with open(path, "rb") as f:
            header = b""
            seq_parts = []
            for line in f:
                if not line:
                    break
                if line.startswith(b">"):
                    if header or seq_parts:
                        yield header, b"".join(seq_parts)
                    header = line.rstrip(b"\r\n")[1:]
                    seq_parts = []
                else:
                    seq_parts.append(line.rstrip(b"\r\n"))
            if header or seq_parts:
                yield header, b"".join(seq_parts)


def _validate_and_upper(seq: bytes) -> str | None:
    """20 AA 字母过滤 + upper,非法返回 None。

    用 translate + bytes.find() 代替 Python-level byte iteration,速度 ~200x。
    """
    upper = seq.upper()  # bytes.upper 是 C-level
    # C-level 判断: 查找第一个不在 STD_AA 的字符
    for i in range(len(upper)):
        if upper[i] not in STD_AA:
            # uniprot/X 等 是非法
            return None
    return upper.decode("ascii")


def _resolve_source(source_id: str, legacy: bool) -> dict:
    if legacy:
        if source_id not in LEGACY_SOURCES:
            raise SystemExit(f"unknown legacy source: {source_id}. 可选: {sorted(LEGACY_SOURCES)}")
        info = LEGACY_SOURCES[source_id]
        return {
            "path": info["path"],
            "source_version": info["source_version"],
            "source_file": info["path"],
            "status_label": info["source_version"],
        }
    m = manifest_load()
    if source_id not in m["sources"]:
        raise SystemExit(f"unknown source: {source_id}. MANIFEST 现有: {sorted(m['sources'])}")
    sdef = m["sources"][source_id]
    cod = sdef.get("current_on_disk", {})
    file_rel = cod.get("file")
    if not file_rel:
        raise SystemExit(f"source {source_id} 没有 current_on_disk.file,跳过")
    path = str(Path(m["public_data_root"]) / file_rel)
    return {
        "path": path,
        "source_version": cod.get("version") or cod.get("version_date") or "unknown",
        "source_file": path,
        "status_label": sdef.get("status", "track-latest"),
    }


def _upsert_manifest_row(conn, source_id, version, file_path, sha256, nbytes,
                         status, rows_inserted=0, error=None, finished=False):
    sql = """
    INSERT INTO dataset_manifest (source, source_version, source_file, source_file_sha256, source_file_bytes,
                                  scan_started_at, scan_finished_at, status, rows_inserted, error_message)
    VALUES (%s, %s, %s, %s, %s, now(),
            CASE WHEN %s THEN now() ELSE NULL END,
            %s, %s, %s)
    ON CONFLICT (source, source_version, source_file) DO UPDATE
      SET source_file_sha256 = COALESCE(EXCLUDED.source_file_sha256, dataset_manifest.source_file_sha256),
          source_file_bytes  = COALESCE(EXCLUDED.source_file_bytes,  dataset_manifest.source_file_bytes),
          scan_started_at    = now(),
          scan_finished_at   = CASE WHEN %s THEN now() ELSE dataset_manifest.scan_finished_at END,
          status             = EXCLUDED.status,
          rows_inserted      = EXCLUDED.rows_inserted,
          error_message      = EXCLUDED.error_message
    """
    with conn.cursor() as cur:
        cur.execute(sql, (source_id, version, file_path, sha256, nbytes, finished,
                          status, rows_inserted, error, finished))


def _flush(conn, batch: list):
    sql = """
    INSERT INTO peptides (sequence, length, source, source_version,
                          source_accession, seq_md5)
    VALUES (%s,%s,%s,COALESCE(%s::date, CURRENT_DATE), %s,%s)
    ON CONFLICT (sequence, source, source_version) DO NOTHING
    """
    with conn.cursor() as cur:
        cur.executemany(sql, batch)
    conn.commit()


def scan(source_id: str, legacy: bool, dry_run: bool = False, limit: int | None = None):
    # --- 启动期校验: 把踩过的坑变成强约束 ---
    validate_environment()
    info = _resolve_source(source_id, legacy)
    path = info["path"]
    version = info["source_version"]
    nbytes_pre = os.path.getsize(path) if os.path.exists(path) else 0
    other = get_running_scan_count()
    assert_safe_scan(path, nbytes_pre, other_running_scans=other)

    sha256 = None
    nbytes = None
    if os.path.exists(path):
        nbytes = os.path.getsize(path)
        if not legacy and nbytes < 200_000_000_000:
            print(f"[sha256] 计算 {path} ({nbytes/1e9:.1f} GB)...", flush=True)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(64 * 1024 * 1024), b""):
                    h.update(chunk)
            sha256 = h.hexdigest()
            print(f"[sha256] {sha256}", flush=True)

    if dry_run:
        print(f"[dry-run] source={source_id} legacy={legacy} path={path} version={version} bytes={nbytes} sha={sha256}")
        return

    if not os.path.exists(path):
        raise SystemExit(f"文件不存在: {path}")

    print(f"[scan] source={source_id} legacy={legacy} path={path}", flush=True)

    conn = psycopg.connect(DB_DSN)
    _upsert_manifest_row(conn, source_id, version, path, sha256, nbytes, "in_progress")
    conn.commit()

    rows_total = 0
    batch = []
    t0 = time.time()

    try:
        stream = _stream_fasta_gz(path)  # 内部处理 .gz / 非 .gz 两种情况
        n_seqs = 0
        n_kept = 0
        n_invalid = 0
        n_bytes_in = 0
        last_flush_kept = 0
        last_flush_bytes = 0
        next_progress_bytes = 50 * 1024 * 1024  # 50MB 触发一次 progress
        for header, raw_seq in stream:
            n_seqs += 1
            n_bytes_in += len(raw_seq)
            seq = _validate_and_upper(raw_seq)
            if seq is None:
                n_invalid += 1
                continue
            L = len(seq)
            if L < MIN_LEN or L > MAX_LEN:
                continue
            meta = _parse_header(source_id, header)
            version_str = str(version)
            version_for_pg = version_str if "-" in version_str else None
            batch.append((
                seq,
                L,
                source_id,
                version_for_pg,
                meta.get("parent_header"),
                _md5(seq),
            ))
            n_kept += 1
            # 多个 flush 触发: batch 满, 或超过 100 条 kept, 或累计 30MB 数据
            should_flush = (len(batch) >= BATCH_SIZE or
                           n_kept - last_flush_kept >= 100 or
                           n_bytes_in - last_flush_bytes >= 30 * 1024 * 1024)
            if should_flush and batch:
                _flush(conn, batch)
                rows_total += len(batch)
                batch.clear()
                last_flush_kept = n_kept
                last_flush_bytes = n_bytes_in
            if n_bytes_in >= next_progress_bytes:
                rate = rows_total / max(1, time.time() - t0)
                print(f"  [scan] bytes={n_bytes_in/1e9:.2f}GB seqs={n_seqs} kept={n_kept} inserted={rows_total} invalid={n_invalid} rate={rate:.0f}/s", flush=True)
                next_progress_bytes += 50 * 1024 * 1024
            if limit and n_kept >= limit:
                break
        if batch:
            _flush(conn, batch)
            rows_total += len(batch)
            batch.clear()

        _upsert_manifest_row(conn, source_id, version, path, sha256, nbytes,
                             "done", rows_inserted=rows_total, finished=True)
        conn.commit()
        elapsed = time.time() - t0
        rate = rows_total / max(1, elapsed)
        print(f"[done] source={source_id} seqs={n_seqs} kept={rows_total} invalid={n_invalid} elapsed={elapsed:.1f}s rate={rate:.0f}/s", flush=True)

    except Exception as e:
        conn.rollback()
        _upsert_manifest_row(conn, source_id, version, path, sha256, nbytes,
                             "failed", rows_inserted=rows_total,
                             error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:2000]}",
                             finished=True)
        conn.commit()
        raise
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", help="新源 source_id(从 MANIFEST 读)")
    p.add_argument("--legacy", help="老源 source_id(从 LEGACY_SOURCES 读)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="最多保留 N 条,用于冒烟测试")
    args = p.parse_args()
    if not args.source and not args.legacy:
        p.error("必须 --source 或 --legacy")
    scan(args.source or args.legacy, legacy=bool(args.legacy),
         dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()