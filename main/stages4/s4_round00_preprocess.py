"""
Round 0: 数据预处理。

复用 stages3 的预处理结果。如果 stages3 的 candidates 表存在且有数据，
直接导入到 stages4 的数据库。否则需要重跑预处理。

用法:
    uv run python -m main.stages4.s4_round00_preprocess
    uv run python -m main.stages4.s4_round00_preprocess --force  # 强制重导
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from main.stages4.s4_db import PipelineDB

S3_DB_PATH = PROJECT_ROOT / "output3" / "pipeline.db"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def import_from_stages3(force: bool = False) -> int:
    """从 stages3 数据库导入 candidates 表。"""
    db = PipelineDB()
    conn = db.connect()
    db.init_schema()

    # 检查是否已有数据
    existing = db.row_count("candidates")
    if existing > 0 and not force:
        log(f"candidates 表已有 {existing:,} 条记录，跳过导入（使用 --force 强制重导）")
        return existing

    # 检查 stages3 DB
    s3_db = Path(S3_DB_PATH)
    if not s3_db.exists():
        log(f"❌ stages3 数据库不存在: {s3_db}")
        log("请先运行 stages3 的预处理，或手动提供 candidates 数据。")
        sys.exit(1)

    if force:
        conn.execute("DELETE FROM candidates")
        log("已清空 candidates 表（--force）")

    log(f"从 stages3 导入 candidates: {s3_db}")
    start = time.time()

    # 使用 DuckDB 的 ATTACH 跨库导入
    conn.execute(f"ATTACH '{s3_db}' AS s3 (READ_ONLY)")
    result = conn.execute("""
        INSERT INTO candidates (source, source_id, header, sequence, length, is_standard_aa)
        SELECT source, source_id, header, sequence, length, is_standard_aa
        FROM s3.candidates
        ORDER BY candidate_id
    """)
    inserted = db.row_count("candidates")

    elapsed = time.time() - start
    log(f"导入完成: {inserted:,} 条, 耗时 {elapsed:.0f}s ({elapsed/60:.1f} min)")
    return inserted


def main():
    force = "--force" in sys.argv
    log("Round 0: 数据预处理")
    n = import_from_stages3(force)
    log(f"✅ candidates: {n:,} 条")


if __name__ == "__main__":
    main()
