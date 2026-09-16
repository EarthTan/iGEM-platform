-- 12_create_constructs.sql
-- Constructs 表（预计算 construct 库）
--
-- 一条 construct = 1 个 backbone + 1 个 linker + 1 个 peptide 拼成的融合蛋白。
-- 抗氧化方向的目标产出：Top 150 + Bottom 100 = 250 条。
--
-- 设计原则（对齐 README §三后端指引）：
--   * status='passed' 才参与综合分排名；'failed_safety' 综合分置 0
--   * scores 存 9 项基线分（与 README §三 scores 对齐）
--   * delivery_scores 存 4 种递送方式调整后的分（前端切换递送时不重算）
--   * channel='top'/'bottom' 用于对照（评估用，不进 Library 前端）
--
-- 注意：本表对 1 个 (backbone, linker, peptide, direction) 组合要求唯一，避免
--       同一组合在不同 scenario 下被重复插入。

BEGIN;

CREATE TABLE IF NOT EXISTS backbone_proteins (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    type            VARCHAR(16) NOT NULL CHECK (type IN ('natural','modified','custom')),
    sequence        TEXT NOT NULL,
    length          INTEGER NOT NULL,
    species         TEXT,
    common_name     TEXT,
    accession       TEXT,
    lab             TEXT,
    patent          TEXT,
    reference       TEXT,
    characteristics TEXT,
    properties      TEXT,
    applications    TEXT,
    expression_notes TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS linkers (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    sequence        TEXT NOT NULL,
    length          INTEGER NOT NULL,
    flexible_count  INTEGER NOT NULL,
    rigid_count     INTEGER NOT NULL,
    rigidity        REAL,
    description     TEXT
);

-- 抗氧化预计算 backbone / linker 占位数据，方向构造器再扩。
INSERT INTO backbone_proteins (name, type, sequence, length, common_name, applications)
VALUES
    ('4RepCT',     'modified', 'PLACEHOLDER_4RepCT', 0, 'Modified silk', 'Cosmetic'),
    ('MaSp1',      'natural',  'PLACEHOLDER_MaSp1', 0, 'Spider dragline silk', 'Wound dressing'),
    ('MiSp',       'natural',  'PLACEHOLDER_MiSp',  0, 'Spider minor ampullate silk', 'Wound dressing'),
    ('Fibroin',    'natural',  'PLACEHOLDER_Fibroin', 0, 'Bombyx mori silk fibroin', 'Wound dressing'),
    ('ELP',        'modified', 'PLACEHOLDER_ELP', 0, 'Elastin-like polypeptide', 'Cosmetic')
ON CONFLICT DO NOTHING;

-- 32-linker 库（GGGGS / EAAAK 五位全排列 = 32 种）
-- 这里只示例插入最终拼接构造时会用到的几个；完整 32 库由 linker_gen.py 生成。
INSERT INTO linkers (name, sequence, length, flexible_count, rigid_count, rigidity, description)
VALUES
    ('GGGGS',            'GGGGS',           5, 1, 0, 0.0,  'full flexible'),
    ('EAAAK',            'EAAAK',           5, 0, 1, 1.0,  'full rigid'),
    ('GGGGSGGGGS',       'GGGGSGGGGS',     10, 2, 0, 0.0,  '2x flexible'),
    ('EAAAKEAAAK',       'EAAAKEAAAK',     10, 0, 2, 1.0,  '2x rigid'),
    ('GGGGSGGGGSGGGGS',  'GGGGSGGGGSGGGGS',15, 3, 0, 0.0,  '3x flexible'),
    ('EAAAKEAAAKEAAAK',  'EAAAKEAAAKEAAAK',15, 0, 3, 1.0,  '3x rigid')
ON CONFLICT DO NOTHING;


CREATE TABLE IF NOT EXISTS constructs (
    id              BIGSERIAL PRIMARY KEY,
    direction       VARCHAR(32) NOT NULL,
    scenario        TEXT NOT NULL DEFAULT 'wound_careing',
    backbone_id     BIGINT REFERENCES backbone_proteins(id),
    linker_id       BIGINT REFERENCES linkers(id),
    peptide_id      BIGINT REFERENCES peptides(id),
    full_sequence   TEXT NOT NULL,
    channel         VARCHAR(8) CHECK (channel IN ('top','bottom')),
    status          VARCHAR(16) CHECK (status IN ('passed','failed_safety','failed_score')),
    rank            INTEGER,
    scores          JSONB NOT NULL,
    delivery_scores JSONB NOT NULL,
    assessed_hemo   BOOLEAN NOT NULL DEFAULT FALSE,
    assessed_mhci   BOOLEAN NOT NULL DEFAULT FALSE,
    assessed_mhcii  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS constructs_direction_channel_idx
    ON constructs (direction, channel, status);
CREATE INDEX IF NOT EXISTS constructs_direction_rank_idx
    ON constructs (direction, status, rank);
CREATE UNIQUE INDEX IF NOT EXISTS constructs_uniq_combo
    ON constructs (direction, backbone_id, linker_id, peptide_id);

COMMIT;