-- derive_anoxpepred_branches.sql
-- 把 peptide_enrichment.tool='anoxpepred' 的 details JSON 里 frs_score / chel_score
-- 拆成两个独立 tool:'anoxpepred-frs' 和 'anoxpepred-chelating'
-- 各自一行,score 列直接存分支分,label 用 SDK 的 threshold=0.5 规则重打
--
-- 来源:基于与 Milo 的两轮核实 ——
--   - frs_score/chel_score 100% 完整(20248885 行 0 行缺失)
--   - 0.6*frs+0.4*chel 与 overall_score 严格相等(最大偏差 8e-5)
--   - SDK 内部 threshold = 0.5 硬编码
--
-- 用法:
--   PGPASSWORD=... psql -h 127.0.0.1 -U igem -d igem_peptides \
--       -v ON_ERROR_STOP=1 -f scripts/derive_anoxpepred_branches.sql
--
-- 回滚:
--   DELETE FROM peptide_enrichment WHERE tool IN ('anoxpepred-frs','anoxpepred-chelating');
--
-- 前置条件:已执行 SELECT * FROM peptide_enrichment WHERE tool='anoxpepred' 入 _bkp_anoxpepred_*
BEGIN;

-- --- 0. 防护:确认 frs/chel 字段在 details 里 100% 完整 ----------------------
DO $$
DECLARE
  n_total  bigint;
  n_frs    bigint;
  n_chel   bigint;
BEGIN
  SELECT count(*) INTO n_total
    FROM peptide_enrichment WHERE tool='anoxpepred';
  SELECT count(*) INTO n_frs
    FROM peptide_enrichment
   WHERE tool='anoxpepred' AND details ? 'frs_score';
  SELECT count(*) INTO n_chel
    FROM peptide_enrichment
   WHERE tool='anoxpepred' AND details ? 'chel_score';

  IF n_total = 0 THEN
    RAISE EXCEPTION 'no anoxpepred rows found, abort';
  END IF;
  IF n_frs <> n_total THEN
    RAISE EXCEPTION 'frs_score missing in % / % rows', n_total - n_frs, n_total;
  END IF;
  IF n_chel <> n_total THEN
    RAISE EXCEPTION 'chel_score missing in % / % rows', n_total - n_chel, n_total;
  END IF;
  RAISE NOTICE 'sanity check passed: % rows, frs=%, chel=%', n_total, n_frs, n_chel;
END $$;


-- --- 1. 派生 anoxpepred-frs ------------------------------------------------
INSERT INTO peptide_enrichment (peptide_id, tool, score, label, details, scored_at)
SELECT peptide_id,
       'anoxpepred-frs',
       (details->>'frs_score')::float,
       CASE WHEN (details->>'frs_score')::float >= 0.5
            THEN 'FRS_active' ELSE 'FRS_inactive' END,
       jsonb_build_object(
         'source_tool', 'anoxpepred',
         'branch', 'frs',
         'threshold', 0.5,
         'derived_from', 'details->>''frs_score'''
       ),
       scored_at
  FROM peptide_enrichment
 WHERE tool = 'anoxpepred'
   AND details ? 'frs_score'
ON CONFLICT (peptide_id, tool) DO UPDATE
  SET score     = EXCLUDED.score,
      label     = EXCLUDED.label,
      details   = EXCLUDED.details,
      scored_at = EXCLUDED.scored_at;


-- --- 2. 派生 anoxpepred-chelating -----------------------------------------
INSERT INTO peptide_enrichment (peptide_id, tool, score, label, details, scored_at)
SELECT peptide_id,
       'anoxpepred-chelating',
       (details->>'chel_score')::float,
       CASE WHEN (details->>'chel_score')::float >= 0.5
            THEN 'Chel_active' ELSE 'Chel_inactive' END,
       jsonb_build_object(
         'source_tool', 'anoxpepred',
         'branch', 'chelating',
         'threshold', 0.5,
         'derived_from', 'details->>''chel_score'''
       ),
       scored_at
  FROM peptide_enrichment
 WHERE tool = 'anoxpepred'
   AND details ? 'chel_score'
ON CONFLICT (peptide_id, tool) DO UPDATE
  SET score     = EXCLUDED.score,
      label     = EXCLUDED.label,
      details   = EXCLUDED.details,
      scored_at = EXCLUDED.scored_at;


-- --- 3. 不删 anoxpepred 原行(保守,可读旧代码) ---------------------------
-- 如果想完全清掉旧行,手动跑:
--   DELETE FROM peptide_enrichment WHERE tool = 'anoxpepred';
-- 派生 SQL 不自动删,避免脚本误删。

COMMIT;

-- --- 4. 校验(脚本末尾 SELECT,方便 stdout 一次看完) ---------------------
\echo '=== derived rows ==='
SELECT tool, count(*) AS rows,
       avg(score)::numeric(6,4) AS mean,
       min(score)::numeric(6,4) AS minv,
       max(score)::numeric(6,4) AS maxv,
       count(*) FILTER (WHERE label LIKE '%_active')   AS n_active,
       count(*) FILTER (WHERE label LIKE '%_inactive') AS n_inactive
  FROM peptide_enrichment
 WHERE tool IN ('anoxpepred','anoxpepred-frs','anoxpepred-chelating')
 GROUP BY tool
 ORDER BY tool;

\echo '=== null score check (期望 0) ==='
SELECT tool, count(*) FILTER (WHERE score IS NULL) AS null_scores
  FROM peptide_enrichment
 WHERE tool IN ('anoxpepred-frs','anoxpepred-chelating')
 GROUP BY tool
 ORDER BY tool;

\echo '=== 一致性:frs 派生行 score 与源 details->>frs_score 完全相等 ==='
SELECT count(*) FILTER (
         WHERE abs((e.score - ((e.details->>'frs_score')::float)) < 1e-4)
       ) AS frs_matched,
       count(*) AS frs_total
  FROM peptide_enrichment e
 WHERE e.tool = 'anoxpepred-frs';
