-- ============================================================================
-- 重组结构蛋白专利数据包 → 数据库 Schema
-- 数据源: patent_sequence_reorganized_2026-08-05/
-- 设计结论: 两张核心表 (patent_records + experiments)，另设一张可选 groups 维度表
-- 语法: PostgreSQL（其他库把 GENERATED ALWAYS AS IDENTITY 改为 AUTO_INCREMENT 即可）
-- 说明: 开头 DROP TABLE IF EXISTS 用于可重跑；若库中已有同名重要表，请先备份或删掉这几行
-- ============================================================================

DROP TABLE IF EXISTS experiments CASCADE;
DROP TABLE IF EXISTS patent_records CASCADE;
DROP TABLE IF EXISTS groups CASCADE;

-- ----------------------------------------------------------------------------
-- 表 1: patent_records  —— 主信息表
-- 粒度: 1 行 = 1 个序列记录(有 FASTA) 或 1 个无序列的专利/产品记录
-- 共 27 行 (16 条有 FASTA + 11 条有意不留 FASTA)
-- ----------------------------------------------------------------------------
CREATE TABLE patent_records (
    id                      BIGSERIAL PRIMARY KEY,   -- 代理主键（因 11 条无 FASTA 的记录 record_id 为空，不能做主键）

    record_id              TEXT UNIQUE,             -- 业务主键 = Sequence record ID（= FASTA 头部前缀）。
                                                    -- 仅 16 条有 FASTA 的记录有值；11 条无 FASTA 为 NULL，
                                                    -- 需导入时按 <申请人>_<专利号> 生成合成键（见 group_record_mapping.csv）

    fasta_filename         TEXT,                   -- 源 FASTA 文件名；无序列记录为 NULL
    aa_sequence            TEXT,                   -- 氨基酸序列（导入时从 FASTA 读取）；无序列记录为 NULL
    length_aa              INTEGER,                -- Length (aa)；无序列记录为 NULL

    patent_family          TEXT NOT NULL,          -- Patent / family（专利号 / 专利族）
    applicant              TEXT NOT NULL,          -- Applicant / assignee（申请人）
    protein_construct      TEXT,                   -- Protein / construct（构建体描述）
    product_intended_use   TEXT,                   -- Product / intended use（产品预期用途）
    potential_application  TEXT,                   -- Potential application（潜在应用）
    regulatory_status      TEXT,                   -- Market authorization / regulatory status（监管状态）
    testing_summary        TEXT,                   -- Safety and functional testing summary（安全/功能测试概述）
    highest_evidence_level TEXT
        CHECK (highest_evidence_level IN ('E1','E2','E3','E4','E5')),  -- 该记录可核实到的最高证据等级

    fasta_availability     TEXT,                   -- FASTA availability / download（如 "Not obtained (...)"）
    pdb_accession          TEXT,                   -- PDB/CIF accession（可为空）
    patent_source_url      TEXT,                   -- Patent source URL
    regulatory_evidence_url TEXT,                  -- Regulatory / external evidence URL
    confidence_limitations TEXT,                   -- Confidence and limitations（置信度与局限）

    group_key              TEXT,                   -- 关联键 → experiments.group_key（连接枢纽；Yusong 待定可为 NULL）
    -- 若启用 groups 维度表，加外键约束：
    -- CONSTRAINT fk_rec_group FOREIGN KEY (group_key) REFERENCES groups(group_key)
);

-- ----------------------------------------------------------------------------
-- 表 2: experiments  —— 实验明细表
-- 粒度: 1 行 = 1 个 (实验组, 实验等级) 条目（同一组可有 E1/E2/E4 多行）
-- 共 12 行
-- ----------------------------------------------------------------------------
CREATE TABLE experiments (
    experiment_id          BIGSERIAL PRIMARY KEY,  -- 代理主键（原表无天然主键）
    group_key              TEXT NOT NULL,          -- Patent / product group（与 patent_records.group_key 对应）
    potential_application  TEXT,                   -- 组级潜在应用（与主表同名字段含义不同，这里是组级）
    highest_level          TEXT
        CHECK (highest_level IN ('E1','E2','E3','E4','E5')),   -- 该组的“最高”等级（注意：未必等于其成员记录等级的最大值）
    experiment_level       TEXT,                   -- 该行具体记录的实验等级（如 E1 / E2 / E4/E5 / E3-E5）
    specific_experiments   TEXT,                   -- Specific experiments（具体做了什么实验）
    principal_result       TEXT,                   -- Principal result / validation stage（主要结果与验证阶段）
    result_location        TEXT,                   -- Where to find the result（结果出处）
    evidence_type_limitations TEXT,                -- Evidence type and limitations（证据类型与局限）
    -- 若启用 groups 维度表，加外键约束：
    -- CONSTRAINT fk_exp_group FOREIGN KEY (group_key) REFERENCES groups(group_key)
);

-- ----------------------------------------------------------------------------
-- 可选表 3: groups  —— 实验组维度表（规范化连接枢纽，强烈建议）
-- 粒度: 1 行 = 1 个实验组（共 12 组；其中 2 组 jinbo_devices / trautec_kefuyan 无对应序列记录）
-- 作用: group_key 同时出现在 patent_records 与 experiments 两张表，做一张维度表可消除
--       重复文本、统一维护、并加参照完整性约束
-- ----------------------------------------------------------------------------
CREATE TABLE groups (
    group_key              TEXT PRIMARY KEY,       -- 短 slug，如 jinbo_constructs / eadf4_c16
    group_name             TEXT NOT NULL,          -- 完整组名（= experiments 表中的 Patent / product group）
    potential_application  TEXT,
    highest_level          TEXT
        CHECK (highest_level IN ('E1','E2','E3','E4','E5'))
);

-- ----------------------------------------------------------------------------
-- 便捷索引（按申请人 / 专利族 / 证据等级 / 组 查询时提速）
-- ----------------------------------------------------------------------------
CREATE INDEX idx_rec_applicant   ON patent_records(applicant);
CREATE INDEX idx_rec_patent      ON patent_records(patent_family);
CREATE INDEX idx_rec_evlevel     ON patent_records(highest_evidence_level);
CREATE INDEX idx_rec_group       ON patent_records(group_key);
CREATE INDEX idx_exp_group       ON experiments(group_key);
