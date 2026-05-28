"""
Stages4 DuckDB 数据库接口模块。

所有 round 脚本通过此模块读写数据库，不直接构造 SQL。

设计原则:
- 所有表通过 init_schema() 创建
- 写入使用批量 INSERT（VALUES 子句）
- 查询返回 Python 原生类型
- 检查点支持断点续跑

基于 stages3/db.py 重构，表名和 schema 按 stages4 需求重新设计。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "output4" / "pipeline.db"


class PipelineDB:
    """Pipeline DuckDB 连接封装。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self._conn: duckdb.DuckDBPyConnection | None = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        """打开（或创建）数据库连接。"""
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(self.db_path)
            self._conn.execute("SET memory_limit = '32GB'")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> PipelineDB:
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ────────────────────────────────────────────────────────────────
    # Schema 初始化
    # ────────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """创建所有表（幂等，IF NOT EXISTS）。"""
        conn = self.connect()

        # Round 0: 候选肽段池（与 stages3 兼容）
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_candidate_id START 1
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id   BIGINT  DEFAULT nextval('seq_candidate_id'),
                source         VARCHAR NOT NULL,
                source_id      VARCHAR,
                header         VARCHAR,
                sequence       VARCHAR NOT NULL,
                length         SMALLINT NOT NULL,
                is_standard_aa BOOLEAN DEFAULT TRUE,
                cluster_id     VARCHAR,
                cluster_rep    BOOLEAN,
                PRIMARY KEY (candidate_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_candidates_source_id
            ON candidates(source, source_id)
        """)

        # Round 1: AnOxPePred + AlgPred2 评分
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round1_scores (
                candidate_id        BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                anoxpepred_score    FLOAT,
                anoxpepred_success  BOOLEAN,
                algpred2_score      FLOAT,
                algpred2_success    BOOLEAN,
                scored_at           TIMESTAMP DEFAULT now()
            )
        """)

        # Round 1: 双通道归属
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round1_channels (
                candidate_id      BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                channel           VARCHAR NOT NULL,  -- 'top' or 'bottom'
                anoxpepred_score  FLOAT NOT NULL,
                rank_in_channel   BIGINT,
                assigned_at       TIMESTAMP DEFAULT now()
            )
        """)

        # Round 2: 安全服务评分
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round2_scores (
                candidate_id        BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                toxinpred3_score    FLOAT,
                toxinpred3_success  BOOLEAN,
                hemopi2_score       FLOAT,
                hemopi2_success     BOOLEAN,
                mhcflurry_score     FLOAT,
                mhcflurry_success   BOOLEAN,
                scored_at           TIMESTAMP DEFAULT now()
            )
        """)

        # Round 2: 安全阈值通过者
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round2_passed (
                candidate_id  BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                channel       VARCHAR NOT NULL,
                passed_at     TIMESTAMP DEFAULT now()
            )
        """)

        # Round 2: 安全淘汰记录
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round2_excluded (
                candidate_id  BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                channel       VARCHAR,
                reason        VARCHAR NOT NULL,  -- 哪个安全项未通过
                excluded_at   TIMESTAMP DEFAULT now()
            )
        """)

        # Round 3: 深度服务评分（含 ToxinPred3）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round3_scores (
                candidate_id        BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                bepipred3_score     FLOAT,
                bepipred3_success   BOOLEAN,
                temstapro_score     FLOAT,
                temstapro_success   BOOLEAN,
                sodope_score        FLOAT,
                sodope_success      BOOLEAN,
                plm4cpps_score      FLOAT,
                plm4cpps_success    BOOLEAN,
                graphcpp_score      FLOAT,
                graphcpp_success    BOOLEAN,
                toxinpred3_score    FLOAT,
                toxinpred3_success  BOOLEAN,
                scored_at           TIMESTAMP DEFAULT now()
            )
        """)
        # 兼容已有数据库（表已存在时补加 ToxinPred3 列）
        for col in ["toxinpred3_score FLOAT", "toxinpred3_success BOOLEAN"]:
            try:
                conn.execute(f"ALTER TABLE round3_scores ADD COLUMN {col}")
            except Exception:
                pass  # 列已存在

        # Round 3: SD 加权排名
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round3_ranking (
                candidate_id    BIGINT PRIMARY KEY REFERENCES candidates(candidate_id),
                composite_score FLOAT NOT NULL,
                rank            BIGINT NOT NULL,
                weight_snapshot VARCHAR,
                ranked_at       TIMESTAMP DEFAULT now()
            )
        """)

        # Round 4: 构造枚举
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_construct_id START 1
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS constructs (
                construct_id   BIGINT DEFAULT nextval('seq_construct_id'),
                candidate_id   BIGINT REFERENCES candidates(candidate_id),
                linker         VARCHAR NOT NULL,
                position       VARCHAR NOT NULL,
                channel        VARCHAR NOT NULL,
                scaffold_seq   VARCHAR NOT NULL,
                linker_seq     VARCHAR NOT NULL,
                peptide_seq    VARCHAR NOT NULL,
                full_sequence  VARCHAR NOT NULL,
                PRIMARY KEY (construct_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS construct_scores (
                construct_id     BIGINT PRIMARY KEY REFERENCES constructs(construct_id),
                sodope_score      FLOAT,
                sodope_success    BOOLEAN,
                temstapro_score   FLOAT,
                temstapro_success BOOLEAN,
                bepipred3_score   FLOAT,
                bepipred3_success BOOLEAN,
                scored_at         TIMESTAMP DEFAULT now()
            )
        """)
        # 兼容已有数据库（表已存在时补加 bepipred3 列）
        for col in ["bepipred3_score FLOAT", "bepipred3_success BOOLEAN"]:
            try:
                conn.execute(f"ALTER TABLE construct_scores ADD COLUMN {col}")
            except Exception:
                pass

        # Round 4: Phase 1 过滤通过表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round4_phase1_passed (
                construct_id   BIGINT PRIMARY KEY REFERENCES constructs(construct_id),
                candidate_id   BIGINT NOT NULL,
                combined_score FLOAT,
                rank           BIGINT
            )
        """)

        # Round 5: 结构预测结果
        conn.execute("""
            CREATE TABLE IF NOT EXISTS structure_results (
                construct_id  BIGINT PRIMARY KEY REFERENCES constructs(construct_id),
                service       VARCHAR NOT NULL,
                pdb_path      VARCHAR,
                plddt         FLOAT,
                completed_at  TIMESTAMP DEFAULT now()
            )
        """)

        # Round 6: PDB 评估
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pdb_eval (
                construct_id     BIGINT PRIMARY KEY REFERENCES constructs(construct_id),
                sasa_score       FLOAT,
                sasa_success     BOOLEAN,
                aggrescan3d_score FLOAT,
                aggrescan3d_success BOOLEAN,
                scored_at        TIMESTAMP DEFAULT now()
            )
        """)

        # Round 7: 最终排名
        conn.execute("""
            CREATE TABLE IF NOT EXISTS final_ranking (
                construct_id    BIGINT REFERENCES constructs(construct_id),
                candidate_id    BIGINT REFERENCES candidates(candidate_id),
                channel         VARCHAR NOT NULL,
                composite_score FLOAT NOT NULL,
                rank            BIGINT NOT NULL,
                rank_in_channel BIGINT,
                weight_snapshot VARCHAR,
                ranked_at       TIMESTAMP DEFAULT now()
            )
        """)

        # 全局: 分数分布统计
        conn.execute("""
            CREATE TABLE IF NOT EXISTS score_distribution (
                id                BIGINT,
                stage_name        VARCHAR NOT NULL,
                service_name      VARCHAR NOT NULL,
                count             BIGINT,
                mean              FLOAT,
                stddev            FLOAT,
                min               FLOAT,
                p01               FLOAT,
                p05               FLOAT,
                p25               FLOAT,
                p50               FLOAT,
                p75               FLOAT,
                p95               FLOAT,
                p99               FLOAT,
                max               FLOAT,
                winsorized_stddev FLOAT,
                sd_weight         FLOAT,
                manual_coefficient FLOAT,
                final_weight      FLOAT,
                computed_at       TIMESTAMP DEFAULT now()
            )
        """)

        # 全局: 权重配置记录
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_config (
                id                BIGINT,
                stage_name        VARCHAR NOT NULL,
                total_candidates  BIGINT,
                weight_formula    VARCHAR,
                sd_weights        JSON,
                manual_coefficients JSON,
                final_weights     JSON,
                adjustment_reason VARCHAR,
                distribution_snapshot JSON,
                created_at        TIMESTAMP DEFAULT now()
            )
        """)

        # 全局: 检查点
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint (
                round           VARCHAR NOT NULL,
                step            VARCHAR,
                status          VARCHAR NOT NULL,
                total_items     BIGINT,
                processed_items BIGINT,
                error_message   VARCHAR,
                started_at      TIMESTAMP,
                completed_at    TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT now(),
                PRIMARY KEY (round, step)
            )
        """)

    # ────────────────────────────────────────────────────────────────
    # Round 0: 候选写入
    # ────────────────────────────────────────────────────────────────

    def insert_candidates(self, records: list[dict]) -> int:
        """批量插入候选肽（复用 stages3 逻辑）。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 10_000
        written = 0
        _q = lambda s: str(s).replace("'", "''") if s else ""
        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"('{r['source']}','{_q(r['source_id'])}','{_q(r.get('header'))}',"
                f"'{r['sequence']}',{r['length']},"
                f"{'true' if r.get('is_standard_aa', True) else 'false'})"
                for r in batch
            )
            conn.execute(
                f"INSERT INTO candidates (source, source_id, header, sequence, length, is_standard_aa) "
                f"VALUES {values}"
            )
            written += len(batch)
        return written

    # ────────────────────────────────────────────────────────────────
    # Round 1: 评分写入 + 通道分配
    # ────────────────────────────────────────────────────────────────

    def insert_round1_scores(self, records: list[dict]) -> int:
        """批量写入 Round 1 评分结果。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 10_000
        written = 0

        def _null(v):
            return 'NULL' if v is None else str(v)

        def _bool(v):
            return 'true' if v else 'false'

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['candidate_id']},"
                f"{_null(r.get('anoxpepred_score'))},"
                f"{_bool(r.get('anoxpepred_success', False))},"
                f"{_null(r.get('algpred2_score'))},"
                f"{_bool(r.get('algpred2_success', False))})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO round1_scores
                    (candidate_id, anoxpepred_score, anoxpepred_success,
                     algpred2_score, algpred2_success)
                VALUES {values}
                ON CONFLICT (candidate_id) DO UPDATE SET
                    anoxpepred_score   = EXCLUDED.anoxpepred_score,
                    anoxpepred_success = EXCLUDED.anoxpepred_success,
                    algpred2_score     = EXCLUDED.algpred2_score,
                    algpred2_success   = EXCLUDED.algpred2_success
            """)
            written += len(batch)
        return written

    def assign_channels(self, top_pct: float = 10.0, bottom_pct: float = 1.0) -> dict:
        """
        双通道分选。

        按 AnOxPePred 降序排序，AlgPred2 ≥ 阈值淘汰。
        Top 通道取前 top_pct%，Bottom 通道取后 bottom_pct%。
        使用纯 SQL 批量操作，避免逐行 INSERT。

        Returns:
            {"top": N, "bottom": N, "excluded_algpred2": N,
             "total_qualified": N, "top_anoxpepred_range": str, "bottom_anoxpepred_range": str}
        """
        conn = self.connect()

        excluded = conn.execute("""
            SELECT COUNT(*) FROM round1_scores
            WHERE algpred2_score >= 0.30 AND algpred2_success = true
        """).fetchone()[0]

        total = conn.execute("""
            SELECT COUNT(*) FROM round1_scores
            WHERE (algpred2_score IS NULL OR algpred2_score < 0.30)
              AND anoxpepred_score IS NOT NULL
        """).fetchone()[0]

        top_n = int(total * top_pct / 100)
        bottom_n = max(1, int(total * bottom_pct / 100))

        conn.execute("DELETE FROM round1_channels WHERE 1=1")

        conn.execute(f"""
            INSERT INTO round1_channels (candidate_id, channel, anoxpepred_score, rank_in_channel)
            SELECT candidate_id, 'top', anoxpepred_score,
                   ROW_NUMBER() OVER (ORDER BY anoxpepred_score DESC) AS rn
            FROM round1_scores
            WHERE (algpred2_score IS NULL OR algpred2_score < 0.30)
              AND anoxpepred_score IS NOT NULL
            ORDER BY anoxpepred_score DESC
            LIMIT {top_n}
        """)

        conn.execute(f"""
            INSERT INTO round1_channels (candidate_id, channel, anoxpepred_score, rank_in_channel)
            SELECT candidate_id, 'bottom', anoxpepred_score,
                   ROW_NUMBER() OVER (ORDER BY anoxpepred_score DESC) AS rn
            FROM round1_scores
            WHERE (algpred2_score IS NULL OR algpred2_score < 0.30)
              AND anoxpepred_score IS NOT NULL
            ORDER BY anoxpepred_score ASC
            LIMIT {bottom_n}
        """)

        top_min = conn.execute("""
            SELECT MIN(anoxpepred_score), MAX(anoxpepred_score)
            FROM round1_channels WHERE channel = 'top'
        """).fetchone()
        bottom_min = conn.execute("""
            SELECT MIN(anoxpepred_score), MAX(anoxpepred_score)
            FROM round1_channels WHERE channel = 'bottom'
        """).fetchone()

        top_count = conn.execute(
            "SELECT COUNT(*) FROM round1_channels WHERE channel = 'top'"
        ).fetchone()[0]
        bottom_count = conn.execute(
            "SELECT COUNT(*) FROM round1_channels WHERE channel = 'bottom'"
        ).fetchone()[0]

        return {
            "total_qualified": total,
            "top": top_count,
            "bottom": bottom_count,
            "excluded_algpred2": excluded,
            "top_anoxpepred_range": f"{top_min[0]:.4f} ~ {top_min[1]:.4f}" if top_min[0] else "N/A",
            "bottom_anoxpepred_range": f"{bottom_min[0]:.4f} ~ {bottom_min[1]:.4f}" if bottom_min[0] else "N/A",
        }

    def get_channel_candidates(self, channel: str) -> list[dict]:
        """获取指定通道的候选列表。"""
        conn = self.connect()
        rows = conn.execute("""
            SELECT c.candidate_id, c.sequence, c.length,
                   ch.anoxpepred_score, ch.rank_in_channel
            FROM round1_channels ch
            JOIN candidates c ON c.candidate_id = ch.candidate_id
            WHERE ch.channel = ?
            ORDER BY ch.rank_in_channel
        """, [channel]).fetchall()
        return [
            {"candidate_id": int(r[0]), "sequence": r[1], "length": r[2],
             "anoxpepred_score": float(r[3]) if r[3] else None, "rank": int(r[4]) if r[4] else None}
            for r in rows
        ]

    # ────────────────────────────────────────────────────────────────
    # Round 2: 安全评分 + 阈值筛选
    # ────────────────────────────────────────────────────────────────

    def insert_round2_scores(self, records: list[dict]) -> int:
        """批量写入 Round 2 安全服务评分。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 10_000
        written = 0

        def _null(v):
            return 'NULL' if v is None else str(v)

        def _bool(v):
            return 'true' if v else 'false'

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['candidate_id']},"
                f"{_null(r.get('toxinpred3_score'))},"
                f"{_bool(r.get('toxinpred3_success', False))},"
                f"{_null(r.get('hemopi2_score'))},"
                f"{_bool(r.get('hemopi2_success', False))},"
                f"{_null(r.get('mhcflurry_score'))},"
                f"{_bool(r.get('mhcflurry_success', False))})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO round2_scores
                    (candidate_id, toxinpred3_score, toxinpred3_success,
                     hemopi2_score, hemopi2_success,
                     mhcflurry_score, mhcflurry_success)
                VALUES {values}
                ON CONFLICT (candidate_id) DO UPDATE SET
                    toxinpred3_score   = EXCLUDED.toxinpred3_score,
                    toxinpred3_success = EXCLUDED.toxinpred3_success,
                    hemopi2_score      = EXCLUDED.hemopi2_score,
                    hemopi2_success    = EXCLUDED.hemopi2_success,
                    mhcflurry_score    = EXCLUDED.mhcflurry_score,
                    mhcflurry_success  = EXCLUDED.mhcflurry_success
            """)
            written += len(batch)
        return written

    def apply_safety_thresholds(
        self,
        toxin_threshold: float = 0.38,
        hemo_threshold: float = 0.55,
        mhc_threshold: float = 0.5,
    ) -> dict:
        """
        应用安全硬阈值过滤。

        任一安全项超过阈值即淘汰。服务不可用（NULL）时宽容放行。

        Returns:
            {"passed": N, "excluded": N, "details": {"toxin": N, "hemo": N, "mhc": N}}
        """
        conn = self.connect()
        details = {}

        # 逐一排查各安全项
        for svc, col, threshold in [
            ("toxin", "toxinpred3_score", toxin_threshold),
            ("hemo", "hemopi2_score", hemo_threshold),
            ("mhc", "mhcflurry_score", mhc_threshold),
        ]:
            count = conn.execute(f"""
                SELECT COUNT(*) FROM round2_scores
                WHERE {col} IS NOT NULL AND {col} >= ?
            """, [threshold]).fetchone()[0]
            details[svc] = count

        # 写入通过者
        conn.execute("DELETE FROM round2_passed WHERE 1=1")
        conn.execute("""
            INSERT INTO round2_passed (candidate_id, channel)
            SELECT DISTINCT s.candidate_id, ch.channel
            FROM round2_scores s
            JOIN round1_channels ch ON ch.candidate_id = s.candidate_id
            WHERE (s.toxinpred3_score IS NULL OR s.toxinpred3_score < ?)
              AND (s.hemopi2_score IS NULL OR s.hemopi2_score < ?)
              AND (s.mhcflurry_score IS NULL OR s.mhcflurry_score < ?)
        """, [toxin_threshold, hemo_threshold, mhc_threshold])

        # 写入淘汰者
        conn.execute("DELETE FROM round2_excluded WHERE 1=1")
        # ToxinPred3
        conn.execute(f"""
            INSERT INTO round2_excluded (candidate_id, channel, reason)
            SELECT s.candidate_id, ch.channel, 'toxinpred3'
            FROM round2_scores s
            JOIN round1_channels ch ON ch.candidate_id = s.candidate_id
            WHERE s.toxinpred3_score IS NOT NULL AND s.toxinpred3_score >= ?
            ON CONFLICT (candidate_id) DO NOTHING
        """, [toxin_threshold])

        # HemoPI2
        conn.execute(f"""
            INSERT INTO round2_excluded (candidate_id, channel, reason)
            SELECT s.candidate_id, ch.channel, 'hemopi2'
            FROM round2_scores s
            JOIN round1_channels ch ON ch.candidate_id = s.candidate_id
            WHERE s.hemopi2_score IS NOT NULL AND s.hemopi2_score >= ?
            ON CONFLICT (candidate_id) DO NOTHING
        """, [hemo_threshold])

        # MHCflurry
        conn.execute(f"""
            INSERT INTO round2_excluded (candidate_id, channel, reason)
            SELECT s.candidate_id, ch.channel, 'mhcflurry'
            FROM round2_scores s
            JOIN round1_channels ch ON ch.candidate_id = s.candidate_id
            WHERE s.mhcflurry_score IS NOT NULL AND s.mhcflurry_score >= ?
            ON CONFLICT (candidate_id) DO NOTHING
        """, [mhc_threshold])

        passed = conn.execute("SELECT COUNT(*) FROM round2_passed").fetchone()[0]
        excluded = conn.execute("SELECT COUNT(*) FROM round2_excluded").fetchone()[0]

        return {
            "passed": passed,
            "excluded": excluded,
            "details": details,
        }

    # ────────────────────────────────────────────────────────────────
    # Round 3 ToxinPred3 过滤
    # ────────────────────────────────────────────────────────────────

    def apply_toxin_threshold(self, threshold: float = 0.38) -> dict:
        """
        从 round3_scores 中排除 ToxinPred3 ≥ threshold 的候选。

        round3_scores 是工作表，删除有毒候选不影响后续重跑（checkpoint 保障）。
        Returns: {"excluded": N, "remaining": N}
        """
        conn = self.connect()
        excluded = conn.execute("""
            SELECT COUNT(*) FROM round3_scores
            WHERE toxinpred3_score IS NOT NULL AND toxinpred3_score >= ?
        """, [threshold]).fetchone()[0]

        conn.execute("""
            DELETE FROM round3_scores
            WHERE toxinpred3_score IS NOT NULL AND toxinpred3_score >= ?
        """, [threshold])

        remaining = conn.execute("SELECT COUNT(*) FROM round3_scores").fetchone()[0]
        return {"excluded": excluded, "remaining": remaining}

    # ────────────────────────────────────────────────────────────────
    # Round 3: 深度评分写入
    # ────────────────────────────────────────────────────────────────

    def insert_round3_scores(self, records: list[dict]) -> int:
        """批量写入 Round 3 深度评分结果（含 ToxinPred3）。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 10_000
        written = 0

        def _null(v):
            return 'NULL' if v is None else str(v)

        def _bool(v):
            return 'true' if v else 'false'

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['candidate_id']},"
                f"{_null(r.get('bepipred3_score'))},"
                f"{_bool(r.get('bepipred3_success', False))},"
                f"{_null(r.get('temstapro_score'))},"
                f"{_bool(r.get('temstapro_success', False))},"
                f"{_null(r.get('sodope_score'))},"
                f"{_bool(r.get('sodope_success', False))},"
                f"{_null(r.get('plm4cpps_score'))},"
                f"{_bool(r.get('plm4cpps_success', False))},"
                f"{_null(r.get('graphcpp_score'))},"
                f"{_bool(r.get('graphcpp_success', False))},"
                f"{_null(r.get('toxinpred3_score'))},"
                f"{_bool(r.get('toxinpred3_success', False))})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO round3_scores
                    (candidate_id, bepipred3_score, bepipred3_success,
                     temstapro_score, temstapro_success,
                     sodope_score, sodope_success,
                     plm4cpps_score, plm4cpps_success,
                     graphcpp_score, graphcpp_success,
                     toxinpred3_score, toxinpred3_success)
                VALUES {values}
                ON CONFLICT (candidate_id) DO UPDATE SET
                    bepipred3_score   = EXCLUDED.bepipred3_score,
                    bepipred3_success = EXCLUDED.bepipred3_success,
                    temstapro_score   = EXCLUDED.temstapro_score,
                    temstapro_success = EXCLUDED.temstapro_success,
                    sodope_score      = EXCLUDED.sodope_score,
                    sodope_success    = EXCLUDED.sodope_success,
                    plm4cpps_score    = EXCLUDED.plm4cpps_score,
                    plm4cpps_success  = EXCLUDED.plm4cpps_success,
                    graphcpp_score    = EXCLUDED.graphcpp_score,
                    graphcpp_success  = EXCLUDED.graphcpp_success,
                    toxinpred3_score   = EXCLUDED.toxinpred3_score,
                    toxinpred3_success = EXCLUDED.toxinpred3_success
            """)
            written += len(batch)
        return written

    def write_round3_ranking(self, records: list[dict], weight_snapshot: str | None = None) -> int:
        """写入 Round 3 排名结果。"""
        conn = self.connect()
        conn.execute("DELETE FROM round3_ranking WHERE 1=1")
        for r in records:
            conn.execute("""
                INSERT INTO round3_ranking (candidate_id, composite_score, rank, weight_snapshot)
                VALUES (?, ?, ?, ?)
            """, [
                r["candidate_id"], r["composite_score"], r["rank"],
                weight_snapshot or "",
            ])
        return len(records)

    # ────────────────────────────────────────────────────────────────
    # Round 3 Phase 2: GraphCPP 补充更新
    # ────────────────────────────────────────────────────────────────

    def update_round3_graphcpp(self, records: list[dict]) -> int:
        """仅更新 round3_scores 中的 graphcpp 分数（不触及已有列）。"""
        if not records:
            return 0
        conn = self.connect()
        for r in records:
            score = r.get("graphcpp_score")
            success = bool(r.get("graphcpp_success", False))
            conn.execute("""
                UPDATE round3_scores
                SET graphcpp_score = ?, graphcpp_success = ?
                WHERE candidate_id = ?
            """, [score, success, r["candidate_id"]])
        return len(records)

    # ────────────────────────────────────────────────────────────────
    # Round 4: Construct 操作
    # ────────────────────────────────────────────────────────────────

    def insert_constructs(self, records: list[dict]) -> list[int]:
        """批量写入 construct 枚举结果，返回 construct_id 列表。"""
        if not records:
            return []
        conn = self.connect()
        BATCH = 500
        ids: list[int] = []

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['candidate_id']},'{r['linker']}','{r['position']}','{r['channel']}',"
                f"'{r['scaffold_seq']}','{r['linker_seq']}','{r['peptide_seq']}',"
                f"'{r['full_sequence']}')"
                for r in batch
            )
            result = conn.execute(f"""
                INSERT INTO constructs
                    (candidate_id, linker, position, channel,
                     scaffold_seq, linker_seq, peptide_seq, full_sequence)
                VALUES {values}
                RETURNING construct_id
            """)
            ids.extend(r[0] for r in result.fetchall())
        return ids

    def insert_construct_scores(self, records: list[dict]) -> int:
        """批量写入 construct 评分。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 500
        written = 0

        def _null(v):
            return 'NULL' if v is None else str(v)

        def _bool(v):
            return 'true' if v else 'false'

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['construct_id']},"
                f"{_null(r.get('sodope_score'))},"
                f"{_bool(r.get('sodope_success', False))},"
                f"{_null(r.get('temstapro_score'))},"
                f"{_bool(r.get('temstapro_success', False))})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO construct_scores
                    (construct_id, sodope_score, sodope_success,
                     temstapro_score, temstapro_success)
                VALUES {values}
                ON CONFLICT (construct_id) DO UPDATE SET
                    sodope_score      = EXCLUDED.sodope_score,
                    sodope_success    = EXCLUDED.sodope_success,
                    temstapro_score   = EXCLUDED.temstapro_score,
                    temstapro_success = EXCLUDED.temstapro_success
            """)
            written += len(batch)
        return written

    def insert_round4_phase1_passed(self, records: list[dict]) -> int:
        """写入 Phase 1 过滤结果。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 500
        written = 0
        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['construct_id']},{r['candidate_id']},{r['combined_score']},{r['rank']})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO round4_phase1_passed
                    (construct_id, candidate_id, combined_score, rank)
                VALUES {values}
                ON CONFLICT (construct_id) DO UPDATE SET
                    combined_score = EXCLUDED.combined_score,
                    rank           = EXCLUDED.rank
            """)
            written += len(batch)
        return written

    def update_construct_bepipred3(self, records: list[dict]) -> int:
        """更新 construct 的 BepiPred3 评分。"""
        if not records:
            return 0
        conn = self.connect()
        for r in records:
            score = r.get("bepipred3_score")
            success = bool(r.get("bepipred3_success", False))
            conn.execute("""
                UPDATE construct_scores
                SET bepipred3_score = ?, bepipred3_success = ?
                WHERE construct_id = ?
            """, [score, success, r["construct_id"]])
        return len(records)

    # ────────────────────────────────────────────────────────────────
    # Round 5–6: 结构 + PDB 写入
    # ────────────────────────────────────────────────────────────────

    def write_structure_result(self, construct_id: int, service: str,
                                pdb_path: str, plddt: float | None) -> None:
        """写入单个结构预测结果。"""
        conn = self.connect()
        conn.execute("""
            INSERT INTO structure_results (construct_id, service, pdb_path, plddt)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (construct_id) DO UPDATE SET
                service=excluded.service, pdb_path=excluded.pdb_path,
                plddt=excluded.plddt
        """, [construct_id, service, pdb_path, plddt])

    def insert_pdb_eval(self, records: list[dict]) -> int:
        """批量写入 PDB 评估结果。"""
        if not records:
            return 0
        conn = self.connect()
        BATCH = 100
        written = 0

        def _null(v):
            return 'NULL' if v is None else str(v)

        def _bool(v):
            return 'true' if v else 'false'

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['construct_id']},"
                f"{_null(r.get('sasa_score'))},"
                f"{_bool(r.get('sasa_success', False))},"
                f"{_null(r.get('aggrescan3d_score'))},"
                f"{_bool(r.get('aggrescan3d_success', False))})"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO pdb_eval
                    (construct_id, sasa_score, sasa_success,
                     aggrescan3d_score, aggrescan3d_success)
                VALUES {values}
                ON CONFLICT (construct_id) DO UPDATE SET
                    sasa_score          = EXCLUDED.sasa_score,
                    sasa_success        = EXCLUDED.sasa_success,
                    aggrescan3d_score   = EXCLUDED.aggrescan3d_score,
                    aggrescan3d_success = EXCLUDED.aggrescan3d_success
            """)
            written += len(batch)
        return written

    def insert_final_ranking(self, records: list[dict]) -> int:
        """清空并重新写入最终排名结果。"""
        if not records:
            return 0
        conn = self.connect()
        conn.execute("DELETE FROM final_ranking WHERE 1=1")
        BATCH = 100
        written = 0

        for start_idx in range(0, len(records), BATCH):
            batch = records[start_idx:start_idx + BATCH]
            values = ",".join(
                f"({r['construct_id']},"
                f"{r['candidate_id']},"
                f"'{r['channel']}',"
                f"{r['composite_score']},"
                f"{r['rank']},"
                f"{r['rank_in_channel'] if r.get('rank_in_channel') is not None else 'NULL'},"
                f"'{r['weight_snapshot']}')"
                for r in batch
            )
            conn.execute(f"""
                INSERT INTO final_ranking
                    (construct_id, candidate_id, channel,
                     composite_score, rank, rank_in_channel, weight_snapshot)
                VALUES {values}
            """)
            written += len(batch)
        return written

    # ────────────────────────────────────────────────────────────────
    # 聚合查询
    # ────────────────────────────────────────────────────────────────

    def compute_distribution(self, table: str, score_column: str) -> dict:
        """计算某个分数列的 DuckDB 端分布统计。"""
        conn = self.connect()
        row = conn.execute(f"""
            SELECT
                COUNT(*)                                             AS count,
                AVG({score_column})                                  AS mean,
                STDDEV({score_column})                               AS stddev,
                MIN({score_column})                                  AS min,
                PERCENTILE_CONT(0.01) WITHIN GROUP (ORDER BY {score_column}) AS p01,
                PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY {score_column}) AS p05,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {score_column}) AS p25,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {score_column}) AS p50,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {score_column}) AS p75,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {score_column}) AS p95,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY {score_column}) AS p99,
                MAX({score_column})                                  AS max
            FROM {table}
            WHERE {score_column} IS NOT NULL
        """).fetchone()
        if row is None or row[0] == 0:
            return {}
        columns = ["count", "mean", "stddev", "min", "p01", "p05", "p25",
                    "p50", "p75", "p95", "p99", "max"]
        return dict(zip(columns, [float(v) if v is not None else None for v in row]))

    # ────────────────────────────────────────────────────────────────
    # 检查点
    # ────────────────────────────────────────────────────────────────

    def set_checkpoint(self, round_name: str, step: str, status: str,
                       total: int = 0, processed: int = 0,
                       error: str | None = None) -> None:
        """更新检查点状态。"""
        conn = self.connect()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO checkpoint (round, step, status, total_items, processed_items,
                                    error_message, updated_at, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (round, step) DO UPDATE SET
                status          = EXCLUDED.status,
                total_items     = EXCLUDED.total_items,
                processed_items = EXCLUDED.processed_items,
                error_message   = COALESCE(EXCLUDED.error_message, checkpoint.error_message),
                updated_at      = EXCLUDED.updated_at
        """, [round_name, step, status, total, processed, error, now, now])

    def get_checkpoint(self, round_name: str) -> dict | None:
        """查询某个 round 的最新检查点状态。"""
        conn = self.connect()
        row = conn.execute("""
            SELECT round, step, status, total_items, processed_items, error_message
            FROM checkpoint
            WHERE round = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, [round_name]).fetchone()
        if row is None:
            return None
        cols = ["round", "step", "status", "total_items", "processed_items", "error_message"]
        return dict(zip(cols, row))

    def get_last_processed_id(self, table: str, id_column: str = "candidate_id") -> int:
        """查询某个表已处理的最大 ID。"""
        conn = self.connect()
        row = conn.execute(f"SELECT COALESCE(MAX({id_column}), 0) FROM {table}").fetchone()
        return int(row[0]) if row else 0

    # ────────────────────────────────────────────────────────────────
    # 工具
    # ────────────────────────────────────────────────────────────────

    def table_exists(self, table: str) -> bool:
        """检查表是否存在。"""
        conn = self.connect()
        row = conn.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name = ?
        """, [table]).fetchone()
        return row is not None

    def row_count(self, table: str) -> int:
        """返回表的总行数。"""
        conn = self.connect()
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
