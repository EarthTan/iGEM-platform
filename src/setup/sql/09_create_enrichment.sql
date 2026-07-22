-- 09_create_enrichment.sql — v2 spec §6 (新表,不动 peptides / peptide_metadata)
BEGIN;

CREATE TABLE IF NOT EXISTS peptide_enrichment (
    peptide_id   BIGINT  NOT NULL REFERENCES peptides(id) ON DELETE CASCADE,
    tool         VARCHAR(32) NOT NULL,
    score        REAL,
    label        TEXT,
    details      JSONB,
    scored_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT peptide_enrichment_pk PRIMARY KEY (peptide_id, tool)
);

CREATE INDEX IF NOT EXISTS peptide_enrichment_tool_score_idx
    ON peptide_enrichment (tool, score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS peptide_enrichment_tool_idx
    ON peptide_enrichment (tool);

-- 覆盖度统计视图:每工具跑了多少 + 整体有多少该跑
CREATE OR REPLACE VIEW v_peptide_enrichment_coverage AS
SELECT
    t.tool,
    coalesce(c.done, 0)              AS done_count,
    coalesce(c.tool_eligible, 0)     AS eligible_count,
    CASE WHEN coalesce(c.tool_eligible, 0) = 0
         THEN 0
         ELSE round(100.0 * c.done / c.tool_eligible, 2)
    END                              AS coverage_pct
FROM (
    VALUES
        ('toxinpred3'::text, '1-30'::text),
        ('tipred',     '1-30'),
        ('algpred2',   '1-30'),
        ('sodope',     '1-30'),
        ('anoxpepred', '1-30'),
        ('hemopi2',    '1-40'),
        ('plm4cpps',   'length_supported'),
        ('temstapro',  'length_supported'),
        ('mhcflurry',  '5-15')
) AS t(tool, eligible_filter)
LEFT JOIN (
    SELECT tool, count(*) AS done,
           -- 工具对应的"应跑"量:统一通过 LEFT JOIN peptides 过滤
           CASE tool
               WHEN 'mhcflurry' THEN (SELECT count(*) FROM peptides WHERE length BETWEEN 5 AND 15)
               WHEN 'hemopi2'   THEN (SELECT count(*) FROM peptides WHERE length <= 40)
               ELSE                  (SELECT count(*) FROM peptides)
           END AS tool_eligible
    FROM peptide_enrichment
    GROUP BY tool
) c ON c.tool = t.tool
ORDER BY coverage_pct DESC NULLS LAST, t.tool;

COMMIT;
