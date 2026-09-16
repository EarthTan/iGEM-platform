-- 10_create_scaffold_patents.sql
-- 来源: data/patent_sequence_reorganized_2026-08-05/patent_db_handoff/
-- 目的: 重组结构蛋白专利数据(scaffold = 骨架蛋白 = 长蛋白,加 scaffold_* 前缀与项目 peptides 流区分)
-- 约定: 沿用 04 / 09 的风格 — BEGIN/COMMIT 包裹, IF NOT EXISTS, BIGSERIAL, TIMESTAMPTZ 默认 now()
BEGIN;

-- ----------------------------------------------------------------------------
-- scaffold_patent_records —— 专利/产品主信息
-- 粒度: 1 行 = 1 个序列记录(有 FASTA) 或 1 个无序列的专利/产品记录
-- 27 行 = 16 行有 FASTA + 11 行无 FASTA(11 行 record_id 用合成键)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scaffold_patent_records (
    id                      BIGSERIAL PRIMARY KEY,
    record_id               TEXT UNIQUE,        -- 业务主键 = FASTA 头部前缀;无 FASTA 行 NULL,导入时按 <申请人>_<专利号> 生成合成键
    fasta_filename          TEXT,               -- 源 FASTA 文件名;无序列记录 NULL
    aa_sequence             TEXT,               -- 氨基酸序列;无序列记录 NULL
    length_aa               INTEGER,            -- Length (aa);无序列记录 NULL

    patent_family           TEXT NOT NULL,      -- Patent / family(专利号 / 专利族)
    applicant               TEXT NOT NULL,      -- Applicant / assignee
    protein_construct       TEXT,               -- Protein / construct(构建体描述)
    product_intended_use    TEXT,               -- Product / intended use
    potential_application   TEXT,               -- Potential application
    regulatory_status       TEXT,               -- Market authorization / regulatory status(checked 2026-08-05)
    testing_summary         TEXT,               -- Safety and functional testing summary
    highest_evidence_level  TEXT
        CHECK (highest_evidence_level IN ('E1','E2','E3','E4','E5')),
    fasta_availability      TEXT,               -- FASTA availability / download
    pdb_accession           TEXT,               -- PDB/CIF accession
    patent_source_url       TEXT,               -- Patent source URL
    regulatory_evidence_url TEXT,               -- Regulatory / external evidence URL
    confidence_limitations  TEXT,               -- Confidence and limitations

    scaffold_group_key      TEXT,               -- 关联键 → scaffold_groups.scaffold_group_key(Yusong 可为 NULL)
    source_version          DATE NOT NULL,      -- 数据快照日期(2026-08-05),沿用 peptides 流 source_version DATE 惯例
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
    -- highest_evidence_level 保留 CHECK(实测均为单值 E1/E2/E4/E5)
);

CREATE INDEX IF NOT EXISTS scaffold_patent_records_applicant_idx ON scaffold_patent_records (applicant);
CREATE INDEX IF NOT EXISTS scaffold_patent_records_patent_idx    ON scaffold_patent_records (patent_family);
CREATE INDEX IF NOT EXISTS scaffold_patent_records_evlevel_idx   ON scaffold_patent_records (highest_evidence_level);
CREATE INDEX IF NOT EXISTS scaffold_patent_records_group_idx     ON scaffold_patent_records (scaffold_group_key);
CREATE INDEX IF NOT EXISTS scaffold_patent_records_srcver_idx    ON scaffold_patent_records (source_version);

-- ----------------------------------------------------------------------------
-- scaffold_groups —— 实验组维度表(连接枢纽)
-- 11 行 = 12 个实验组中 2 组(jinbo_devices / trautec_kefuyan)无对应序列记录,但 experiments 仍以它们为 FK 目标,
-- 故 groups 表只装"有 FASTA 记录或显式列出的组",共 11 行。
-- 列名避开 'group'(PG 保留字)。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scaffold_groups (
    scaffold_group_key      TEXT PRIMARY KEY,   -- 短 slug,如 jinbo_constructs / eadf4_c16
    scaffold_group_name     TEXT NOT NULL,      -- 完整组名(Patent / product group)
    potential_application   TEXT,
    highest_level           TEXT,               -- 实测取值为 E1/E2/E4/E5 或 'E1/E2' 范围表达,不加 CHECK
    source_version          DATE NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scaffold_groups_name_idx ON scaffold_groups (scaffold_group_name);

-- ----------------------------------------------------------------------------
-- scaffold_experiments —— 实验明细
-- 12 行,粒度 = 1 (实验组, 实验等级) 条目(同一组可有 E1/E2/E4 多行)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scaffold_experiments (
    id                         BIGSERIAL PRIMARY KEY,
    scaffold_group_key         TEXT NOT NULL,    -- FK → scaffold_groups(同 schema 内,建后加)
    potential_application      TEXT,
    highest_level              TEXT,             -- 实测有 E1/E2/E4/E5 与 'E1/E2' 等,不加 CHECK
    experiment_level           TEXT,             -- 实测含 'E4/E5'/'E3-E5'/'E1-E5'/'E1/E4'/'E1/E2',纯自由文本
    specific_experiments       TEXT,
    principal_result           TEXT,
    result_location            TEXT,
    evidence_type_limitations  TEXT,
    source_version             DATE NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scaffold_experiments_group_idx    ON scaffold_experiments (scaffold_group_key);
CREATE INDEX IF NOT EXISTS scaffold_experiments_evlevel_idx  ON scaffold_experiments (highest_level);
CREATE INDEX IF NOT EXISTS scaffold_experiments_srcver_idx   ON scaffold_experiments (source_version);

-- 事后加 FK(IF NOT EXISTS 不能直接挂 FK,先加一个 DO block)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'scaffold_experiments_group_fk'
    ) THEN
        ALTER TABLE scaffold_experiments
            ADD CONSTRAINT scaffold_experiments_group_fk
            FOREIGN KEY (scaffold_group_key)
            REFERENCES scaffold_groups (scaffold_group_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'scaffold_patent_records_group_fk'
    ) THEN
        ALTER TABLE scaffold_patent_records
            ADD CONSTRAINT scaffold_patent_records_group_fk
            FOREIGN KEY (scaffold_group_key)
            REFERENCES scaffold_groups (scaffold_group_key);
    END IF;
END$$;

COMMIT;